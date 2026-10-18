from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from system_trade.domain import OrderStatus
from system_trade.repository import MySQLRepository


@pytest.mark.parametrize("case", ["success", "conflict", "event_failure"])
def test_broker_state_and_audit_event_share_one_guarded_transaction(case):
    repo = MySQLRepository(MagicMock())
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    current = {"status": "SENT", "quantity": 2, "filled_quantity": 0, "broker_order_no": "demo91"}
    cursor.fetchone.return_value = {**current, "status": "FILLED"} if case == "conflict" else current

    @contextmanager
    def connection():
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise

    repo._conn = connection
    if case == "event_failure":
        def execute(sql, params):
            if "INSERT INTO trade_order_events" in sql:
                raise RuntimeError("simulated insert failure")
        cursor.execute.side_effect = execute
    kwargs = dict(
        order_id=91, status=OrderStatus.REJECTED, expected=current,
        updates={"filled_quantity": 0, "remaining_quantity": 2},
        event={"event_type": "BROKER_STATE_SYNCED", "request_payload": {"before": current},
               "response_payload": {"rejected_quantity": 2}, "note": "daily_execution_sync", "event_at": datetime(2026, 10, 18)},
    )
    if case == "event_failure":
        with pytest.raises(RuntimeError, match="simulated insert failure"):
            repo.apply_order_sync(**kwargs)
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()
    elif case == "conflict":
        assert repo.apply_order_sync(**kwargs) is False
        assert cursor.execute.call_count == 1
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()
    else:
        assert repo.apply_order_sync(**kwargs) is True
        conn.commit.assert_called_once()
        assert cursor.execute.call_count == 3
        assert "FOR UPDATE" in cursor.execute.call_args_list[0].args[0]
