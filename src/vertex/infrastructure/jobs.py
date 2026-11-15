"""Durable SQLite-backed background jobs with leases, retries, throttling, and fair scheduling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from vertex.infrastructure.scope import UserScope, validate_user_id
from vertex.infrastructure.sqlite import DatabaseManager


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: int
    owner_user_id: str
    kind: str
    provider: str
    payload: dict[str, Any]
    dedup_key: str | None
    state: str
    attempts: int
    max_attempts: int
    scheduled_at: str
    available_at: str
    lease_owner: str | None
    lease_expires_at: str | None
    started_at: str | None
    finished_at: str | None
    last_error_code: str | None
    last_error: str | None
    result: dict[str, Any] | None
    created_at: str
    updated_at: str


def _row_to_job(row: Any) -> JobRecord:
    return JobRecord(
        id=int(row["id"]),
        owner_user_id=row["owner_user_id"],
        kind=row["kind"],
        provider=row["provider"],
        payload=json.loads(row["payload_json"] or "{}"),
        dedup_key=row["dedup_key"],
        state=row["state"],
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        scheduled_at=row["scheduled_at"],
        available_at=row["available_at"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        last_error_code=row["last_error_code"],
        last_error=row["last_error"],
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class JobQueue:
    """Persistent job queue stored in system.db."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db_manager = db_manager

    def enqueue(
        self,
        scope: UserScope,
        *,
        kind: str,
        provider: str = "internal",
        payload: dict[str, Any] | None = None,
        dedup_key: str | None = None,
        max_attempts: int = 3,
        delay_seconds: int = 0,
    ) -> JobRecord:
        scope.require_active()
        owner_user_id = validate_user_id(scope.user_id)
        now = _utc_now()
        now_iso = _iso(now)
        avail_iso = _iso(now + timedelta(seconds=max(0, delay_seconds)))
        payload_json = json.dumps(payload or {}, sort_keys=True)

        with self.db_manager.system_connection(immediate=True) as conn:
            if dedup_key:
                existing = conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE dedup_key = ? AND state IN ('queued', 'running')
                    LIMIT 1
                    """,
                    (dedup_key,),
                ).fetchone()
                if existing is not None:
                    return _row_to_job(existing)

            cursor = conn.execute(
                """
                INSERT INTO jobs (
                    owner_user_id, kind, provider, payload_json, dedup_key,
                    state, attempts, max_attempts, scheduled_at, available_at,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?)
                """,
                (
                    owner_user_id,
                    kind,
                    provider,
                    payload_json,
                    dedup_key,
                    max(1, max_attempts),
                    now_iso,
                    avail_iso,
                    now_iso,
                    now_iso,
                ),
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return _row_to_job(row)

    def reclaim_expired_leases(self, now: datetime | None = None) -> int:
        now_dt = now or _utc_now()
        now_iso = _iso(now_dt)
        reclaimed = 0
        with self.db_manager.system_connection(immediate=True) as conn:
            expired = conn.execute(
                """
                SELECT * FROM jobs
                WHERE state = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
                """,
                (now_iso,),
            ).fetchall()
            for row in expired:
                attempts = int(row["attempts"])
                max_attempts = int(row["max_attempts"])
                if attempts < max_attempts:
                    backoff = min(300, 5 * (2 ** max(0, attempts - 1)))
                    avail = _iso(now_dt + timedelta(seconds=backoff))
                    conn.execute(
                        """
                        UPDATE jobs
                        SET state = 'queued',
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            available_at = ?,
                            last_error_code = 'lease_expired',
                            last_error = 'Worker lease expired before completion; re-queued',
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (avail, now_iso, row["id"]),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE jobs
                        SET state = 'failed',
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            finished_at = ?,
                            last_error_code = 'lease_expired',
                            last_error = 'Worker lease expired after max attempts',
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now_iso, now_iso, row["id"]),
                    )
                reclaimed += 1
        return reclaimed

    def acquire_next_lease(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 120,
        now: datetime | None = None,
    ) -> JobRecord | None:
        now_dt = now or _utc_now()
        self.reclaim_expired_leases(now=now_dt)
        now_iso = _iso(now_dt)
        lease_until = _iso(now_dt + timedelta(seconds=max(5, lease_seconds)))

        with self.db_manager.system_connection(immediate=True) as conn:
            # Fair per-user scheduling: prefer users with fewest running/recent jobs.
            candidate = conn.execute(
                """
                SELECT j.*
                FROM jobs j
                JOIN users u ON u.id = j.owner_user_id
                LEFT JOIN provider_rate_limits prl ON prl.provider = j.provider
                WHERE j.state = 'queued'
                  AND u.status = 'active'
                  AND j.available_at <= ?
                  AND (prl.next_allowed_at IS NULL OR prl.next_allowed_at <= ?)
                ORDER BY
                  (SELECT COUNT(*) FROM jobs r WHERE r.owner_user_id = j.owner_user_id AND r.state = 'running') ASC,
                  COALESCE(
                    (SELECT MAX(started_at) FROM jobs p WHERE p.owner_user_id = j.owner_user_id),
                    ''
                  ) ASC,
                  j.available_at ASC,
                  j.id ASC
                LIMIT 1
                """,
                (now_iso, now_iso),
            ).fetchone()
            if candidate is None:
                return None

            conn.execute(
                """
                UPDATE jobs
                SET state = 'running',
                    attempts = attempts + 1,
                    lease_owner = ?,
                    lease_expires_at = ?,
                    started_at = ?,
                    updated_at = ?
                WHERE id = ? AND state = 'queued'
                """,
                (worker_id, lease_until, now_iso, now_iso, candidate["id"]),
            )
            updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (candidate["id"],)).fetchone()
            return _row_to_job(updated)

    def complete_job(
        self,
        job_id: int,
        worker_id: str,
        *,
        result: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> JobRecord | None:
        now_iso = _iso(now or _utc_now())
        with self.db_manager.system_connection(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ? AND state = 'running' AND lease_owner = ?",
                (job_id, worker_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE jobs
                SET state = 'succeeded',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    finished_at = ?,
                    last_error_code = NULL,
                    last_error = NULL,
                    result_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_iso, json.dumps(result or {}, sort_keys=True), now_iso, job_id),
            )
            return _row_to_job(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def fail_job(
        self,
        job_id: int,
        worker_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool = True,
        throttle_seconds: int = 0,
        now: datetime | None = None,
    ) -> JobRecord | None:
        now_dt = now or _utc_now()
        now_iso = _iso(now_dt)
        with self.db_manager.system_connection(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ? AND state = 'running' AND lease_owner = ?",
                (job_id, worker_id),
            ).fetchone()
            if row is None:
                return None

            attempts = int(row["attempts"])
            max_attempts = int(row["max_attempts"])
            provider = row["provider"]

            if throttle_seconds > 0:
                next_allowed = _iso(now_dt + timedelta(seconds=throttle_seconds))
                conn.execute(
                    """
                    INSERT INTO provider_rate_limits (provider, next_allowed_at, last_error_at, reason)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(provider) DO UPDATE SET
                        next_allowed_at = excluded.next_allowed_at,
                        last_error_at = excluded.last_error_at,
                        reason = excluded.reason
                    """,
                    (provider, next_allowed, now_iso, error_message[:256]),
                )

            if retryable and attempts < max_attempts:
                backoff = max(throttle_seconds, min(300, 5 * (2 ** max(0, attempts - 1))))
                avail_iso = _iso(now_dt + timedelta(seconds=backoff))
                conn.execute(
                    """
                    UPDATE jobs
                    SET state = 'queued',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        available_at = ?,
                        last_error_code = ?,
                        last_error = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (avail_iso, error_code, error_message[:500], now_iso, job_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE jobs
                    SET state = 'failed',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        finished_at = ?,
                        last_error_code = ?,
                        last_error = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso, error_code, error_message[:500], now_iso, job_id),
                )
            return _row_to_job(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def cancel_job(self, job_id: int, reason: str) -> None:
        now_iso = _iso(_utc_now())
        with self.db_manager.system_connection(immediate=True) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET state = 'cancelled',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    finished_at = ?,
                    last_error_code = 'cancelled',
                    last_error = ?,
                    updated_at = ?
                WHERE id = ? AND state IN ('queued', 'running')
                """,
                (now_iso, reason[:500], now_iso, job_id),
            )

    def cancel_user_jobs(self, user_id: str, reason: str) -> int:
        now_iso = _iso(_utc_now())
        with self.db_manager.system_connection(immediate=True) as conn:
            cur = conn.execute(
                """
                UPDATE jobs
                SET state = 'cancelled',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    finished_at = ?,
                    last_error_code = 'account_unavailable',
                    last_error = ?,
                    updated_at = ?
                WHERE owner_user_id = ? AND state IN ('queued', 'running')
                """,
                (now_iso, reason[:500], now_iso, user_id),
            )
            return cur.rowcount

    def list_user_jobs(self, scope: UserScope, *, limit: int = 25) -> list[JobRecord]:
        scope.require_active()
        with self.db_manager.system_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE owner_user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (scope.user_id, max(1, min(100, limit))),
            ).fetchall()
            return [_row_to_job(r) for r in rows]

    def get_provider_status_summary(self, scope: UserScope) -> list[dict[str, Any]]:
        scope.require_active()
        with self.db_manager.system_connection() as conn:
            providers = [
                r["provider"]
                for r in conn.execute(
                    "SELECT DISTINCT provider FROM jobs WHERE owner_user_id = ? ORDER BY provider ASC",
                    (scope.user_id,),
                ).fetchall()
            ]
            summaries: list[dict[str, Any]] = []
            for provider in providers:
                last_ok = conn.execute(
                    """
                    SELECT finished_at, result_json FROM jobs
                    WHERE owner_user_id = ? AND provider = ? AND state = 'succeeded'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (scope.user_id, provider),
                ).fetchone()
                last_err = conn.execute(
                    """
                    SELECT updated_at, last_error_code, last_error FROM jobs
                    WHERE owner_user_id = ? AND provider = ? AND (state = 'failed' OR last_error_code IS NOT NULL)
                    ORDER BY id DESC LIMIT 1
                    """,
                    (scope.user_id, provider),
                ).fetchone()
                pending = conn.execute(
                    """
                    SELECT state, available_at FROM jobs
                    WHERE owner_user_id = ? AND provider = ? AND state IN ('queued', 'running')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (scope.user_id, provider),
                ).fetchone()
                latest_any = conn.execute(
                    """
                    SELECT state, last_error_code FROM jobs
                    WHERE owner_user_id = ? AND provider = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (scope.user_id, provider),
                ).fetchone()
                credential_expired = bool(
                    latest_any
                    and latest_any["state"] == "failed"
                    and latest_any["last_error_code"] == "auth_failed"
                )
                summaries.append(
                    {
                        "provider": provider,
                        "last_success_at": last_ok["finished_at"] if last_ok else None,
                        "last_failure_at": last_err["updated_at"] if last_err else None,
                        "last_error_code": last_err["last_error_code"] if last_err else None,
                        "last_error": last_err["last_error"] if last_err else None,
                        "next_attempt_at": pending["available_at"] if pending else None,
                        "active_state": pending["state"] if pending else (latest_any["state"] if latest_any else None),
                        "credential_expired": credential_expired,
                    }
                )
            return summaries
