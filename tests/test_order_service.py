from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from system_trade.domain import OrderRequest, OrderStatus, OrderType, Side
from system_trade.exceptions import ConfigError, KISError
from system_trade.order_service import OrderService


_DEFAULT = object()


@dataclass
class FakeSettings:
    kis_account_no: str | None = "11111111"
    kis_acnt_prdt: str | None = "01"
    account_alias: str | None = "hagfish"

    def require_account(self) -> None:
        if not self.kis_account_no or not self.kis_acnt_prdt:
            raise ConfigError("missing account")


class FakeKISClient:
    def __init__(self, settings: FakeSettings, *, buying_power: dict[str, Any] | None = None):
        self._settings = settings
        self.buying_power = buying_power
        self.buying_power_requests: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []

    def get_buying_power(self, **payload: Any) -> dict[str, Any]:
        self.buying_power_requests.append(payload)
        return self.buying_power or {"output": {}}

    def place_cash_order(self, **payload: Any) -> dict[str, Any]:
        self.orders.append(payload)
        return {"output": {"ODNO": "12345", "KRX_FWDG_ORD_ORGNO": "67890"}}


class RejectingKISClient(FakeKISClient):
    def place_cash_order(self, **payload: Any) -> dict[str, Any]:
        self.orders.append(payload)
        raise KISError(
            "TTTC0012U",
            "실전투자 TR 이 아닙니다.",
            payload={
                "rt_cd": "1",
                "msg_cd": "EGW02005",
                "msg1": "실전투자 TR 이 아닙니다.",
                "_systemtrade": {
                    "order_tr_id": "TTTC0012U",
                    "order_tr_id_attempts": ["TTTC0802U", "TTTC0012U"],
                },
            },
            error_code="EGW02005",
        )


class FakeRepository:
    def __init__(
        self,
        *,
        active_account: dict[str, Any] | None | object = _DEFAULT,
        allocation: dict[str, Any] | None | object = _DEFAULT,
        existing: dict[str, Any] | None = None,
    ):
        self.active_account = {"id": 7, "account_role": "STRATEGY"} if active_account is _DEFAULT else active_account
        self.allocation = {"id": 11} if allocation is _DEFAULT else allocation
        self.existing = existing
        self.account_lookup: dict[str, Any] | None = None
        self.allocation_lookup: dict[str, Any] | None = None
        self.idempotency_lookup: tuple[str, dict[str, Any]] | None = None
        self.created_payload: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self.status_updates: list[tuple[int, Any, dict[str, Any]]] = []

    def find_active_trade_account(self, **kwargs: Any) -> dict[str, Any] | None:
        self.account_lookup = kwargs
        return self.active_account

    def find_active_strategy_account_allocation(self, **kwargs: Any) -> dict[str, Any] | None:
        self.allocation_lookup = kwargs
        return self.allocation

    def get_order_by_idempotency(self, idempotency_key: str, **kwargs: Any) -> dict[str, Any] | None:
        self.idempotency_lookup = (idempotency_key, kwargs)
        return self.existing

    def create_order(self, payload: dict[str, Any]) -> int:
        self.created_payload = payload
        return 42

    def insert_order_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)

    def update_order_status(self, order_id: int, status: Any, **updates: Any) -> None:
        self.status_updates.append((order_id, status, updates))

    def get_order_by_id(self, order_id: int) -> dict[str, Any] | None:
        status = self.status_updates[-1][1].value if self.status_updates else "SENT"
        return {"id": order_id, "status": status, "trade_account_id": 7}


@pytest.fixture(autouse=True)
def _disable_order_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("system_trade.order_service.notify_order_event", lambda *args, **kwargs: None)


def _request(**overrides: Any) -> OrderRequest:
    payload = {
        "side": Side.BUY,
        "symbol": "005930",
        "quantity": 1,
        "order_type": OrderType.MARKET,
        "price": None,
        "idempotency_key": "hagfish:2026-05-11:005930:BUY",
        "strategy_family": "hagfish",
        "strategy_name": "hagfish_v2",
        "account_alias": "hagfish",
        "requested_at": datetime(2026, 5, 11, 0, 0, 0),
    }
    payload.update(overrides)
    return OrderRequest(**payload)


