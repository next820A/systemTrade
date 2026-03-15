from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from .config import Settings
from .domain import OrderStatus


class MySQLRepository:
    def __init__(self, settings: Settings):
        self._settings = settings

    @contextmanager
    def _conn(self) -> Iterator[pymysql.connections.Connection]:
        conn = pymysql.connect(
            host=self._settings.db_host,
            user=self._settings.db_user,
            password=self._settings.db_password,
            database=self._settings.db_name,
            port=self._settings.db_port,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )
        try:
            yield conn
        finally:
            conn.close()

    def ping(self) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                row = cursor.fetchone()
                return bool(row and row.get("ok") == 1)

    def apply_migration_file(self, migration_file: Path) -> None:
        sql_text = migration_file.read_text(encoding="utf-8")
        statements = [s.strip() for s in sql_text.split(";") if s.strip()]
        with self._conn() as conn:
            with conn.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
            conn.commit()

    def get_order_by_idempotency(self, idempotency_key: str) -> dict[str, Any] | None:
        sql = "SELECT * FROM trade_orders WHERE idempotency_key = %s LIMIT 1"
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (idempotency_key,))
                return cursor.fetchone()

    def get_order_by_id(self, order_id: int) -> dict[str, Any] | None:
        sql = "SELECT * FROM trade_orders WHERE id = %s LIMIT 1"
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (order_id,))
                return cursor.fetchone()

    def create_order(self, payload: dict[str, Any]) -> int:
        sql = """
        INSERT INTO trade_orders (
            idempotency_key,
            account_no,
            side,
            symbol,
            order_type,
            quantity,
            price,
            status,
            strategy_id,
            strategy_name,
            reason,
            trade_date,
            requested_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            payload["idempotency_key"],
            payload["account_no"],
            payload["side"],
            payload["symbol"],
            payload["order_type"],
            payload["quantity"],
            payload["price"],
            payload["status"],
            payload.get("strategy_id"),
            payload.get("strategy_name"),
            payload.get("reason"),
            payload["trade_date"],
            payload["requested_at"],
        )

        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                order_id = cursor.lastrowid
            conn.commit()
        return int(order_id)

    def update_order_status(self, order_id: int, status: OrderStatus, **updates: Any) -> None:
        allowed_keys = {
            "broker_order_no",
            "broker_org_order_no",
            "sent_at",
            "last_synced_at",
            "error_code",
            "error_message",
        }
        set_clauses = ["status = %s"]
        params: list[Any] = [status.value]

        for key, value in updates.items():
            if key in allowed_keys:
                set_clauses.append(f"{key} = %s")
                params.append(value)

        params.append(order_id)
        sql = f"UPDATE trade_orders SET {', '.join(set_clauses)} WHERE id = %s"

        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
            conn.commit()

    def insert_order_event(
        self,
        order_id: int,
        event_type: str,
        status: OrderStatus | None,
        request_payload: dict[str, Any] | None,
        response_payload: dict[str, Any] | None,
        note: str | None,
        event_at: datetime,
    ) -> None:
        sql = """
        INSERT INTO trade_order_events (
            order_id,
            event_type,
            status,
            note,
            request_payload,
            response_payload,
            event_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            order_id,
            event_type,
            status.value if status else None,
            note,
            json.dumps(request_payload, ensure_ascii=False) if request_payload is not None else None,
            json.dumps(response_payload, ensure_ascii=False) if response_payload is not None else None,
            event_at,
        )

        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
            conn.commit()

    def list_orders(self, limit: int = 20) -> list[dict[str, Any]]:
        sql = """
        SELECT *
        FROM trade_orders
        ORDER BY id DESC
        LIMIT %s
        """
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (int(limit),))
                return list(cursor.fetchall())
