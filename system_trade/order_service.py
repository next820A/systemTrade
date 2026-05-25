from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .domain import OrderRequest, OrderStatus, OrderType
from .exceptions import ConfigError, KISError
from .kis_client import KISClient, extract_order_identifiers
from .repository import MySQLRepository


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OrderService:
    def __init__(self, repository: MySQLRepository, kis_client: KISClient):
        self._repository = repository
        self._kis_client = kis_client

    def _validate_and_create_order(self, request: OrderRequest, *, record_only: bool) -> tuple[int | None, dict[str, Any] | None]:
        self._kis_client._settings.require_account()
        settings = self._kis_client._settings
        account_alias = request.account_alias or settings.account_alias
        if request.account_alias and settings.account_alias and request.account_alias != settings.account_alias:
            raise ConfigError(
                f"account_alias mismatch: request={request.account_alias}, env={settings.account_alias}"
            )

        if not account_alias:
            raise ConfigError("account_alias is required. Set SYSTEM_TRADE_ACCOUNT_ALIAS or pass --account-alias.")

        if request.quantity <= 0:
            raise ConfigError("quantity must be positive.")

        if request.order_type == OrderType.LIMIT and (request.price is None or request.price <= 0):
            raise ConfigError("price must be positive for LIMIT order.")

        if not settings.kis_account_no or not settings.kis_acnt_prdt:
            raise ConfigError("KIS_ACCOUNT_NO and KIS_ACNT_PRDT are required for order submission.")

        now = request.requested_at or _utcnow_naive()
        trade_date = request.trade_date or now.date()
        broker = "KIS"
        trade_account = self._repository.find_active_trade_account(
            broker=broker,
            account_alias=account_alias,
            account_no=settings.kis_account_no,
            account_product_code=settings.kis_acnt_prdt,
        )
        if not trade_account:
            raise ConfigError(
                "No active trade_accounts row matches "
                f"broker={broker}, account_alias={account_alias}, account_no={settings.kis_account_no}."
            )

        trade_account_id = int(trade_account["id"])
        account_role = str(trade_account.get("account_role") or "")
        if account_role == "STRATEGY" and not request.strategy_family:
            raise ConfigError("strategy_family is required for STRATEGY accounts.")

        if request.strategy_family:
            allocation = self._repository.find_active_strategy_account_allocation(
                trade_account_id=trade_account_id,
                strategy_family=request.strategy_family,
                allocation_role=request.side.value,
                trade_date=trade_date,
            )
            if not allocation:
                raise ConfigError(
                    "No active strategy_account_allocations row matches "
                    f"strategy_family={request.strategy_family}, account_alias={account_alias}, side={request.side.value}."
                )

        existing = self._repository.get_order_by_idempotency(
            request.idempotency_key,
            account_no=settings.kis_account_no,
            broker=broker,
        )
        if existing:
            return None, existing

        order_id = self._repository.create_order(
            {
                "idempotency_key": request.idempotency_key,
                "source_system": request.source_system,
                "source_run_id": request.source_run_id,
                "broker": broker,
                "trade_account_id": trade_account_id,
                "account_no": settings.kis_account_no,
                "account_product_code": settings.kis_acnt_prdt,
                "account_alias": account_alias,
                "side": request.side.value,
                "source_symbol": request.source_symbol,
                "symbol": request.symbol,
                "order_type": request.order_type.value,
                "quantity": request.quantity,
                "price": request.price,
                "status": OrderStatus.CREATED.value,
                "strategy_id": request.strategy_id,
                "strategy_family": request.strategy_family,
                "strategy_name": request.strategy_name,
                "strategy_version": request.strategy_version,
                "signal_id": request.signal_id,
                "condition_id": request.condition_id,
                "condition_version": request.condition_version,
                "condition_snapshot": request.condition_snapshot,
                "intent_metadata": request.intent_metadata,
                "reason": request.reason,
                "trade_date": trade_date,
                "requested_at": now,
            }
        )

        self._repository.insert_order_event(
            order_id=order_id,
            event_type="CREATED",
            status=OrderStatus.CREATED,
            request_payload={
                "side": request.side.value,
                "symbol": request.symbol,
                "quantity": request.quantity,
                "order_type": request.order_type.value,
                "price": request.price,
                "strategy_id": request.strategy_id,
                "strategy_family": request.strategy_family,
                "strategy_name": request.strategy_name,
                "strategy_version": request.strategy_version,
                "trade_account_id": trade_account_id,
                "account_alias": account_alias,
                "source_system": request.source_system,
                "source_run_id": request.source_run_id,
                "source_symbol": request.source_symbol,
                "signal_id": request.signal_id,
                "condition_id": request.condition_id,
                "condition_version": request.condition_version,
                "condition_snapshot": request.condition_snapshot,
                "intent_metadata": request.intent_metadata,
                "reason": request.reason,
            },
            response_payload=None,
            note="record_only_no_broker_submit" if record_only else None,
            event_at=now,
        )
        return order_id, None

    def record_order_intent(self, request: OrderRequest) -> dict[str, Any]:
        order_id, existing = self._validate_and_create_order(request, record_only=True)
        if existing:
            return existing
        assert order_id is not None
        return self._repository.get_order_by_id(order_id) or {}

    def submit_order(self, request: OrderRequest) -> dict[str, Any]:
        order_id, existing = self._validate_and_create_order(request, record_only=False)
        if existing:
            return existing
        assert order_id is not None

        self._repository.update_order_status(order_id, OrderStatus.SUBMITTING, last_synced_at=_utcnow_naive())
        self._repository.insert_order_event(
            order_id=order_id,
            event_type="SUBMITTING",
            status=OrderStatus.SUBMITTING,
            request_payload=None,
            response_payload=None,
            note=None,
            event_at=_utcnow_naive(),
        )

        try:
            response = self._kis_client.place_cash_order(
                side=request.side,
                symbol=request.symbol,
                quantity=request.quantity,
                order_type=request.order_type,
                price=request.price,
            )
        except KISError as exc:
            self._repository.update_order_status(
                order_id,
                OrderStatus.REJECTED,
                last_synced_at=_utcnow_naive(),
                error_code=exc.error_code,
                error_message=exc.message[:255],
            )
            self._repository.insert_order_event(
                order_id=order_id,
                event_type="REJECTED",
                status=OrderStatus.REJECTED,
                request_payload=None,
                response_payload=exc.payload,
                note=exc.message,
                event_at=_utcnow_naive(),
            )
            return self._repository.get_order_by_id(order_id) or {}

        broker_order_no, broker_org_order_no = extract_order_identifiers(response)

        self._repository.update_order_status(
            order_id,
            OrderStatus.SENT,
            broker_order_no=broker_order_no,
            broker_org_order_no=broker_org_order_no,
            sent_at=_utcnow_naive(),
            last_synced_at=_utcnow_naive(),
            error_code=None,
            error_message=None,
        )
        self._repository.insert_order_event(
            order_id=order_id,
            event_type="SENT",
            status=OrderStatus.SENT,
            request_payload=None,
            response_payload=response,
            note=None,
            event_at=_utcnow_naive(),
        )

        return self._repository.get_order_by_id(order_id) or {}
