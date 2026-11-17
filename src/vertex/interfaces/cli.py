"""Maintenance, backup/restore, migration, bootstrap, and server CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from vertex import __version__
from vertex.config import Settings
from vertex.identity.service import IdentityService
from vertex.infrastructure.backup import create_backup_set, restore_backup_set
from vertex.infrastructure.errors import VertexError
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.migrations import apply_migrations, list_applied_migrations
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager, configure_sqlite_connection
from vertex.interfaces.frontend_build import build_frontend
from vertex.migration.legacy_importer import import_legacy_instance


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vertex", description="Vertex self-hosted CLI")
    parser.add_argument("--data-dir", type=Path, default=None, help="Override VERTEX_DATA_DIR")
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=None,
        help="Override VERTEX_MIGRATIONS_DIR",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Apply pending system and existing user SQLite migrations")

    boot = sub.add_parser("bootstrap-admin", help="Create initial administrator account")
    boot.add_argument("--username", required=True)
    boot.add_argument("--password", required=True)

    reset_pw = sub.add_parser("reset-password", help="Reset a local account password from host CLI")
    reset_pw.add_argument("--username", required=True)
    reset_pw.add_argument("--password", required=True)

    bkp = sub.add_parser("backup", help="Create consistent whole-instance backup set with manifest")
    bkp.add_argument("--dest", type=Path, required=True)

    rst = sub.add_parser("restore", help="Verify and restore a backup set into a clean data dir")
    rst.add_argument("--backup-dir", type=Path, required=True)
    rst.add_argument("--dest", type=Path, required=True)

    imp = sub.add_parser("import-legacy", help="Import legacy Game Hub data dir into a fresh destination")
    imp.add_argument("--source-dir", type=Path, required=True)
    imp.add_argument("--dest-dir", type=Path, required=True)
    imp.add_argument("--report", type=Path, default=None)
    imp.add_argument("--legacy-key", action="append", default=[])
    imp.add_argument("--allow-legacy-fallback-key", action="store_true")
    imp.add_argument("--single-user-admin", default="admin")
    imp.add_argument("--single-user-password", default=None)

    sub.add_parser("diagnostics", help="Emit sanitized instance health and schema report (no secrets)")

    bld = sub.add_parser("build-frontend", help="Compile modular TypeScript frontend into static assets")
    bld.add_argument("--out-dir", type=Path, default=None)

    srv = sub.add_parser("serve", help="Run the Vertex HTTP server")
    srv.add_argument("--host", default=None)
    srv.add_argument("--port", type=int, default=None)

    mcp_srv = sub.add_parser("serve-mcp", help="Run the standalone Vertex Streamable HTTP MCP server")
    mcp_srv.add_argument("--host", default=None)
    mcp_srv.add_argument("--port", type=int, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    settings = Settings.from_env(data_dir=args.data_dir, migrations_dir=args.migrations_dir)
    db_manager = DatabaseManager(settings)
    secret_box = SecretBox(settings.secret_key, settings.previous_secret_keys)

    try:
        if args.command == "migrate":
            sys_applied = db_manager.ensure_system_ready()
            user_applied: dict[str, list[str]] = {}
            with db_manager.system_connection() as conn:
                rows = conn.execute("SELECT id, username, role, status, timezone, timezone_eras_json FROM users").fetchall()
            from vertex.activity.service import rebuild_daily_ledger_for_conn

            for r in rows:
                lib_path = db_manager.users_dir / r["id"] / "library.db"
                if lib_path.exists():
                    import sqlite3

                    uconn = configure_sqlite_connection(sqlite3.connect(str(lib_path)))
                    try:
                        applied_migs = apply_migrations(uconn, settings.migrations_dir / "library")
                        user_applied[r["id"]] = applied_migs
                        if "0004_legacy_sessions_ledger_fix" in applied_migs:
                            rebuild_daily_ledger_for_conn(
                                uconn,
                                tz_name=r["timezone"] or "UTC",
                                timezone_eras=json.loads(r["timezone_eras_json"] or "[]"),
                            )
                            uconn.commit()
                    finally:
                        uconn.close()
            print(json.dumps({"system_applied": sys_applied, "users_applied": user_applied}, indent=2))
            return 0

        if args.command == "bootstrap-admin":
            db_manager.ensure_system_ready()
            ident = IdentityService(db_manager, secret_box, JobQueue(db_manager))
            account, _ = ident.bootstrap_admin(
                username=args.username,
                password=args.password,
                user_agent="cli-bootstrap",
            )
            print(json.dumps({"created_admin": asdict(account)}, indent=2))
            return 0

        if args.command == "reset-password":
            db_manager.ensure_system_ready()
            ident = IdentityService(db_manager, secret_box, JobQueue(db_manager))
            account = ident.cli_reset_password(args.username, args.password)
            print(json.dumps({"password_reset_for": account.username, "user_id": account.id}, indent=2))
            return 0

        if args.command == "backup":
            manifest = create_backup_set(db_manager, args.dest, secret_box=secret_box)
            print(json.dumps(manifest.to_dict(), indent=2))
            return 0

        if args.command == "restore":
            manifest = restore_backup_set(args.backup_dir, args.dest, settings.migrations_dir)
            print(json.dumps({"restored_to": str(args.dest), "users": len(manifest.users)}, indent=2))
            return 0

        if args.command == "import-legacy":
            if not settings.secret_key:
                raise VertexError(
                    "Set VERTEX_SECRET_KEY (or GAMEHUB_SECRET_KEY) in environment before running import-legacy"
                )
            report = import_legacy_instance(
                source_dir=args.source_dir,
                dest_dir=args.dest_dir,
                migrations_dir=settings.migrations_dir,
                dest_secret_key=settings.secret_key,
                legacy_source_keys=list(args.legacy_key) + list(settings.previous_secret_keys),
                allow_legacy_fallback_key=args.allow_legacy_fallback_key,
                single_user_admin_username=args.single_user_admin,
                single_user_admin_password=args.single_user_password,
            )
            payload = json.dumps(report.to_dict(), indent=2)
            if args.report is not None:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(payload, encoding="utf-8")
            print(payload)
            return 0

        if args.command == "diagnostics":
            db_manager.ensure_system_ready()
            with db_manager.system_connection() as conn:
                sys_migs = list_applied_migrations(conn)
                user_cnt = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
                active_cnt = int(conn.execute("SELECT COUNT(*) FROM users WHERE status='active'").fetchone()[0])
                jobs_summary = {
                    r["state"]: int(r["cnt"])
                    for r in conn.execute("SELECT state, COUNT(*) AS cnt FROM jobs GROUP BY state").fetchall()
                }
            diag = {
                "app_version": __version__,
                "data_dir": str(settings.data_dir),
                "bind_host": settings.bind_host,
                "port": settings.port,
                "secret_key_configured": secret_box.is_configured,
                "secret_key_fingerprint": secret_box.key_fingerprint(),
                "outbound_sync_enabled": settings.outbound_sync_enabled,
                "system_migrations": [m["version"] for m in sys_migs],
                "users_total": user_cnt,
                "users_active": active_cnt,
                "jobs_by_state": jobs_summary,
            }
            print(json.dumps(diag, indent=2))
            return 0

        if args.command == "build-frontend":
            out_dir = args.out_dir or settings.frontend_dist_dir
            emitted = build_frontend(out_dir=out_dir)
            print(json.dumps({"built_modules": len(emitted), "out_dir": str(out_dir)}, indent=2))
            return 0

        if args.command == "serve":
            import uvicorn

            from vertex.main import create_app

            web_app = create_app(settings)
            web_host = args.host or settings.bind_host
            web_port = args.port or settings.port
            standalone_mcp = os.environ.get("VERTEX_MCP_STANDALONE_ENABLED", "").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            if not standalone_mcp:
                uvicorn.run(web_app, host=web_host, port=web_port)
                return 0

            from vertex.interfaces.mcp.server import create_mcp_app

            mcp_app = create_mcp_app(settings, services=web_app.state.services)

            async def _serve_dual() -> None:
                web_server = uvicorn.Server(
                    uvicorn.Config(web_app, host=web_host, port=web_port, log_level="info")
                )
                mcp_server = uvicorn.Server(
                    uvicorn.Config(
                        mcp_app,
                        host=settings.mcp_bind_host,
                        port=settings.mcp_port,
                        log_level="info",
                    )
                )
                await asyncio.gather(web_server.serve(), mcp_server.serve())

            asyncio.run(_serve_dual())
            return 0

        if args.command == "serve-mcp":
            import uvicorn

            from vertex.interfaces.mcp.server import create_mcp_app

            uvicorn.run(
                create_mcp_app(settings),
                host=args.host or settings.mcp_bind_host,
                port=args.port or settings.mcp_port,
            )
            return 0

    except VertexError as exc:
        print(json.dumps({"error": exc.code, "detail": str(exc)}), file=sys.stderr)
        return 1

    return 1
