from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from .config import Settings
from .domain import OrderStatus


def _json_dumps(payload: dict[str, Any] | None) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False)


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

    def ensure_database(self) -> None:
        database = self._settings.db_name.replace("`", "``")
        conn = pymysql.connect(
            host=self._settings.db_host,
            user=self._settings.db_user,
            password=self._settings.db_password,
            port=self._settings.db_port,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` DEFAULT CHARSET utf8mb4")
        finally:
            conn.close()

    def apply_migration_file(self, migration_file: Path) -> bool:
        sql_text = migration_file.read_text(encoding="utf-8")
        statements = [s.strip() for s in sql_text.split(";") if s.strip()]
        migration_name = migration_file.name
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        migration_name VARCHAR(191) NOT NULL,
                        applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                        PRIMARY KEY (migration_name)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                cursor.execute(
                    "SELECT migration_name FROM schema_migrations WHERE migration_name = %s LIMIT 1",
                    (migration_name,),
                )
                if cursor.fetchone():
                    conn.commit()
                    return False

                for statement in statements:
                    try:
                        cursor.execute(statement)
                    except pymysql.err.OperationalError as exc:
                        if exc.args and exc.args[0] in {1060, 1061, 1091}:
                            continue
                        raise
                cursor.execute(
                    "INSERT INTO schema_migrations (migration_name) VALUES (%s)",
                    (migration_name,),
                )
            conn.commit()
        return True

    def find_active_trade_account(
        self,
        *,
        account_alias: str,
        account_no: str,
        account_product_code: str,
        broker: str = "KIS",
    ) -> dict[str, Any] | None:
        sql = """
        SELECT *
        FROM trade_accounts
        WHERE broker = %s
          AND account_alias = %s
          AND account_no = %s
          AND account_product_code = %s
          AND account_status = 'ACTIVE'
          AND is_active = 1
        LIMIT 1
        """
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (broker, account_alias, account_no, account_product_code))
                return cursor.fetchone()

    def bind_trade_account_alias(
        self,
        *,
        account_alias: str,
        account_no: str,
        account_product_code: str,
        broker: str = "KIS",
    ) -> dict[str, Any]:
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE trade_accounts
                    SET account_no = %s,
                        account_product_code = %s,
                        account_status = 'ACTIVE',
                        is_active = 1
                    WHERE broker = %s
                      AND account_alias = %s
                    """,
                    (account_no, account_product_code, broker, account_alias),
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        """
                        INSERT INTO trade_accounts (
                            broker,
                            account_alias,
                            account_no,
                            account_product_code,
                            account_role,
                            account_status,
                            purpose,
                            is_active
                        )
                        VALUES (%s, %s, %s, %s, 'STRATEGY', 'ACTIVE', NULL, 1)
                        """,
                        (broker, account_alias, account_no, account_product_code),
                    )
                conn.commit()

        row = self.find_active_trade_account(
            broker=broker,
            account_alias=account_alias,
            account_no=account_no,
            account_product_code=account_product_code,
        )
        if not row:
            raise RuntimeError(f"Failed to bind trade account alias: {account_alias}")
        return row

    def find_active_strategy_account_allocation(
        self,
        *,
        trade_account_id: int,
        strategy_family: str,
        allocation_role: str,
        trade_date: date,
    ) -> dict[str, Any] | None:
        sql = """
        SELECT *
        FROM strategy_account_allocations
        WHERE trade_account_id = %s
          AND strategy_family = %s
          AND allocation_role IN (%s, 'PRIMARY')
          AND is_active = 1
          AND (valid_from IS NULL OR valid_from <= %s)
          AND (valid_to IS NULL OR valid_to >= %s)
        ORDER BY FIELD(allocation_role, %s, 'PRIMARY')
        LIMIT 1
        """
        params = (
            trade_account_id,
            strategy_family,
            allocation_role,
            trade_date,
            trade_date,
            allocation_role,
        )
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchone()

    def get_order_by_idempotency(
        self,
        idempotency_key: str,
        *,
        account_no: str | None = None,
        broker: str = "KIS",
    ) -> dict[str, Any] | None:
        if account_no:
            sql = """
            SELECT *
            FROM trade_orders
            WHERE broker = %s
              AND account_no = %s
              AND idempotency_key = %s
            LIMIT 1
            """
            params = (broker, account_no, idempotency_key)
        else:
            sql = "SELECT * FROM trade_orders WHERE idempotency_key = %s LIMIT 1"
            params = (idempotency_key,)
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
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
            source_system,
            source_run_id,
            broker,
            trade_account_id,
            account_no,
            account_product_code,
            account_alias,
            side,
            source_symbol,
            symbol,
            order_type,
            quantity,
            price,
            status,
            strategy_id,
            strategy_family,
            strategy_name,
            strategy_version,
            signal_id,
            condition_id,
            condition_version,
            condition_snapshot,
            intent_metadata,
            reason,
            trade_date,
            requested_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            payload["idempotency_key"],
            payload.get("source_system", "systemTrade"),
            payload.get("source_run_id"),
            payload.get("broker", "KIS"),
            payload.get("trade_account_id"),
            payload["account_no"],
            payload.get("account_product_code"),
            payload.get("account_alias"),
            payload["side"],
            payload.get("source_symbol"),
            payload["symbol"],
            payload["order_type"],
            payload["quantity"],
            payload["price"],
            payload["status"],
            payload.get("strategy_id"),
            payload.get("strategy_family"),
            payload.get("strategy_name"),
            payload.get("strategy_version"),
            payload.get("signal_id"),
            payload.get("condition_id"),
            payload.get("condition_version"),
            _json_dumps(payload.get("condition_snapshot")),
            _json_dumps(payload.get("intent_metadata")),
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
            "filled_quantity",
            "remaining_quantity",
            "avg_fill_price",
            "sent_at",
            "closed_at",
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

    def create_account_query_request(self, payload: dict[str, Any]) -> int:
        sql = """
        INSERT INTO account_query_requests (
            broker,
            trade_account_id,
            account_no,
            account_product_code,
            account_alias,
            query_type,
            symbol,
            side,
            order_type,
            price,
            start_date,
            end_date,
            status,
            rt_cd,
            msg_cd,
            msg1,
            request_payload,
            response_payload,
            requested_at,
            responded_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            payload.get("broker", "KIS"),
            payload.get("trade_account_id"),
            payload["account_no"],
            payload.get("account_product_code"),
            payload.get("account_alias"),
            payload["query_type"],
            payload.get("symbol"),
            payload.get("side"),
            payload.get("order_type"),
            payload.get("price"),
            payload.get("start_date"),
            payload.get("end_date"),
            payload.get("status", "SUCCEEDED"),
            payload.get("rt_cd"),
            payload.get("msg_cd"),
            payload.get("msg1"),
            _json_dumps(payload.get("request_payload")),
            _json_dumps(payload.get("response_payload")),
            payload["requested_at"],
            payload.get("responded_at"),
        )
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                query_request_id = cursor.lastrowid
            conn.commit()
        return int(query_request_id)

    def insert_account_balance_summary(self, payload: dict[str, Any]) -> None:
        sql = """
        INSERT INTO account_balance_summaries (
            query_request_id,
            account_no,
            cash_total,
            cash_available,
            cash_withdrawable,
            purchase_amount,
            securities_value,
            total_value,
            eval_pnl,
            as_of,
            raw_payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            payload["query_request_id"],
            payload["account_no"],
            payload.get("cash_total"),
            payload.get("cash_available"),
            payload.get("cash_withdrawable"),
            payload.get("purchase_amount"),
            payload.get("securities_value"),
            payload.get("total_value"),
            payload.get("eval_pnl"),
            payload["as_of"],
            _json_dumps(payload.get("raw_payload")),
        )
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
            conn.commit()

    def insert_account_holding_snapshots(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        sql = """
        INSERT INTO account_holding_snapshots (
            query_request_id,
            account_no,
            symbol,
            product_name,
            quantity,
            available_quantity,
            avg_price,
            current_price,
            purchase_amount,
            evaluation_amount,
            pnl,
            pnl_rate,
            as_of,
            raw_payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = [
            (
                row["query_request_id"],
                row["account_no"],
                row["symbol"],
                row.get("product_name"),
                row.get("quantity", 0),
                row.get("available_quantity"),
                row.get("avg_price"),
                row.get("current_price"),
                row.get("purchase_amount"),
                row.get("evaluation_amount"),
                row.get("pnl"),
                row.get("pnl_rate"),
                row["as_of"],
                _json_dumps(row.get("raw_payload")),
            )
            for row in rows
        ]
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.executemany(sql, params)
            conn.commit()

    def insert_order_capacity_snapshot(self, payload: dict[str, Any]) -> None:
        sql = """
        INSERT INTO order_capacity_snapshots (
            query_request_id,
            account_no,
            capacity_type,
            symbol,
            order_type,
            price,
            max_buy_amount,
            max_buy_quantity,
            order_possible_cash,
            order_possible_quantity,
            sellable_quantity,
            holding_quantity,
            current_price,
            as_of,
            raw_payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            payload["query_request_id"],
            payload["account_no"],
            payload["capacity_type"],
            payload["symbol"],
            payload.get("order_type"),
            payload.get("price"),
            payload.get("max_buy_amount"),
            payload.get("max_buy_quantity"),
            payload.get("order_possible_cash"),
            payload.get("order_possible_quantity"),
            payload.get("sellable_quantity"),
            payload.get("holding_quantity"),
            payload.get("current_price"),
            payload["as_of"],
            _json_dumps(payload.get("raw_payload")),
        )
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
            conn.commit()

    def insert_cancelable_order_snapshots(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        sql = """
        INSERT INTO cancelable_order_snapshots (
            query_request_id,
            account_no,
            broker_order_no,
            broker_org_order_no,
            side,
            symbol,
            order_type,
            order_quantity,
            unfilled_quantity,
            order_price,
            ordered_at,
            raw_payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = [
            (
                row["query_request_id"],
                row["account_no"],
                row.get("broker_order_no"),
                row.get("broker_org_order_no"),
                row.get("side"),
                row.get("symbol"),
                row.get("order_type"),
                row.get("order_quantity"),
                row.get("unfilled_quantity"),
                row.get("order_price"),
                row.get("ordered_at"),
                _json_dumps(row.get("raw_payload")),
            )
            for row in rows
        ]
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.executemany(sql, params)
            conn.commit()

    def insert_broker_execution_snapshots(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        sql = """
        INSERT INTO broker_execution_snapshots (
            query_request_id,
            account_no,
            broker_order_no,
            broker_fill_id,
            side,
            symbol,
            order_quantity,
            filled_quantity,
            fill_price,
            fill_amount,
            ordered_at,
            filled_at,
            raw_payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = [
            (
                row["query_request_id"],
                row["account_no"],
                row.get("broker_order_no"),
                row.get("broker_fill_id"),
                row.get("side"),
                row.get("symbol"),
                row.get("order_quantity"),
                row.get("filled_quantity"),
                row.get("fill_price"),
                row.get("fill_amount"),
                row.get("ordered_at"),
                row.get("filled_at"),
                _json_dumps(row.get("raw_payload")),
            )
            for row in rows
        ]
        with self._conn() as conn:
            with conn.cursor() as cursor:
                cursor.executemany(sql, params)
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
