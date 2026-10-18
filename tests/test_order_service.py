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
    def __init__(
        self,
        settings: FakeSettings,
        *,
        buying_power: dict[str, Any] | None = None,
        asking_price: dict[str, Any] | None = None,
        cancelable_orders: dict[str, Any] | None = None,
    ):
        self._settings = settings
        self.buying_power = buying_power
        self.asking_price = asking_price or {"output1": {}}
        self.cancelable_orders = cancelable_orders or {"output": []}
        self.buying_power_requests: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.revisions: list[dict[str, Any]] = []

    def get_buying_power(self, **payload: Any) -> dict[str, Any]:
        self.buying_power_requests.append(payload)
        return self.buying_power or {"output": {}}

    def place_cash_order(self, **payload: Any) -> dict[str, Any]:
        self.orders.append(payload)
        return {"output": {"ODNO": "12345", "KRX_FWDG_ORD_ORGNO": "67890"}}

    def get_asking_price(self, symbol: str) -> dict[str, Any]:
        self.asked_symbol = symbol
        return self.asking_price

    def get_cancelable_orders(self, **payload: Any) -> dict[str, Any]:
        self.cancelable_request = payload
        return self.cancelable_orders

    def revise_or_cancel_order(self, **payload: Any) -> dict[str, Any]:
        self.revisions.append(payload)
        return {"output": {"ODNO": "54321", "KRX_FWDG_ORD_ORGNO": "06010"}}


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


