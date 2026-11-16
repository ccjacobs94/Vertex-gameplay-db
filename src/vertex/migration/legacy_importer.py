"""Non-destructive, staging-atomic legacy SQLite -> Vertex rebuild importer."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cryptography.fernet import InvalidToken

from vertex import __version__
from vertex.activity.service import rebuild_daily_ledger_for_conn
from vertex.config import Settings
from vertex.identity.passwords import hash_password
from vertex.infrastructure.errors import MigrationError, MissingSecretKeyError, ScopeViolationError
from vertex.infrastructure.migrations import apply_migrations
from vertex.infrastructure.scope import UserScope, validate_user_id
from vertex.infrastructure.secrets import SecretBox, legacy_fernet_from_secret
from vertex.infrastructure.sqlite import configure_sqlite_connection
from vertex.library.models import VALID_OWNERSHIP_STATUSES
from vertex.library.service import _OWNERSHIP_SYNONYMS

_RETIRED_SETTINGS = frozenset(
    {
        "signup_mode",
        "vapid_private_key",
        "vapid_public_key",
        "edition",
        "registration_policy",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot_sqlite_to(src_path: Path, dst_path: Path) -> None:
    src = sqlite3.connect(str(src_path), timeout=15.0)
    dst = sqlite3.connect(str(dst_path), timeout=15.0)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()


def _parse_json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                return loaded
        except Exception:
            pass
    return []


def _decrypt_legacy_secret(
    raw_value: str | None,
    candidate_ciphers: list[Any],
) -> tuple[str | None, bool]:
    """Return (plaintext, decrypt_failed)."""
    if not raw_value:
        return None, False
    val = str(raw_value).strip()
    if not val:
        return None, False
    if not val.startswith("gAAAAA"):
        # Legacy plaintext stored before encryption migration.
        return val, False
    for cipher in candidate_ciphers:
        try:
            return cipher.decrypt(val.encode("ascii")).decode("utf-8"), False
        except (InvalidToken, ValueError, Exception):
            continue
    return None, True


@dataclass(frozen=True, slots=True)
class LegacyInventory:
    layout: str  # "multi_user" | "single_user"
    source_dir: str
    users: list[dict[str, Any]]
    orphaned_directories: list[str]
    conflicts: list[str]
    legacy_sessions_to_revoke: int
    legacy_oauth_tokens_to_revoke: int


@dataclass(frozen=True, slots=True)
class MigrationReport:
    format_version: int
    app_version: str
    created_at: str
    layout: str
    source_dir: str
    dest_dir: str
    users_migrated: list[dict[str, Any]]
    orphaned_directories: list[str]
    conflicts: list[str]
    credentials_needing_reentry: list[dict[str, str]]
    revoked_web_sessions: int
    revoked_oauth_tokens: int
    skipped_or_transformed_records: list[dict[str, Any]]
    catalog_entries_migrated: int
    parity_checks: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "app_version": self.app_version,
            "created_at": self.created_at,
            "layout": self.layout,
            "source_dir": self.source_dir,
            "dest_dir": self.dest_dir,
            "users_migrated": self.users_migrated,
            "orphaned_directories": self.orphaned_directories,
            "conflicts": self.conflicts,
            "credentials_needing_reentry": self.credentials_needing_reentry,
            "revoked_web_sessions": self.revoked_web_sessions,
            "revoked_oauth_tokens": self.revoked_oauth_tokens,
            "skipped_or_transformed_records": self.skipped_or_transformed_records,
            "catalog_entries_migrated": self.catalog_entries_migrated,
            "parity_checks": self.parity_checks,
        }


def inventory_legacy_source(source_dir: Path) -> LegacyInventory:
    """Inspect a legacy Game Hub data directory without modifying source files."""
    source_dir = Path(source_dir)
    if not source_dir.exists() or not source_dir.is_dir():
        raise MigrationError(f"Legacy source directory not found: {source_dir}")

    sys_db = source_dir / "system.db"
    single_db = source_dir / "games.db"
    users_root = source_dir / "users"

    if not sys_db.exists():
        if not single_db.exists():
            raise MigrationError(
                f"Neither system.db nor games.db found in legacy source directory: {source_dir}"
            )
        return LegacyInventory(
            layout="single_user",
            source_dir=str(source_dir),
            users=[
                {
                    "id": "admin",
                    "username": "admin",
                    "role": "admin",
                    "status": "active",
                    "status_reason": None,
                    "source_db": str(single_db),
                }
            ],
            orphaned_directories=[],
            conflicts=[],
            legacy_sessions_to_revoke=0,
            legacy_oauth_tokens_to_revoke=0,
        )

    with tempfile.TemporaryDirectory(prefix="vertex-inv-") as tmp:
        snap_sys = Path(tmp) / "system.db"
        _snapshot_sqlite_to(sys_db, snap_sys)
        conn = sqlite3.connect(str(snap_sys))
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "users" not in tables:
                raise MigrationError("Legacy system.db is missing the 'users' table")

            user_rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC, id ASC").fetchall()
            matched_dirs: set[str] = set()
            conflicts: list[str] = []
            users_list: list[dict[str, Any]] = []

            for u in user_rows:
                ud = dict(u)
                uid = ud["id"]
                uname = ud["username"]
                candidate_names = []
                for cand in (uid, uname, uname.lower()):
                    if cand and cand not in candidate_names:
                        candidate_names.append(cand)

                found_paths: list[tuple[str, Path]] = []
                for cname in candidate_names:
                    p = users_root / cname / "games.db"
                    if p.exists():
                        found_paths.append((cname, p))

                selected_db: str | None = None
                if len(found_paths) > 1:
                    conflicts.append(
                        f"User '{uid}' matches multiple library directories: "
                        f"{[c for c, _ in found_paths]}"
                    )
                    selected_db = str(found_paths[0][1])
                    matched_dirs.add(found_paths[0][0])
                elif len(found_paths) == 1:
                    selected_db = str(found_paths[0][1])
                    matched_dirs.add(found_paths[0][0])

                users_list.append(
                    {
                        "id": uid,
                        "username": uname,
                        "role": ud.get("role") or "user",
                        "status": ud.get("status") or "active",
                        "status_reason": ud.get("status_reason"),
                        "source_db": selected_db,
                    }
                )

            orphans: list[str] = []
            if users_root.exists() and users_root.is_dir():
                for child in sorted(users_root.iterdir()):
                    if child.is_dir() and child.name not in matched_dirs:
                        orphans.append(child.name)

            sess_count = 0
            if "web_sessions" in tables:
                sess_count = int(conn.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0])
            oauth_count = 0
            if "oauth_tokens" in tables:
                oauth_count = int(conn.execute("SELECT COUNT(*) FROM oauth_tokens").fetchone()[0])

            return LegacyInventory(
                layout="multi_user",
                source_dir=str(source_dir),
                users=users_list,
                orphaned_directories=orphans,
                conflicts=conflicts,
                legacy_sessions_to_revoke=sess_count,
                legacy_oauth_tokens_to_revoke=oauth_count,
            )
        finally:
            conn.close()


def _translate_lifecycle_status(
    legacy_status: str,
    legacy_reason: str | None,
    subscription_status: str | None,
) -> tuple[str, str | None, bool]:
    """Map legacy user status/reason safely: never silently activate pending/lapsed accounts."""
    st = (legacy_status or "active").strip().lower()
    reason = (legacy_reason or "").strip() or None
    reason_low = (reason or "").lower()

    if st == "active":
        return "active", reason, False
    if st == "admin_suspended" or (st == "inactive" and reason_low == "admin_suspended"):
        return "suspended", reason or "admin_suspended", False

    retired_tags = ("awaiting_approval", "awaiting_payment", "subscription_lapsed")
    if reason_low in retired_tags or st in (*retired_tags, "pending", "inactive"):
        tag = reason_low if reason_low in retired_tags else st
        detail = f"migrated_from:{tag}"
        if subscription_status:
            detail = f"{detail} (legacy_subscription={subscription_status})"
        if reason and reason_low != tag:
            detail = f"{detail}; {reason}"
        return "suspended", detail, True

    return "suspended", f"migrated_unknown_status:{st}", True


def import_legacy_instance(
    *,
    source_dir: Path,
    dest_dir: Path,
    migrations_dir: Path,
    dest_secret_key: str,
    legacy_source_keys: list[str] | tuple[str, ...] = (),
    allow_legacy_fallback_key: bool = False,
    single_user_admin_username: str = "admin",
    single_user_admin_password: str | None = None,
    _fail_after_user_hook: Callable[[str], None] | None = None,
) -> MigrationReport:
    """Import a legacy single-user or multi-user directory atomically into a fresh dest_dir."""
    source_dir = Path(source_dir)
    dest_dir = Path(dest_dir)
    migrations_dir = Path(migrations_dir)

    if dest_dir.exists() and any(dest_dir.iterdir()):
        raise MigrationError(
            f"Destination directory must be empty before migration: {dest_dir}"
        )

    dest_secret_box = SecretBox(dest_secret_key)
    legacy_ciphers = [legacy_fernet_from_secret(k) for k in legacy_source_keys if k and k.strip()]
    if allow_legacy_fallback_key:
        legacy_ciphers.append(legacy_fernet_from_secret("gamehub-envelope-key-fallback"))

    inv = inventory_legacy_source(source_dir)
    now_iso = _utc_now_iso()

    credentials_reentry: list[dict[str, str]] = []
    skipped_transformed: list[dict[str, Any]] = []
    users_migrated_report: list[dict[str, Any]] = []
    parity_checks: dict[str, Any] = {"users": {}, "all_passed": True}
    catalog_count = 0

    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vertex-migrate-stage-", dir=str(dest_dir.parent)) as tmp:
        tmp_path = Path(tmp)
        stage_data = tmp_path / "data"
        stage_snap = tmp_path / "source_snap"
        stage_data.mkdir(parents=True, exist_ok=True)
        (stage_data / "users").mkdir(parents=True, exist_ok=True)
        stage_snap.mkdir(parents=True, exist_ok=True)

        dest_settings = Settings(
            data_dir=stage_data,
            migrations_dir=migrations_dir,
            frontend_dist_dir=stage_data / "unused-frontend",
            secret_key=dest_secret_key,
            worker_enabled=False,
        )

        # Initialize fresh destination system.db
        dest_sys_conn = configure_sqlite_connection(sqlite3.connect(str(dest_settings.system_db_path)))
        try:
            apply_migrations(dest_sys_conn, migrations_dir / "system")

            if inv.layout == "single_user":
                if not single_user_admin_password:
                    raise MigrationError(
                        "Single-user legacy migration requires --single-user-password to create the local admin account"
                    )
                admin_name = single_user_admin_username.strip() or "admin"
                admin_id = validate_user_id(admin_name.lower())
                dest_sys_conn.execute(
                    """
                    INSERT INTO users (
                        id, username, role, status, status_reason, password_hash,
                        timezone, timezone_eras_json, onboarding_completed, created_at, updated_at
                    ) VALUES (?, ?, 'admin', 'active', NULL, ?, 'UTC', '[]', 1, ?, ?)
                    """,
                    (admin_id, admin_name, hash_password(single_user_admin_password), now_iso, now_iso),
                )
                dest_sys_conn.execute(
                    "INSERT INTO user_connections (user_id, updated_at) VALUES (?, ?)",
                    (admin_id, now_iso),
                )
                dest_sys_conn.commit()

                lib_src_snap = stage_snap / "single_games.db"
                _snapshot_sqlite_to(source_dir / "games.db", lib_src_snap)
                user_report, parity = _migrate_user_library(
                    src_db_path=lib_src_snap,
                    dest_data_dir=stage_data,
                    migrations_dir=migrations_dir,
                    target_scope=UserScope(
                        user_id=admin_id,
                        username=admin_name,
                        role="admin",
                        status="active",
                    ),
                    tz_name="UTC",
                    timezone_eras=[],
                    skipped_log=skipped_transformed,
                )
                users_migrated_report.append(
                    {
                        "source_user_id": None,
                        "new_user_id": admin_id,
                        "username": admin_name,
                        "role": "admin",
                        "source_status": "single_user_legacy",
                        "new_status": "active",
                        "status_reason": None,
                        **user_report,
                    }
                )
                parity_checks["users"][admin_id] = parity
                if not parity["passed"]:
                    parity_checks["all_passed"] = False

            else:
                src_sys_snap = stage_snap / "system.db"
                _snapshot_sqlite_to(source_dir / "system.db", src_sys_snap)
                src_sys = sqlite3.connect(str(src_sys_snap))
                src_sys.row_factory = sqlite3.Row
                try:
                    sys_tables = {
                        r["name"]
                        for r in src_sys.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                    }
                    subs_by_user: dict[str, dict[str, Any]] = {}
                    if "subscriptions" in sys_tables:
                        for sr in src_sys.execute("SELECT * FROM subscriptions").fetchall():
                            subs_by_user[sr["user_id"]] = dict(sr)
                            skipped_transformed.append(
                                {
                                    "table": "subscriptions",
                                    "user_id": sr["user_id"],
                                    "action": "retired_archived_in_report",
                                    "legacy_status": sr["status"],
                                }
                            )
                    if "billing_events_seen" in sys_tables:
                        cnt = int(src_sys.execute("SELECT COUNT(*) FROM billing_events_seen").fetchone()[0])
                        if cnt:
                            skipped_transformed.append(
                                {
                                    "table": "billing_events_seen",
                                    "count": cnt,
                                    "action": "retired_skipped",
                                }
                            )
                    if inv.legacy_sessions_to_revoke:
                        skipped_transformed.append(
                            {
                                "table": "web_sessions",
                                "count": inv.legacy_sessions_to_revoke,
                                "action": "revoked_reauth_required",
                            }
                        )
                    if inv.legacy_oauth_tokens_to_revoke:
                        skipped_transformed.append(
                            {
                                "table": "oauth_tokens",
                                "count": inv.legacy_oauth_tokens_to_revoke,
                                "action": "revoked_reauth_required",
                            }
                        )

                    # Migrate catalog_cache
                    if "catalog_cache" in sys_tables:
                        for cr in src_sys.execute("SELECT * FROM catalog_cache").fetchall():
                            cd = dict(cr)
                            dest_sys_conn.execute(
                                """
                                INSERT OR IGNORE INTO catalog_cache (
                                    normalized_title, title, steam_appid, genres_json, tags_json,
                                    release_year, critic_score, cover_url, source, retrieved_at, updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'legacy_import', ?, ?)
                                """,
                                (
                                    cd["normalized_title"],
                                    cd["normalized_title"],
                                    cd.get("steam_appid"),
                                    cd.get("genres_json") or "[]",
                                    "[]",
                                    cd.get("release_year"),
                                    cd.get("critic_score"),
                                    cd.get("cover_url"),
                                    cd.get("updated_at") or now_iso,
                                    cd.get("updated_at") or now_iso,
                                ),
                            )
                            catalog_count += 1

                    # Migrate safe instance_settings
                    if "instance_settings" in sys_tables:
                        for ir in src_sys.execute("SELECT * FROM instance_settings").fetchall():
                            key_name = str(ir["key"] or "")
                            if key_name in _RETIRED_SETTINGS or key_name.startswith("stripe_"):
                                skipped_transformed.append(
                                    {
                                        "table": "instance_settings",
                                        "key": key_name,
                                        "action": "retired_skipped",
                                    }
                                )
                            else:
                                dest_sys_conn.execute(
                                    "INSERT OR REPLACE INTO instance_settings (key, value, updated_at) VALUES (?, ?, ?)",
                                    (key_name, ir["value"], ir["updated_at"]),
                                )

                    # Migrate users and connections
                    user_rows = src_sys.execute("SELECT * FROM users ORDER BY created_at ASC, id ASC").fetchall()
                    inv_by_id = {u["id"]: u for u in inv.users}
                    old_to_new_user_id: dict[str, str] = {}

                    for u in user_rows:
                        ud = dict(u)
                        old_id = ud["id"]
                        try:
                            new_id = validate_user_id((old_id or ud["username"]).lower())
                        except ScopeViolationError as exc:
                            raise MigrationError(f"Invalid legacy user id '{old_id}': {exc}") from exc
                        old_to_new_user_id[str(old_id)] = new_id
                        old_to_new_user_id[str(ud["username"]).lower()] = new_id
                        new_role = "admin" if ud.get("role") == "admin" else "member"
                        sub_info = subs_by_user.get(old_id)
                        new_status, new_reason, transformed = _translate_lifecycle_status(
                            ud.get("status") or "active",
                            ud.get("status_reason"),
                            sub_info.get("status") if sub_info else None,
                        )
                        if transformed:
                            skipped_transformed.append(
                                {
                                    "table": "users",
                                    "user_id": old_id,
                                    "action": f"status_{ud.get('status')}_to_{new_status}",
                                    "reason": new_reason,
                                }
                            )

                        tz_name = ud.get("timezone") or "auto"
                        tz_eras = _parse_json_list(ud.get("timezone_eras"))
                        pw_hash = ud.get("password_hash") or ""
                        if not pw_hash.startswith("scrypt$"):
                            pw_hash = hash_password(f"disabled-{new_id}-{now_iso}")
                            skipped_transformed.append(
                                {
                                    "table": "users",
                                    "user_id": old_id,
                                    "action": "non_scrypt_password_locked",
                                }
                            )

                        dest_sys_conn.execute(
                            """
                            INSERT INTO users (
                                id, username, role, status, status_reason, password_hash,
                                timezone, timezone_eras_json, onboarding_completed, created_at,
                                updated_at, last_login_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                new_id,
                                ud["username"],
                                new_role,
                                new_status,
                                new_reason,
                                pw_hash,
                                tz_name,
                                json.dumps(tz_eras),
                                0,
                                ud.get("created_at") or now_iso,
                                ud.get("updated_at") or now_iso,
                                ud.get("last_login_at"),
                            ),
                        )

                        # Connections & encrypted credentials migration
                        if "user_connections" in sys_tables:
                            uc = src_sys.execute(
                                "SELECT * FROM user_connections WHERE user_id = ?",
                                (old_id,),
                            ).fetchone()
                        else:
                            uc = None

                        if uc is not None:
                            ucd = dict(uc)
                            if ucd.get("onboarding_completed"):
                                dest_sys_conn.execute(
                                    "UPDATE users SET onboarding_completed = 1 WHERE id = ?",
                                    (new_id,),
                                )

                            reencrypted: dict[str, str | None] = {}
                            for field_name in (
                                "xbl_api_key_encrypted",
                                "xbl_watcher_api_key_encrypted",
                                "psn_npsso_encrypted",
                                "ha_token_encrypted",
                            ):
                                raw_val = ucd.get(field_name)
                                if not raw_val:
                                    reencrypted[field_name] = None
                                    continue
                                plain, failed = _decrypt_legacy_secret(raw_val, legacy_ciphers)
                                if failed or plain is None:
                                    reencrypted[field_name] = None
                                    credentials_reentry.append(
                                        {
                                            "user_id": new_id,
                                            "field": field_name,
                                            "reason": "decryption_failed_source_key_mismatch",
                                        }
                                    )
                                else:
                                    try:
                                        reencrypted[field_name] = dest_secret_box.encrypt(plain)
                                    except MissingSecretKeyError as exc:
                                        raise MigrationError(str(exc)) from exc

                            dest_sys_conn.execute(
                                """
                                INSERT INTO user_connections (
                                    user_id, steam_id, xbl_api_key_encrypted, xbl_watcher_api_key_encrypted,
                                    xbox_gamertag, xbox_xuid, psn_npsso_encrypted,
                                    ha_url, ha_token_encrypted, ha_entity_id, updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    new_id,
                                    ucd.get("steam_id"),
                                    reencrypted["xbl_api_key_encrypted"],
                                    reencrypted["xbl_watcher_api_key_encrypted"],
                                    ucd.get("xbox_gamertag"),
                                    ucd.get("xbox_xuid"),
                                    reencrypted["psn_npsso_encrypted"],
                                    ucd.get("ha_url"),
                                    reencrypted["ha_token_encrypted"],
                                    ucd.get("ha_entity_id"),
                                    ucd.get("updated_at") or now_iso,
                                ),
                            )
                        else:
                            dest_sys_conn.execute(
                                "INSERT INTO user_connections (user_id, updated_at) VALUES (?, ?)",
                                (new_id, now_iso),
                            )

                        # Invites & events handled below; now migrate this user's games.db
                        src_lib_path = (inv_by_id.get(old_id) or {}).get("source_db")
                        scope = UserScope(
                            user_id=new_id,
                            username=ud["username"],
                            role=new_role,
                            status=new_status,
                        )
                        if src_lib_path and Path(src_lib_path).exists():
                            snap_lib = stage_snap / f"user_{new_id}.db"
                            _snapshot_sqlite_to(Path(src_lib_path), snap_lib)
                            lib_rep, parity = _migrate_user_library(
                                src_db_path=snap_lib,
                                dest_data_dir=stage_data,
                                migrations_dir=migrations_dir,
                                target_scope=scope,
                                tz_name=tz_name,
                                timezone_eras=tz_eras,
                                skipped_log=skipped_transformed,
                            )
                        else:
                            # Provision clean empty library even for users without legacy games.db
                            lib_path = stage_data / "users" / new_id / "library.db"
                            lib_path.parent.mkdir(parents=True, exist_ok=True)
                            empty_conn = configure_sqlite_connection(sqlite3.connect(str(lib_path)))
                            try:
                                apply_migrations(empty_conn, migrations_dir / "library")
                            finally:
                                empty_conn.close()
                            lib_rep = {"games_migrated": 0, "sessions_migrated": 0}
                            parity = {"passed": True, "source_games": 0, "dest_games": 0}

                        users_migrated_report.append(
                            {
                                "source_user_id": old_id,
                                "new_user_id": new_id,
                                "username": ud["username"],
                                "role": new_role,
                                "source_status": ud.get("status") or "active",
                                "new_status": new_status,
                                "status_reason": new_reason,
                                **lib_rep,
                            }
                        )
                        parity_checks["users"][new_id] = parity
                        if not parity["passed"]:
                            parity_checks["all_passed"] = False

                        if _fail_after_user_hook is not None:
                            _fail_after_user_hook(old_id)

                    # Migrate invites
                    if "invites" in sys_tables:
                        for ir in src_sys.execute("SELECT * FROM invites").fetchall():
                            idict = dict(ir)
                            raw_creator = str(idict["created_by"] or "")
                            mapped_creator = old_to_new_user_id.get(raw_creator) or old_to_new_user_id.get(
                                raw_creator.lower(), raw_creator
                            )
                            raw_used_by = idict.get("used_by")
                            mapped_used_by = (
                                old_to_new_user_id.get(str(raw_used_by))
                                or old_to_new_user_id.get(str(raw_used_by).lower(), str(raw_used_by).lower())
                                if raw_used_by
                                else None
                            )
                            dest_sys_conn.execute(
                                """
                                INSERT OR IGNORE INTO invites (
                                    code_hash, created_by, role, note, created_at, expires_at, used_by, used_at
                                ) VALUES (?, ?, 'member', ?, ?, ?, ?, ?)
                                """,
                                (
                                    idict["code_hash"],
                                    mapped_creator,
                                    idict.get("note") or "",
                                    idict["created_at"],
                                    idict.get("expires_at"),
                                    mapped_used_by,
                                    idict.get("used_at"),
                                ),
                            )
                finally:
                    src_sys.close()

            report = MigrationReport(
                format_version=1,
                app_version=__version__,
                created_at=now_iso,
                layout=inv.layout,
                source_dir=str(source_dir),
                dest_dir=str(dest_dir),
                users_migrated=users_migrated_report,
                orphaned_directories=inv.orphaned_directories,
                conflicts=inv.conflicts,
                credentials_needing_reentry=credentials_reentry,
                revoked_web_sessions=inv.legacy_sessions_to_revoke,
                revoked_oauth_tokens=inv.legacy_oauth_tokens_to_revoke,
                skipped_or_transformed_records=skipped_transformed,
                catalog_entries_migrated=catalog_count,
                parity_checks=parity_checks,
            )

            dest_sys_conn.execute(
                """
                INSERT INTO migration_imports (import_id, source_path, status, summary_json, created_at, updated_at)
                VALUES (?, ?, 'completed', ?, ?, ?)
                """,
                ("legacy_import_v1", str(source_dir), json.dumps(report.to_dict()), now_iso, now_iso),
            )
            dest_sys_conn.commit()
        finally:
            dest_sys_conn.close()

        # Atomically promote staging destination only after full verification succeeds.
        if dest_dir.exists():
            try:
                dest_dir.rmdir()
                shutil.move(str(stage_data), str(dest_dir))
            except OSError:
                # Destination is an empty mount point (e.g. Docker volume); move staged children inside.
                for item in stage_data.iterdir():
                    shutil.move(str(item), str(dest_dir / item.name))
        else:
            shutil.move(str(stage_data), str(dest_dir))
        return report


def _migrate_user_library(
    *,
    src_db_path: Path,
    dest_data_dir: Path,
    migrations_dir: Path,
    target_scope: UserScope,
    tz_name: str,
    timezone_eras: list[dict[str, str]],
    skipped_log: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    now_iso = _utc_now_iso()
    dest_lib_path = dest_data_dir / "users" / target_scope.user_id / "library.db"
    dest_lib_path.parent.mkdir(parents=True, exist_ok=True)

    src = sqlite3.connect(str(src_db_path))
    src.row_factory = sqlite3.Row
    dst = configure_sqlite_connection(sqlite3.connect(str(dest_lib_path)))

    try:
        apply_migrations(dst, migrations_dir / "library")
        src_tables = {
            r["name"]
            for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "games" not in src_tables:
            raise MigrationError(f"Legacy library DB {src_db_path} has no 'games' table")

        game_id_map: dict[int, int] = {}
        canon_id_map: dict[int, int] = {}
        src_games = src.execute("SELECT * FROM games ORDER BY id ASC").fetchall()

        for g in src_games:
            gd = dict(g)
            old_gid = int(gd["id"])
            old_canon = gd.get("canonical_id")
            raw_own = str(gd.get("ownership_status") or "Owned").strip()
            if raw_own in VALID_OWNERSHIP_STATUSES:
                own, forced_gp = raw_own, False
            else:
                own, forced_gp = _OWNERSHIP_SYNONYMS.get(raw_own.lower(), ("Owned", False))
            gp = bool(gd.get("is_game_pass")) or forced_gp
            mins = int(gd.get("playtime_minutes") or round(float(gd.get("playtime_hours") or 0.0) * 60))
            last_synced = gd.get("last_synced_playtime_minutes")
            if last_synced is None:
                last_synced = mins

            cur = dst.execute(
                """
                INSERT INTO games (
                    external_id, title, platform, source, playtime_hours, playtime_minutes,
                    last_synced_playtime_minutes, last_played, completion_status, status_locked,
                    ownership_status, ownership_locked, is_game_pass, is_play_anywhere,
                    achievements_unlocked, achievements_total, achievements_synced_at,
                    genres_json, tags_json, critic_score, user_rating, release_year, cover_url,
                    notes, favorite, playtime_prompt_muted, legacy_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gd["external_id"],
                    gd["title"],
                    gd["platform"],
                    gd["source"],
                    round(mins / 60.0, 2),
                    mins,
                    last_synced,
                    gd.get("last_played"),
                    gd.get("completion_status") or "Not Played",
                    int(bool(gd.get("status_locked"))),
                    own,
                    int(bool(gd.get("ownership_locked"))),
                    int(gp),
                    int(bool(gd.get("is_play_anywhere"))),
                    int(gd.get("achievements_unlocked") or 0),
                    int(gd.get("achievements_total") or 0),
                    gd.get("achievements_synced_at"),
                    json.dumps(_parse_json_list(gd.get("genres"))),
                    json.dumps(_parse_json_list(gd.get("tags"))),
                    gd.get("critic_score"),
                    gd.get("user_rating"),
                    gd.get("release_year"),
                    gd.get("cover_url"),
                    gd.get("notes") or "",
                    int(bool(gd.get("favorite"))),
                    int(bool(gd.get("playtime_prompt_muted"))),
                    old_gid,
                    gd.get("created_at") or now_iso,
                    gd.get("updated_at") or now_iso,
                ),
            )
            new_gid = int(cur.lastrowid)
            game_id_map[old_gid] = new_gid
            if old_canon is not None:
                mapped_canon = canon_id_map.setdefault(int(old_canon), new_gid)
                dst.execute("UPDATE games SET canonical_id = ? WHERE id = ?", (mapped_canon, new_gid))

            dst.execute(
                """
                INSERT INTO platform_copies (
                    game_id, platform, source, external_id, ownership_status, ownership_locked,
                    is_game_pass, is_play_anywhere, playtime_minutes, last_played, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_gid,
                    gd["platform"],
                    gd["source"],
                    gd["external_id"],
                    own,
                    int(bool(gd.get("ownership_locked"))),
                    int(gp),
                    int(bool(gd.get("is_play_anywhere"))),
                    mins,
                    gd.get("last_played"),
                    gd.get("created_at") or now_iso,
                    gd.get("updated_at") or now_iso,
                ),
            )
            dst.execute(
                """
                INSERT INTO provider_baselines (
                    game_id, source, baseline_minutes, last_observed_at, reset_count, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?)
                """,
                (new_gid, gd["source"], max(0, int(last_synced or 0)), gd.get("updated_at") or now_iso, now_iso),
            )

        if "reconcile_dismissals" in src_tables:
            for d in src.execute("SELECT * FROM reconcile_dismissals").fetchall():
                parts = [p for p in str(d["pair_key"]).split("|") if p]
                remapped_parts = []
                ok = True
                for p in parts:
                    try:
                        mapped = game_id_map.get(int(p))
                        if mapped is None:
                            ok = False
                            break
                        remapped_parts.append(str(mapped))
                    except ValueError:
                        remapped_parts.append(p)
                if ok and remapped_parts:
                    new_pair = "|".join(sorted(remapped_parts, key=lambda x: (len(x), x)))
                    dst.execute(
                        "INSERT OR IGNORE INTO reconciliation_dismissals (pair_key, created_at) VALUES (?, ?)",
                        (new_pair, d["created_at"]),
                    )

        if "game_achievements" in src_tables:
            for a in src.execute("SELECT * FROM game_achievements").fetchall():
                ad = dict(a)
                new_gid = game_id_map.get(int(ad["game_id"]))
                if new_gid is None:
                    continue
                dst.execute(
                    """
                    INSERT OR IGNORE INTO game_achievements (
                        game_id, external_ach_id, source, platform, name, description,
                        icon_url, icon_gray_url, unlocked, unlock_time, reward_type,
                        reward_value, rarity_pct, is_hidden, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_gid,
                        ad["external_ach_id"],
                        ad["source"],
                        ad["platform"],
                        ad["name"],
                        ad.get("description") or "",
                        ad.get("icon_url"),
                        ad.get("icon_gray_url"),
                        int(bool(ad.get("unlocked"))),
                        ad.get("unlock_time"),
                        ad.get("reward_type"),
                        ad.get("reward_value"),
                        ad.get("rarity_pct"),
                        int(bool(ad.get("is_hidden"))),
                        ad.get("updated_at") or now_iso,
                    ),
                )

        if "playtime_prompts" in src_tables:
            for p in src.execute("SELECT * FROM playtime_prompts").fetchall():
                pd = dict(p)
                new_gid = game_id_map.get(int(pd["game_id"]))
                if new_gid is None:
                    continue
                dst.execute(
                    """
                    INSERT OR IGNORE INTO playtime_prompts (
                        game_id, previous_hours, new_hours, delta_hours, current_status, detected_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        new_gid,
                        float(pd["previous_hours"]),
                        float(pd["new_hours"]),
                        float(pd["delta_hours"]),
                        pd["current_status"],
                        pd["detected_at"],
                    ),
                )

        sessions_migrated = 0
        if "play_sessions" in src_tables:
            for s in src.execute("SELECT * FROM play_sessions ORDER BY id ASC").fetchall():
                sd = dict(s)
                new_gid = game_id_map.get(int(sd["game_id"]))
                dur = int(sd.get("duration_minutes") or 0)
                if new_gid is None or dur <= 0:
                    skipped_log.append(
                        {"table": "play_sessions", "user_id": target_scope.user_id, "id": sd.get("id"), "action": "skipped"}
                    )
                    continue
                dst.execute(
                    """
                    INSERT INTO play_sessions (
                        game_id, platform, started_at, ended_at, duration_minutes, source,
                        precision_level, notes, achievements_unlocked, is_locked, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_gid,
                        sd["platform"],
                        sd["started_at"],
                        sd["ended_at"],
                        dur,
                        sd.get("source") or "sync",
                        "exact" if sd.get("source") in ("manual", "homeassistant", "telemetry") else "estimated",
                        sd.get("notes"),
                        int(sd.get("achievements_unlocked") or 0),
                        int(bool(sd.get("is_locked"))),
                        sd.get("created_at") or now_iso,
                        now_iso,
                    ),
                )
                sessions_migrated += 1

        # Deduplicate legacy telemetry game_sessions into observations without double-counting play_sessions
        if "game_sessions" in src_tables:
            for gs in src.execute("SELECT * FROM game_sessions ORDER BY id ASC").fetchall():
                gsd = dict(gs)
                new_gid = game_id_map.get(int(gsd["game_id"]))
                if new_gid is None:
                    continue
                mins = int(round(float(gsd.get("active_minutes") or gsd.get("wall_clock_minutes") or 0)))
                dst.execute(
                    """
                    INSERT OR IGNORE INTO play_observations (
                        idempotency_key, game_id, source, platform, observation_kind,
                        observed_at, interval_started_at, interval_ended_at, interval_minutes,
                        precision_level, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, 'interval', ?, ?, ?, ?, 'exact', ?, ?)
                    """,
                    (
                        f"legacy_gs:{gsd['id']}",
                        new_gid,
                        gsd.get("source") or "telemetry",
                        gsd["platform"],
                        gsd.get("ended_at") or gsd["last_seen_at"],
                        gsd["started_at"],
                        gsd.get("ended_at") or gsd["last_seen_at"],
                        max(0, mins),
                        json.dumps({"detection_method": gsd.get("detection_method"), "status": gsd.get("status")}),
                        now_iso,
                    ),
                )

        # Wishlist tables with remapped canonical_games -> games, editions, SKUs, price_history
        canon_game_map: dict[int, int] = {}
        if "canonical_games" in src_tables:
            for cg in src.execute("SELECT * FROM canonical_games ORDER BY id ASC").fetchall():
                cgd = dict(cg)
                mapped_lib = game_id_map.get(int(cgd["library_game_id"])) if cgd.get("library_game_id") else None
                cur = dst.execute(
                    """
                    INSERT INTO canonical_games (
                        normalized_title, title, release_year, developer, publisher,
                        cover_url, genres_json, critic_score, library_game_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cgd["normalized_title"],
                        cgd["title"],
                        cgd.get("release_year"),
                        cgd.get("developer") or "",
                        cgd.get("publisher") or "",
                        cgd.get("cover_url"),
                        json.dumps(_parse_json_list(cgd.get("genres"))),
                        cgd.get("critic_score"),
                        mapped_lib,
                        cgd["created_at"],
                        cgd["updated_at"],
                    ),
                )
                canon_game_map[int(cgd["id"])] = int(cur.lastrowid)

        edition_map: dict[int, int] = {}
        if "game_editions" in src_tables:
            for ge in src.execute("SELECT * FROM game_editions ORDER BY id ASC").fetchall():
                ged = dict(ge)
                new_cg = canon_game_map.get(int(ged["canonical_game_id"]))
                if new_cg is None:
                    continue
                cur = dst.execute(
                    """
                    INSERT INTO game_editions (
                        canonical_game_id, edition_name, edition_tier, tier_rank,
                        is_base_edition, included_dlc_notes, cover_url, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_cg,
                        ged["edition_name"],
                        ged.get("edition_tier") or "base",
                        int(ged.get("tier_rank") or 1),
                        int(bool(ged.get("is_base_edition"))),
                        ged.get("included_dlc_notes") or "",
                        ged.get("cover_url"),
                        ged["created_at"],
                        ged["updated_at"],
                    ),
                )
                edition_map[int(ged["id"])] = int(cur.lastrowid)

        sku_map: dict[int, int] = {}
        if "edition_platform_skus" in src_tables:
            for sk in src.execute("SELECT * FROM edition_platform_skus ORDER BY id ASC").fetchall():
                skd = dict(sk)
                new_ed = edition_map.get(int(skd["edition_id"]))
                if new_ed is None:
                    continue
                cur = dst.execute(
                    """
                    INSERT INTO edition_platform_skus (
                        edition_id, platform_family, platform_label, storefront, store_sku_id,
                        itad_plain, store_url, msrp_usd, current_price_usd, regular_price_usd,
                        discount_pct, historical_low_usd, is_authorized_seller, is_drm_free,
                        last_checked_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_ed,
                        skd["platform_family"],
                        skd["platform_label"],
                        skd["storefront"],
                        skd["store_sku_id"],
                        skd.get("itad_plain"),
                        skd.get("store_url") or "",
                        skd.get("msrp_usd"),
                        skd.get("current_price_usd"),
                        skd.get("regular_price_usd"),
                        int(skd.get("discount_pct") or 0),
                        skd.get("historical_low_usd"),
                        int(skd.get("is_authorized_seller", 1)),
                        int(skd.get("is_drm_free", 0)),
                        skd.get("last_checked_at"),
                        skd["created_at"],
                        skd["updated_at"],
                    ),
                )
                sku_map[int(skd["id"])] = int(cur.lastrowid)

        wishlist_map: dict[int, int] = {}
        if "wishlist_items" in src_tables:
            for wi in src.execute("SELECT * FROM wishlist_items ORDER BY id ASC").fetchall():
                wid = dict(wi)
                new_cg = canon_game_map.get(int(wid["canonical_game_id"]))
                if new_cg is None:
                    continue
                pref_ed = edition_map.get(int(wid["preferred_edition_id"])) if wid.get("preferred_edition_id") else None
                cur = dst.execute(
                    """
                    INSERT INTO wishlist_items (
                        canonical_game_id, preferred_edition_id, allow_smart_upgrade,
                        status, notes, added_via, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_cg,
                        pref_ed,
                        int(wid.get("allow_smart_upgrade", 1)),
                        wid.get("status") or "active",
                        wid.get("notes") or "",
                        wid.get("added_via") or "web",
                        wid["created_at"],
                        wid["updated_at"],
                    ),
                )
                wishlist_map[int(wid["id"])] = int(cur.lastrowid)

        if "wishlist_platform_targets" in src_tables:
            for wt in src.execute("SELECT * FROM wishlist_platform_targets ORDER BY id ASC").fetchall():
                wtd = dict(wt)
                new_wi = wishlist_map.get(int(wtd["wishlist_item_id"]))
                if new_wi is None:
                    continue
                last_ed = edition_map.get(int(wtd["last_alerted_edition_id"])) if wtd.get("last_alerted_edition_id") else None
                dst.execute(
                    """
                    INSERT INTO wishlist_platform_targets (
                        wishlist_item_id, platform_family, allowed_storefronts, target_price_usd,
                        is_active, muted_owned_conflict, last_alerted_at, last_alerted_price_usd,
                        last_alerted_edition_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_wi,
                        wtd["platform_family"],
                        wtd.get("allowed_storefronts") or "[]",
                        float(wtd["target_price_usd"]),
                        int(wtd.get("is_active", 1)),
                        int(wtd.get("muted_owned_conflict", 0)),
                        wtd.get("last_alerted_at"),
                        wtd.get("last_alerted_price_usd"),
                        last_ed,
                        wtd["created_at"],
                        wtd["updated_at"],
                    ),
                )

        if "price_history" in src_tables:
            for ph in src.execute("SELECT * FROM price_history ORDER BY id ASC").fetchall():
                phd = dict(ph)
                new_sku = sku_map.get(int(phd["sku_id"]))
                if new_sku is None:
                    continue
                dst.execute(
                    """
                    INSERT INTO price_history (
                        sku_id, storefront, price_usd, regular_price_usd,
                        discount_pct, is_all_time_low, source, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_sku,
                        phd["storefront"],
                        float(phd["price_usd"]),
                        phd.get("regular_price_usd"),
                        int(phd.get("discount_pct") or 0),
                        int(bool(phd.get("is_all_time_low"))),
                        phd.get("source") or "daily_sync",
                        phd["recorded_at"],
                    ),
                )

        rebuild_daily_ledger_for_conn(dst, tz_name=tz_name, timezone_eras=timezone_eras)
        fk_violations = dst.execute("PRAGMA foreign_key_check").fetchall()
        dst.commit()

        # Verify canonical per-user parity counts & aggregates
        src_game_count = len(src_games)
        dst_game_count = int(dst.execute("SELECT COUNT(*) FROM games").fetchone()[0])
        src_playtime_sum = sum(
            int(g["playtime_minutes"] if "playtime_minutes" in g.keys() and g["playtime_minutes"] is not None else round(float(g["playtime_hours"] or 0.0) * 60))
            for g in src_games
        )
        dst_playtime_sum = int(dst.execute("SELECT COALESCE(SUM(playtime_minutes), 0) FROM games").fetchone()[0])
        src_sess_cnt = (
            int(src.execute("SELECT COUNT(*) FROM play_sessions WHERE duration_minutes > 0").fetchone()[0])
            if "play_sessions" in src_tables
            else 0
        )
        dst_sess_cnt = int(dst.execute("SELECT COUNT(*) FROM play_sessions").fetchone()[0])
        src_wish_cnt = (
            int(src.execute("SELECT COUNT(*) FROM wishlist_items").fetchone()[0])
            if "wishlist_items" in src_tables
            else 0
        )
        dst_wish_cnt = int(dst.execute("SELECT COUNT(*) FROM wishlist_items").fetchone()[0])

        passed = (
            src_game_count == dst_game_count
            and src_playtime_sum == dst_playtime_sum
            and src_sess_cnt == dst_sess_cnt
            and src_wish_cnt == dst_wish_cnt
            and len(fk_violations) == 0
        )
        parity = {
            "passed": passed,
            "source_games": src_game_count,
            "dest_games": dst_game_count,
            "source_playtime_minutes": src_playtime_sum,
            "dest_playtime_minutes": dst_playtime_sum,
            "source_sessions": src_sess_cnt,
            "dest_sessions": dst_sess_cnt,
            "source_wishlist_items": src_wish_cnt,
            "dest_wishlist_items": dst_wish_cnt,
            "foreign_key_violations": len(fk_violations),
        }
        return (
            {"games_migrated": dst_game_count, "sessions_migrated": sessions_migrated},
            parity,
        )
    finally:
        src.close()
        dst.close()