def test_submit_order_requires_active_account_and_strategy_mapping() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(FakeSettings())
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    order = service.submit_order(_request())

    assert order["trade_account_id"] == 7
    assert repository.account_lookup == {
        "broker": "KIS",
        "account_alias": "hagfish",
        "account_no": "11111111",
        "account_product_code": "01",
    }
    assert repository.allocation_lookup == {
        "trade_account_id": 7,
        "strategy_family": "hagfish",
        "allocation_role": "BUY",
        "trade_date": datetime(2026, 5, 11).date(),
    }
    assert repository.created_payload is not None
    assert repository.created_payload["trade_account_id"] == 7
    assert kis_client.orders


def test_test_account_can_run_hagfish_strategy_mapping() -> None:
    repository = FakeRepository(active_account={"id": 3, "account_role": "TEST"}, allocation={"id": 12})
    kis_client = FakeKISClient(FakeSettings(account_alias="test"))
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    service.submit_order(_request(account_alias="test"))

    assert repository.account_lookup == {
        "broker": "KIS",
        "account_alias": "test",
        "account_no": "11111111",
        "account_product_code": "01",
    }
    assert repository.allocation_lookup == {
        "trade_account_id": 3,
        "strategy_family": "hagfish",
        "allocation_role": "BUY",
        "trade_date": datetime(2026, 5, 11).date(),
    }
    assert repository.created_payload is not None
    assert repository.created_payload["trade_account_id"] == 3
    assert repository.created_payload["account_alias"] == "test"
    assert kis_client.orders


def test_record_order_intent_does_not_call_kis() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(FakeSettings())
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    order = service.record_order_intent(_request())

    assert order["trade_account_id"] == 7
    assert repository.created_payload is not None
    assert repository.events[0]["note"] == "record_only_no_broker_submit"
    assert not repository.status_updates
    assert not kis_client.orders


def test_request_trade_date_drives_allocation_and_order_date() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(FakeSettings())
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    service.record_order_intent(_request(trade_date=datetime(2026, 5, 12).date()))

    assert repository.allocation_lookup is not None
    assert repository.allocation_lookup["trade_date"] == datetime(2026, 5, 12).date()
    assert repository.created_payload is not None
    assert repository.created_payload["trade_date"] == datetime(2026, 5, 12).date()


def test_idempotency_lookup_is_scoped_to_account() -> None:
    existing = {"id": 99, "account_no": "11111111", "idempotency_key": "same-key"}
    repository = FakeRepository(existing=existing)
    kis_client = FakeKISClient(FakeSettings())
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    order = service.submit_order(_request(idempotency_key="same-key"))

    assert order == existing
    assert repository.idempotency_lookup == ("same-key", {"account_no": "11111111", "broker": "KIS"})
    assert repository.created_payload is None
    assert not kis_client.orders


def _stored_order(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": 77,
        "idempotency_key": "hagfish:2026-05-11:005930:BUY",
        "source_system": "systemAlgo",
        "source_run_id": "run-1",
        "broker": "KIS",
        "trade_account_id": 7,
        "account_no": "11111111",
        "account_product_code": "01",
        "account_alias": "hagfish",
        "side": "BUY",
        "source_symbol": "005930",
        "symbol": "005930",
        "order_type": "MARKET",
        "quantity": 1,
        "price": None,
        "status": "REJECTED",
        "strategy_id": None,
        "strategy_family": "hagfish",
        "strategy_name": "hagfish_v2",
        "strategy_version": "2.6",
        "signal_id": "signal-1",
        "condition_id": "condition-1",
        "condition_version": "2.6",
        "condition_snapshot": None,
        "intent_metadata": None,
        "reason": "retry-test",
        "trade_date": datetime(2026, 5, 11).date(),
        "broker_order_no": None,
        "sent_at": None,
    }
    payload.update(overrides)
    return payload