class BuyingPowerErrorKISClient(FakeKISClient):
    def get_buying_power(self, **payload: Any) -> dict[str, Any]:
        self.buying_power_requests.append(payload)
        raise KISError(
            "TTTC8908R",
            "buying power unavailable",
            error_code="RATE_LIMIT",
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
        self.execution_orders: list[dict[str, Any]] = []

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
        for order in self.execution_orders:
            if int(order.get("id") or 0) == order_id:
                order.update(updates)
                order["status"] = status.value if hasattr(status, "value") else str(status)

    def get_order_by_id(self, order_id: int) -> dict[str, Any] | None:
        status = self.status_updates[-1][1].value if self.status_updates else "SENT"
        return {"id": order_id, "status": status, "trade_account_id": 7}

    def list_orders_for_account_trade_date(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.execution_orders

    def get_order_sync_progress(self, order_id: int, broker_order_no: str) -> dict[str, Any]:
        for event in reversed(self.events):
            payload = event.get("response_payload") or {}
            if event["order_id"] == order_id and payload.get("broker_order_no") == broker_order_no and "base_filled_quantity" in payload:
                return payload
        return {}

    def apply_order_sync(self, *, order_id: int, status: Any, updates: dict[str, Any], event: dict[str, Any], expected: dict[str, Any]) -> bool:
        self.update_order_status(order_id, status, **updates)
        self.insert_order_event(order_id=order_id, status=status, **event)
        return True


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


def test_limit_buy_caps_quantity_to_current_buying_power() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(
        FakeSettings(),
        buying_power={
            "output": {
                "max_buy_qty": "128",
                "ord_psbl_cash": "433637",
            }
        },
    )
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    order = service.submit_order(
        _request(
            symbol="018880",
            quantity=290,
            order_type=OrderType.LIMIT,
            price=3385,
        )
    )

    assert order["status"] == "SENT"
    assert kis_client.buying_power_requests == [
        {
            "symbol": "018880",
            "price": 3385,
            "order_type": OrderType.LIMIT,
        }
    ]
    assert kis_client.orders == [
        {
            "side": Side.BUY,
            "symbol": "018880",
            "quantity": 128,
            "order_type": OrderType.LIMIT,
            "price": 3385,
        }
    ]
    adjustment = repository.events[1]["request_payload"]["adjustment"]
    assert adjustment["reason"] == "limit_buy_capped_to_buying_power"
    assert adjustment["adjusted_quantity"] == 128
    assert repository.status_updates[0][2]["quantity"] == 128


def test_limit_buy_with_zero_buying_power_rejects_before_submit() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(
        FakeSettings(),
        buying_power={"output": {"max_buy_qty": "0", "ord_psbl_cash": "0"}},
    )
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    order = service.submit_order(
        _request(quantity=3, order_type=OrderType.LIMIT, price=102000)
    )

    assert order["status"] == "REJECTED"
    assert not kis_client.orders
    assert repository.status_updates[0][2]["error_code"] == "BUYING_POWER_EXHAUSTED"
    assert repository.events[-1]["note"] == "buying_power_exhausted_before_submit"


def test_limit_buy_blocks_when_buying_power_check_fails() -> None:
    repository = FakeRepository()
    kis_client = BuyingPowerErrorKISClient(FakeSettings())
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    order = service.submit_order(
        _request(quantity=3, order_type=OrderType.LIMIT, price=102000)
    )

    assert order["status"] == "REJECTED"
    assert not kis_client.orders
    assert repository.status_updates[0][2]["error_code"] == "BUYING_POWER_CHECK_FAILED"
    assert repository.events[-1]["note"] == "buying_power_check_failed"


def test_sync_daily_executions_marks_matching_order_filled() -> None:
    repository = FakeRepository()
    repository.execution_orders = [
        {
            "id": 45,
            "broker_order_no": "00012345",
            "side": "SELL",
            "symbol": "069500",
            "quantity": 11,
            "filled_quantity": 0,
            "remaining_quantity": 11,
            "status": "SENT",
        }
    ]
    kis_client = FakeKISClient(FakeSettings())
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    result = service.sync_daily_executions(
        trade_date=datetime(2026, 7, 21).date(),
        response={
            "output1": [
                {
                    "odno": "00012345",
                    "sll_buy_dvsn_cd_name": "매도",
                    "pdno": "069500",
                    "ord_qty": "11",
                    "tot_ccld_qty": "11",
                    "avg_prvs": "104095",
                    "tot_ccld_amt": "1145045",
                }
            ]
        },
    )

    assert result["synced_order_ids"] == [45]
    assert result["orders"][0]["status"] == "FILLED"
    assert repository.status_updates[0][1] == OrderStatus.FILLED
    assert repository.status_updates[0][2]["filled_quantity"] == 11
    assert repository.status_updates[0][2]["remaining_quantity"] == 0
    assert repository.events[-1]["event_type"] == "FILL_SYNCED"


def test_sync_daily_executions_keeps_partial_fill_open() -> None:
    repository = FakeRepository()
    repository.execution_orders = [
        {
            "id": 46,
            "broker_order_no": "00012346",
            "side": "SELL",
            "symbol": "069500",
            "quantity": 11,
            "filled_quantity": 0,
            "remaining_quantity": 11,
            "status": "SENT",
        }
    ]
    service = OrderService(
        repository=repository,  # type: ignore[arg-type]
        kis_client=FakeKISClient(FakeSettings()),  # type: ignore[arg-type]
    )

    result = service.sync_daily_executions(
        trade_date=datetime(2026, 7, 21).date(),
        response={
            "output1": [
                {
                    "odno": "00012346",
                    "pdno": "069500",
                    "ord_qty": "11",
                    "tot_ccld_qty": "5",
                    "avg_prvs": "104095",
                }
            ]
        },
    )

    assert result["orders"][0]["status"] == "PARTIALLY_FILLED"
    assert result["orders"][0]["filled_quantity"] == 5
    assert result["orders"][0]["remaining_quantity"] == 6


def test_sync_daily_executions_accumulates_fill_after_partial_order_reprice() -> None:
    repository = FakeRepository()
    repository.execution_orders = [
        {
            "id": 47,
            "broker_order_no": "00054321",
            "side": "SELL",
            "symbol": "069500",
            "quantity": 11,
            "filled_quantity": 5,
            "remaining_quantity": 6,
            "avg_fill_price": "104000",
            "status": "PARTIALLY_FILLED",
        }
    ]
    service = OrderService(
        repository=repository,  # type: ignore[arg-type]
        kis_client=FakeKISClient(FakeSettings()),  # type: ignore[arg-type]
    )

    result = service.sync_daily_executions(
        trade_date=datetime(2026, 7, 21).date(),
        response={
            "output1": [
                {
                    "odno": "00054321",
                    "pdno": "069500",
                    "ord_qty": "6",
                    "tot_ccld_qty": "6",
                    "avg_prvs": "103800",
                }
            ]
        },
    )

    assert result["orders"][0]["status"] == "FILLED"
    assert result["orders"][0]["filled_quantity"] == 11
    assert result["orders"][0]["remaining_quantity"] == 0
    assert float(repository.status_updates[0][2]["avg_fill_price"]) == pytest.approx(103890.9091)


def test_finalize_closed_session_cancels_unfilled_remainder() -> None:
    repository = FakeRepository()
    repository.execution_orders = [
        _stored_order(
            id=57,
            status="PARTIALLY_FILLED",
            quantity=5,
            filled_quantity=2,
            broker_order_no="00012345",
        )
    ]
    service = OrderService(  # type: ignore[arg-type]
        repository=repository,
        kis_client=FakeKISClient(FakeSettings()),
    )

    result = service.finalize_closed_session(
        trade_date=datetime(2026, 8, 19).date(),
        response={"output1": [{"odno": "00012345", "pdno": "005930", "rmn_qty": "3"}]},
    )

    assert result["finalized_order_ids"] == [57]
    assert repository.status_updates[-1][1] == OrderStatus.CANCELED
    assert repository.status_updates[-1][2]["remaining_quantity"] == 3
    assert repository.events[-1]["event_type"] == "SESSION_EXPIRED"


def test_reprice_sell_order_moves_to_best_bid_within_bound() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(
        FakeSettings(),
        asking_price={"output1": {"bidp1": "103800", "askp1": "103805"}},
        cancelable_orders={
            "output": [
                {
                    "ord_gno_brno": "06010",
                    "odno": "00012345",
                    "orgn_odno": "",
                    "pdno": "069500",
                    "psbl_qty": "11",
                    "sll_buy_dvsn_cd": "01",
                    "excg_id_dvsn_cd": "KRX",
                }
            ]
        },
    )
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    result = service.reprice_order(
        _stored_order(
            side="SELL",
            symbol="069500",
            order_type="LIMIT",
            quantity=11,
            price=104095,
            status="SENT",
            broker_order_no="00012345",
            broker_org_order_no="06010",
        ),
        anchor_price=104095,
        max_deviation_pct=0.01,
    )

    assert result["action"] == "repriced"
    assert result["new_price"] == 103800
    assert kis_client.revisions == [
        {
            "original_order_no": "00012345",
            "revise_or_cancel_code": "01",
            "quantity": 11,
            "price": 103800,
            "order_type": OrderType.LIMIT,
            "exchange_id": "KRX",
            "original_org_no": "06010",
            "all_quantity": True,
        }
    ]
    assert repository.status_updates[-1][2]["broker_order_no"] == "54321"
    assert repository.events[-1]["event_type"] == "REPRICED"


def test_cancel_order_cancels_only_the_unfilled_remainder() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(
        FakeSettings(),
        cancelable_orders={
            "output": [
                {
                    "ord_gno_brno": "06010",
                    "odno": "00012345",
                    "orgn_odno": "",
                    "pdno": "069500",
                    "psbl_qty": "2",
                    "sll_buy_dvsn_cd": "01",
                    "excg_id_dvsn_cd": "KRX",
                }
            ]
        },
    )
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    result = service.cancel_order(
        _stored_order(
            side="SELL",
            symbol="069500",
            order_type="LIMIT",
            quantity=2,
            price=108550,
            status="SENT",
            broker_order_no="00012345",
            broker_org_order_no="06010",
        )
    )

    assert result["action"] == "canceled"
    assert kis_client.revisions == [
        {
            "original_order_no": "00012345",
            "revise_or_cancel_code": "02",
            "quantity": 2,
            "price": 0,
            "order_type": OrderType.LIMIT,
            "exchange_id": "KRX",
            "original_org_no": "06010",
            "all_quantity": True,
        }
    ]
    assert repository.status_updates[-1][1] == OrderStatus.CANCELED
    assert repository.events[-1]["event_type"] == "CANCELED"


def test_cancel_order_leaves_status_when_broker_has_no_cancelable_remainder() -> None:
    repository = FakeRepository()
    service = OrderService(  # type: ignore[arg-type]
        repository=repository,
        kis_client=FakeKISClient(FakeSettings()),
    )

    result = service.cancel_order(
        _stored_order(status="SENT", broker_order_no="00012345")
    )

    assert result["action"] == "not_cancelable"
    assert not repository.status_updates


def test_reprice_buy_order_does_not_chase_quote_beyond_bound() -> None:
    repository = FakeRepository()
    kis_client = FakeKISClient(
        FakeSettings(),
        asking_price={"output1": {"askp1": "102000", "bidp1": "101900"}},
    )
    service = OrderService(repository=repository, kis_client=kis_client)  # type: ignore[arg-type]

    result = service.reprice_order(
        _stored_order(
            order_type="LIMIT",
            price=100000,
            status="SENT",
            broker_order_no="00012345",
        ),
        anchor_price=100000,
        max_deviation_pct=0.01,
    )

    assert result["action"] == "no_change"
    assert result["price_bound"] == 101000
    assert not kis_client.revisions


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


def _reconciliation_service(**overrides: Any) -> tuple[OrderService, FakeRepository, dict[str, Any]]:
    order = _stored_order(
        status="SENT", broker_order_no="0000091", quantity=2,
        filled_quantity=0, remaining_quantity=None, **overrides,
    )
    repo = FakeRepository()
    repo.execution_orders = [order]
    return OrderService(repo, FakeKISClient(FakeSettings())), repo, order  # type: ignore[arg-type]


def test_oct16_zero_fill_rejection_is_terminal_and_repeatable() -> None:
    import json

    service, repo, order = _reconciliation_service()
    payload = {"rt_cd": "0", "output1": [{
        "odno": "0000091", "pdno": "005930", "ord_qty": "2",
        "tot_ccld_qty": "0", "rjct_qty": "2", "rmn_qty": "0",
    }]}
    result = service.sync_daily_executions(trade_date=datetime(2026, 10, 16).date(), response=payload)
    assert order["status"] == "REJECTED"
    assert order["filled_quantity"] == 0
    assert order["closed_at"] is not None
    assert order["error_code"] == "BROKER_REJECTED"
    assert result["orders"][0]["active_remaining_quantity"] == 0
    assert repo.events[0]["request_payload"]["before"]["status"] == "SENT"
    assert repo.events[0]["response_payload"]["after"]["status"] == "REJECTED"
    json.dumps(repo.events[0]["response_payload"])
    repeated = service.sync_daily_executions(trade_date=datetime(2026, 10, 16).date(), response=payload)
    assert repeated["synced_order_ids"] == []
    assert len(repo.events) == 1
    final = service.finalize_closed_session(trade_date=datetime(2026, 10, 16).date(), response=payload)
    assert final["finalized_order_ids"] == []
    assert order["status"] == "REJECTED"


@pytest.mark.parametrize("terminal", ["reject", "cancel"])
def test_partial_fill_terminal_remainder_preserves_execution(terminal: str) -> None:
    service, repo, order = _reconciliation_service()
    order.update(quantity=5, filled_quantity=2, status="PARTIALLY_FILLED", avg_fill_price="208000")
    row = {"odno": "0000091", "pdno": "005930", "ord_qty": "5", "tot_ccld_qty": "2", "rmn_qty": "0"}
    if terminal == "reject":
        row["rjct_qty"] = "3"
    else:
        row.update(odno="0000092", orgn_odno="0000091", ord_qty="3", tot_ccld_qty="0", sll_buy_dvsn_cd_name="매수취소*")
    response = {"output1": [row]}
    service.sync_daily_executions(trade_date=datetime(2026, 10, 16).date(), response=response)
    assert order["status"] == ("REJECTED" if terminal == "reject" else "CANCELED")
    assert order["filled_quantity"] == 2
    assert order["avg_fill_price"] == "208000"
    assert order["remaining_quantity"] == 3
    assert len(repo.events) == 1
    service.sync_daily_executions(trade_date=datetime(2026, 10, 16).date(), response=response)
    assert len(repo.events) == 1


def test_partial_cancel_and_rejected_cancel_do_not_close_the_order() -> None:
    service, repo, order = _reconciliation_service()
    for row in [
        {"odno": "0000092", "orgn_odno": "0000091", "sll_buy_dvsn_cd_name": "매수취소*", "rmn_qty": "1", "rjct_qty": "0"},
        {"odno": "0000092", "orgn_odno": "0000091", "sll_buy_dvsn_cd_name": "매수취소*", "rmn_qty": "0", "rjct_qty": "2"},
    ]:
        service.sync_daily_executions(trade_date=datetime(2026, 10, 16).date(), response={"output1": [{**row, "pdno": "005930"}]})
    assert order["status"] == "SENT"
    assert not repo.events


@pytest.mark.parametrize("payload", [None, {"output1": []}, {"rt_cd": "1", "output1": []}, {"output1": [{"odno": "0000091", "pdno": "005930", "rmn_qty": "0", "tot_ccld_qty": "0"}]}])
def test_closed_session_leaves_missing_or_ambiguous_order_for_review(payload: Any) -> None:
    service, repo, order = _reconciliation_service()
    result = service.finalize_closed_session(trade_date=datetime(2026, 10, 16).date(), response=payload)
    assert result["finalized_order_ids"] == []
    assert result["review_order_ids"] == [order["id"]]
    assert order["status"] == "SENT"
    assert not repo.events


def test_failed_execution_query_does_not_change_ledger() -> None:
    service, repo, _ = _reconciliation_service()
    with pytest.raises(ConfigError, match="successful daily execution response"):
        service.sync_daily_executions(trade_date=datetime(2026, 10, 16).date(), response={"rt_cd": "1", "output1": []})
    assert not repo.status_updates


def test_revised_partial_fills_are_cumulative_and_idempotent() -> None:
    service, repo, order = _reconciliation_service()
    order.update(quantity=11, filled_quantity=5, avg_fill_price="104000", status="PARTIALLY_FILLED")
    for reported, expected in [(2, 7), (2, 7), (5, 10), (6, 11), (6, 11)]:
        result = service.sync_daily_executions(trade_date=datetime(2026, 10, 16).date(), response={"output1": [{
            "odno": "0000091", "pdno": "005930", "ord_qty": "6",
            "tot_ccld_qty": str(reported), "avg_prvs": "103800",
        }]})
        assert result["orders"][0]["filled_quantity"] == expected
    assert len(repo.events) == 3
    assert order["status"] == "FILLED"
    assert float(order["avg_fill_price"]) == pytest.approx((5 * 104000 + 6 * 103800) / 11)


def test_target_submission_and_fill_quantities_are_reported_separately() -> None:
    service, _, order = _reconciliation_service()
    order.update(requested_quantity="9", filled_quantity=0)
    summary = service._execution_summary(order)
    assert (summary["requested_quantity"], summary["submitted_quantity"], summary["filled_quantity"]) == (9, 2, 0)


def test_canceled_parent_snapshot_can_report_a_previously_unseen_fill() -> None:
    service, _, order = _reconciliation_service()
    service.sync_daily_executions(trade_date=datetime(2026, 10, 16).date(), response={"output1": [{
        "odno": "0000091", "pdno": "005930", "ord_qty": "2", "tot_ccld_qty": "1",
        "avg_prvs": "208000", "rmn_qty": "0", "cncl_yn": "Y",
    }]})
    assert order["status"] == "CANCELED"
    assert order["filled_quantity"] == 1
    assert order["remaining_quantity"] == 1
    assert order["avg_fill_price"] is not None


def test_same_order_number_from_another_date_is_not_reconciled() -> None:
    service, repo, _ = _reconciliation_service()
    service.sync_daily_executions(trade_date=datetime(2026, 10, 16).date(), response={"output1": [{
        "odno": "0000091", "pdno": "005930", "ord_dt": "20261015", "ord_tmd": "090040",
        "ord_qty": "2", "tot_ccld_qty": "0", "rjct_qty": "2", "rmn_qty": "0",
    }]})
    assert not repo.events


def test_rejected_remainder_with_zero_reported_fills_keeps_known_executions() -> None:
    service, _, order = _reconciliation_service()
    order.update(quantity=5, filled_quantity=2, avg_fill_price="208000", status="PARTIALLY_FILLED")
    service.sync_daily_executions(trade_date=datetime(2026, 10, 16).date(), response={"output1": [{
        "odno": "0000091", "pdno": "005930", "ord_qty": "5", "tot_ccld_qty": "0",
        "rjct_qty": "3", "rmn_qty": "0",
    }]})
    assert order["status"] == "REJECTED"
    assert order["filled_quantity"] == 2
    assert order["avg_fill_price"] == "208000"
    assert order["remaining_quantity"] == 3
