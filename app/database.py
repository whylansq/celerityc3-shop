from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL UNIQUE,
    telegram_username TEXT,
    celerity_user_id TEXT,
    subscription_token TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE,
    telegram_user_id INTEGER NOT NULL,
    telegram_username TEXT,
    plan_id TEXT NOT NULL,
    plan_name TEXT NOT NULL,
    price INTEGER NOT NULL,
    duration_days INTEGER NOT NULL,
    status TEXT NOT NULL,
    receipt_file_id TEXT,
    receipt_file_type TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewed_by INTEGER,
    celerity_user_id TEXT,
    celerity_subscription_token TEXT,
    celerity_expires_at TEXT,
    provisioning_target_expires_at TEXT,
    error_message TEXT,
    rejection_reason TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
);

CREATE INDEX IF NOT EXISTS idx_users_telegram_user_id
    ON users(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_orders_telegram_user_id
    ON orders(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status
    ON orders(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_order_id
    ON orders(order_id);
"""


class Database:
    def __init__(self, path: str):
        self.path = str(Path(path))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def upsert_user(self, telegram_user_id: int, username: str | None) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (telegram_user_id, telegram_username, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    telegram_username = excluded.telegram_username,
                    updated_at = excluded.updated_at
                """,
                (telegram_user_id, username, now, now),
            )

    def get_user(self, telegram_user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
        return self._dict(row)

    def create_order(
        self,
        telegram_user_id: int,
        telegram_username: str | None,
        plan_id: str,
        plan_name: str,
        price: int,
        duration_days: int,
        *,
        allow_duplicate: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        open_statuses = ("pending_receipt", "pending_review")
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not allow_duplicate:
                existing = conn.execute(
                    """
                    SELECT * FROM orders
                    WHERE telegram_user_id = ?
                      AND plan_id = ?
                      AND status IN (?, ?)
                    ORDER BY id DESC LIMIT 1
                    """,
                    (telegram_user_id, plan_id, *open_statuses),
                ).fetchone()
                if existing is not None:
                    conn.commit()
                    return dict(existing), True

            cursor = conn.execute(
                """
                INSERT INTO orders (
                    telegram_user_id, telegram_username, plan_id, plan_name,
                    price, duration_days, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending_receipt', ?, ?)
                """,
                (
                    telegram_user_id,
                    telegram_username,
                    plan_id,
                    plan_name,
                    price,
                    duration_days,
                    now,
                    now,
                ),
            )
            numeric_id = int(cursor.lastrowid)
            order_code = f"VPN-{datetime.now(timezone.utc):%Y%m%d}-{numeric_id:06d}"
            conn.execute(
                "UPDATE orders SET order_id = ? WHERE id = ?",
                (order_code, numeric_id),
            )
            row = conn.execute(
                "SELECT * FROM orders WHERE id = ?", (numeric_id,)
            ).fetchone()
            conn.commit()
        assert row is not None
        return dict(row), False

    def get_order(self, numeric_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE id = ?", (numeric_id,)
            ).fetchone()
        return self._dict(row)

    def attach_receipt(
        self, numeric_id: int, file_id: str, file_type: str
    ) -> bool:
        now = utc_now()
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE orders
                SET receipt_file_id = ?, receipt_file_type = ?,
                    status = 'pending_review', updated_at = ?
                WHERE id = ? AND status IN ('pending_receipt', 'pending_review')
                """,
                (file_id, file_type, now, numeric_id),
            )
        return result.rowcount == 1

    def claim_for_approval(self, numeric_id: int, admin_id: int) -> str:
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM orders WHERE id = ?", (numeric_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                return "missing"
            status = str(row["status"])
            if status == "completed":
                conn.commit()
                return "completed"
            if status in {"approved", "provisioning"}:
                conn.commit()
                return "processing"
            if status != "pending_review":
                conn.commit()
                return status
            conn.execute(
                """
                UPDATE orders
                SET status = 'approved', reviewed_at = ?, reviewed_by = ?,
                    error_message = NULL, updated_at = ?
                WHERE id = ? AND status = 'pending_review'
                """,
                (now, admin_id, now, numeric_id),
            )
            conn.commit()
        return "claimed"

    def claim_failed_for_retry(self, numeric_id: int, admin_id: int) -> str:
        now = utc_now()
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE orders
                SET status = 'approved', reviewed_at = COALESCE(reviewed_at, ?),
                    reviewed_by = ?, error_message = NULL, updated_at = ?
                WHERE id = ? AND status = 'failed'
                """,
                (now, admin_id, now, numeric_id),
            )
        return "claimed" if result.rowcount == 1 else "not_retryable"

    def mark_provisioning(self, numeric_id: int) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE orders SET status = 'provisioning', updated_at = ?
                WHERE id = ? AND status = 'approved'
                """,
                (utc_now(), numeric_id),
            )
        return result.rowcount == 1

    def set_provisioning_target(self, numeric_id: int, target: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET provisioning_target_expires_at = ?, updated_at = ?
                WHERE id = ? AND provisioning_target_expires_at IS NULL
                """,
                (target, utc_now(), numeric_id),
            )

    def save_celerity_state(
        self,
        numeric_id: int,
        telegram_user_id: int,
        celerity_user_id: str,
        token: str,
        expires_at: str,
    ) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET celerity_user_id = ?, celerity_subscription_token = ?,
                    celerity_expires_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (celerity_user_id, token, expires_at, now, numeric_id),
            )
            conn.execute(
                """
                UPDATE users
                SET celerity_user_id = ?, subscription_token = ?,
                    expires_at = ?, updated_at = ?
                WHERE telegram_user_id = ?
                """,
                (celerity_user_id, token, expires_at, now, telegram_user_id),
            )

    def mark_completed(self, numeric_id: int) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE orders
                SET status = 'completed', error_message = NULL, updated_at = ?
                WHERE id = ? AND status = 'provisioning'
                """,
                (utc_now(), numeric_id),
            )
        return result.rowcount == 1

    def mark_failed(self, numeric_id: int, message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET status = 'failed', error_message = ?, updated_at = ?
                WHERE id = ? AND status != 'completed'
                """,
                (message[:1000], utc_now(), numeric_id),
            )

    def reject_order(self, numeric_id: int, admin_id: int, reason: str) -> bool:
        now = utc_now()
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE orders
                SET status = 'rejected', reviewed_at = ?, reviewed_by = ?,
                    rejection_reason = ?, updated_at = ?
                WHERE id = ? AND status = 'pending_review'
                """,
                (now, admin_id, reason[:1000], now, numeric_id),
            )
        return result.rowcount == 1

    def list_orders(self, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM orders
                WHERE status IN ({placeholders})
                ORDER BY created_at ASC
                """,
                statuses,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_completed_order(self, telegram_user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM orders
                WHERE telegram_user_id = ? AND status = 'completed'
                ORDER BY id DESC LIMIT 1
                """,
                (telegram_user_id,),
            ).fetchone()
        return self._dict(row)

    def count_users(self) -> int:
        return self._count("users")

    def count_orders(self) -> int:
        return self._count("orders")

    def count_orders_by_status(self, status: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM orders WHERE status = ?", (status,)
            ).fetchone()
        return int(row["count"])

    def count_active_subscriptions(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM users
                WHERE subscription_token IS NOT NULL
                  AND expires_at > ?
                """,
                (utc_now(),),
            ).fetchone()
        return int(row["count"])

    def _count(self, table: str) -> int:
        if table not in {"users", "orders"}:
            raise ValueError("Unsupported table")
        with self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"])