def test_resubmit_order_sends_existing_rejected_order() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(FakeSettings())
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    order = service.resubmit_order(_stored_order())

    assert order["status"] == "SENT"
    assert kis_client.orders == [
        {
            "side": Side.BUY,
            "symbol": "005930",
            "quantity": 1,
            "order_type": OrderType.MARKET,
            "price": None,
        }
    ]
    assert repository.events[0]["event_type"] == "RESUBMITTING"
    assert repository.status_updates[0][1] == OrderStatus.SUBMITTING
    assert repository.status_updates[0][2]["error_code"] is None
    assert repository.status_updates[-1][1] == OrderStatus.SENT


def test_rejected_order_event_records_kis_tr_attempts() -> None:
    repository = FakeRepository()
    kis_client = RejectingKISClient(FakeSettings())
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    order = service.submit_order(_request())

    assert order["status"] == "REJECTED"
    rejected_event = repository.events[-1]
    assert rejected_event["event_type"] == "REJECTED"
    assert rejected_event["response_payload"]["_systemtrade"]["order_tr_id"] == "TTTC0012U"
    assert rejected_event["response_payload"]["_systemtrade"]["order_tr_id_attempts"] == [
        "TTTC0802U",
        "TTTC0012U",
    ]
    assert repository.status_updates[-1][1] == OrderStatus.REJECTED
    assert repository.status_updates[-1][2]["error_code"] == "EGW02005"


def test_market_buy_with_reference_price_converts_to_limit_and_caps_quantity() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(
        FakeSettings(),
        buying_power={
            "output": {
                "max_buy_amt": "240000",
                "max_buy_qty": "2",
                "ord_psbl_cash": "250000",
            }
        },
    )
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    order = service.submit_order(
        _request(
            quantity=5,
            intent_metadata={"reference_price": 120000},
        )
    )

    assert order["status"] == "SENT"
    assert kis_client.buying_power_requests == [
        {
            "symbol": "005930",
            "price": 120000,
            "order_type": OrderType.LIMIT,
        }
    ]
    assert kis_client.orders == [
        {
            "side": Side.BUY,
            "symbol": "005930",
            "quantity": 2,
            "order_type": OrderType.LIMIT,
            "price": 120000,
        }
    ]
    assert repository.status_updates[0][2]["order_type"] == "LIMIT"
    assert repository.status_updates[0][2]["quantity"] == 2
    assert repository.status_updates[0][2]["price"] == 120000
    adjustment = repository.events[1]["request_payload"]["adjustment"]
    assert adjustment["original_quantity"] == 5
    assert adjustment["adjusted_quantity"] == 2
    assert adjustment["adjusted_order_type"] == "LIMIT"


def test_market_buy_with_no_buying_power_rejects_before_order_submit() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(
        FakeSettings(),
        buying_power={
            "output": {
                "max_buy_qty": "0",
                "ord_psbl_cash": "0",
            }
        },
    )
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    order = service.submit_order(
        _request(
            quantity=5,
            condition_snapshot={"features": {"reference_price": 120000}},
        )
    )

    assert order["status"] == "REJECTED"
    assert not kis_client.orders
    assert repository.status_updates[0][1] == OrderStatus.REJECTED
    assert repository.status_updates[0][2]["error_code"] == "BUYING_POWER_EXHAUSTED"
    assert repository.events[-1]["event_type"] == "REJECTED"


def test_resubmit_order_blocks_already_sent_order() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(FakeSettings())
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    with pytest.raises(ConfigError, match="Only CREATED/REJECTED/FAILED"):
        service.resubmit_order(_stored_order(status="SENT"))

    assert not kis_client.orders


def test_missing_strategy_allocation_blocks_order_before_kis_call() -> None:
    repository = FakeRepository(allocation=None)
    kis_client = FakeKISClient(FakeSettings())
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    with pytest.raises(ConfigError, match="strategy_account_allocations"):
        service.submit_order(_request())

    assert repository.created_payload is None
    assert not kis_client.orders


def test_limit_order_without_price_is_rejected_before_db_write() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(FakeSettings())
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    with pytest.raises(ConfigError, match="LIMIT"):
        service.submit_order(_request(order_type=OrderType.LIMIT, price=None))

    assert repository.account_lookup is None
    assert repository.created_payload is None
    assert not kis_client.orders
