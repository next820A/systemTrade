from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .account_records import buying_power_from_response
from .domain import OrderRequest, OrderStatus, OrderType, Side
from .exceptions import ConfigError, KISError
from .kis_client import KISClient, extract_order_identifiers
from .repository import MySQLRepository
from .telegram_notifier import notify_order_event


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

        return self._send_order(order_id, request, event_type="SUBMITTING")

    def resubmit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        settings = self._kis_client._settings
        settings.require_account()
        order_id = int(order["id"])
        status = str(order.get("status") or "")
        if status not in {
            OrderStatus.CREATED.value,
            OrderStatus.REJECTED.value,
            OrderStatus.FAILED.value,
        }:
            raise ConfigError(
                "Only CREATED/REJECTED/FAILED orders can be resubmitted. "
                f"order_id={order_id}, status={status}"
            )
        if order.get("sent_at") or order.get("broker_order_no"):
            raise ConfigError(f"Order already has broker submission markers. order_id={order_id}")

        broker = str(order.get("broker") or "KIS")
        account_alias = str(order.get("account_alias") or settings.account_alias or "")
        if broker != "KIS":
            raise ConfigError(f"Only KIS orders can be resubmitted. order_id={order_id}, broker={broker}")
        if not account_alias:
            raise ConfigError(f"account_alias is required for resubmission. order_id={order_id}")
        if str(order.get("account_no") or "") != str(settings.kis_account_no or ""):
            raise ConfigError(f"order account does not match runtime account. order_id={order_id}")
        if str(order.get("account_product_code") or "") != str(settings.kis_acnt_prdt or ""):
            raise ConfigError(f"order product code does not match runtime account. order_id={order_id}")
        if settings.account_alias and account_alias != settings.account_alias:
            raise ConfigError(
                f"account_alias mismatch: order={account_alias}, runtime={settings.account_alias}"
            )

        trade_date = _date_from_value(order.get("trade_date")) or _utcnow_naive().date()
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
        if int(order.get("trade_account_id") or trade_account_id) != trade_account_id:
            raise ConfigError(f"trade_account_id no longer matches active account. order_id={order_id}")

        account_role = str(trade_account.get("account_role") or "")
        strategy_family = _str_or_none(order.get("strategy_family"))
        if account_role == "STRATEGY" and not strategy_family:
            raise ConfigError("strategy_family is required for STRATEGY accounts.")
        if strategy_family:
            side = Side(str(order["side"]))
            allocation = self._repository.find_active_strategy_account_allocation(
                trade_account_id=trade_account_id,
                strategy_family=strategy_family,
                allocation_role=side.value,
                trade_date=trade_date,
            )
            if not allocation:
                raise ConfigError(
                    "No active strategy_account_allocations row matches "
                    f"strategy_family={strategy_family}, account_alias={account_alias}, side={side.value}."
                )
        else:
            side = Side(str(order["side"]))

        request = OrderRequest(
            side=side,
            symbol=str(order["symbol"]),
            quantity=int(order["quantity"]),
            order_type=OrderType(str(order["order_type"])),
            price=int(order["price"]) if order.get("price") is not None else None,
            idempotency_key=str(order["idempotency_key"]),
            strategy_id=int(order["strategy_id"]) if order.get("strategy_id") is not None else None,
            strategy_family=strategy_family,
            strategy_name=_str_or_none(order.get("strategy_name")),
            strategy_version=_str_or_none(order.get("strategy_version")),
            reason=_str_or_none(order.get("reason")),
            account_alias=account_alias,
            source_system=str(order.get("source_system") or "systemTrade"),
            source_run_id=_str_or_none(order.get("source_run_id")),
            source_symbol=_str_or_none(order.get("source_symbol")),
            signal_id=_str_or_none(order.get("signal_id")),
            condition_id=_str_or_none(order.get("condition_id")),
            condition_version=_str_or_none(order.get("condition_version")),
            condition_snapshot=_json_object_or_none(order.get("condition_snapshot")),
            intent_metadata=_json_object_or_none(order.get("intent_metadata")),
            trade_date=trade_date,
        )
        return self._send_order(order_id, request, event_type="RESUBMITTING")

    def _send_order(self, order_id: int, request: OrderRequest, *, event_type: str) -> dict[str, Any]:
        submission_request, adjustment = self._adjust_market_buy_order(request)
        if adjustment and int(adjustment.get("adjusted_quantity") or 0) <= 0:
            self._repository.update_order_status(
                order_id,
                OrderStatus.REJECTED,
                last_synced_at=_utcnow_naive(),
                error_code="BUYING_POWER_EXHAUSTED",
                error_message="buying power is insufficient for adjusted LIMIT BUY order",
            )
            self._repository.insert_order_event(
                order_id=order_id,
                event_type="REJECTED",
                status=OrderStatus.REJECTED,
                request_payload={
                    "side": request.side.value,
                    "symbol": request.symbol,
                    "quantity": request.quantity,
                    "order_type": request.order_type.value,
                    "price": request.price,
                    "adjustment": adjustment,
                },
                response_payload=adjustment.get("buying_power_response"),
                note="buying_power_exhausted_before_submit",
                event_at=_utcnow_naive(),
            )
            rejected_order = self._repository.get_order_by_id(order_id) or {}
            notify_order_event(rejected_order, event_type="REJECTED")
            return rejected_order

        status_updates: dict[str, Any] = {
            "last_synced_at": _utcnow_naive(),
            "error_code": None,
            "error_message": None,
        }
        if adjustment and adjustment.get("changed"):
            status_updates.update(
                {
                    "order_type": submission_request.order_type.value,
                    "quantity": submission_request.quantity,
                    "price": submission_request.price,
                }
            )
        self._repository.update_order_status(
            order_id,
            OrderStatus.SUBMITTING,
            **status_updates,
        )
        self._repository.insert_order_event(
            order_id=order_id,
            event_type=event_type,
            status=OrderStatus.SUBMITTING,
            request_payload={
                "side": submission_request.side.value,
                "symbol": submission_request.symbol,
                "quantity": submission_request.quantity,
                "order_type": submission_request.order_type.value,
                "price": submission_request.price,
                "idempotency_key": submission_request.idempotency_key,
                "account_alias": submission_request.account_alias,
                "source_system": submission_request.source_system,
                "source_run_id": submission_request.source_run_id,
                "adjustment": adjustment,
            },
            response_payload=None,
            note=None,
            event_at=_utcnow_naive(),
        )

        try:
            response = self._kis_client.place_cash_order(
                side=submission_request.side,
                symbol=submission_request.symbol,
                quantity=submission_request.quantity,
                order_type=submission_request.order_type,
                price=submission_request.price,
            )
        except KISError as exc:
            error_payload = _kis_error_payload(exc)
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
                response_payload=error_payload,
                note=exc.message,
                event_at=_utcnow_naive(),
            )
            rejected_order = self._repository.get_order_by_id(order_id) or {}
            rejected_order.update(_order_attempt_notification_fields(error_payload))
            notify_order_event(rejected_order, event_type="REJECTED")
            return rejected_order

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

        sent_order = self._repository.get_order_by_id(order_id) or {}
        sent_order.update(_order_attempt_notification_fields(response))
        notify_order_event(sent_order, event_type="SENT")
        return sent_order

    def _adjust_market_buy_order(self, request: OrderRequest) -> tuple[OrderRequest, dict[str, Any] | None]:
        if request.side != Side.BUY or request.order_type != OrderType.MARKET:
            return request, None

        limit_price = _market_buy_limit_price(request)
        if limit_price is None:
            return request, None

        try:
            buying_power_response = self._kis_client.get_buying_power(
                symbol=request.symbol,
                price=limit_price,
                order_type=OrderType.LIMIT,
            )
        except KISError as exc:
            return request, {
                "changed": False,
                "reason": "buying_power_check_failed",
                "error_code": exc.error_code,
                "error_message": exc.message,
                "original_order_type": request.order_type.value,
                "original_quantity": request.quantity,
                "candidate_order_type": OrderType.LIMIT.value,
                "candidate_price": limit_price,
            }

        capacity = buying_power_from_response(buying_power_response)
        max_quantity = capacity.get("max_buy_quantity") or capacity.get("order_possible_quantity")
        adjusted_quantity = request.quantity if max_quantity is None else min(request.quantity, int(max_quantity))
        adjusted_request = replace(
            request,
            quantity=adjusted_quantity,
            order_type=OrderType.LIMIT,
            price=limit_price,
        )
        return adjusted_request, {
            "changed": (
                adjusted_request.quantity != request.quantity
                or adjusted_request.order_type != request.order_type
                or adjusted_request.price != request.price
            ),
            "reason": "market_buy_converted_to_limit_with_buying_power_cap",
            "original_order_type": request.order_type.value,
            "original_quantity": request.quantity,
            "original_price": request.price,
            "adjusted_order_type": adjusted_request.order_type.value,
            "adjusted_quantity": adjusted_request.quantity,
            "adjusted_price": adjusted_request.price,
            "max_buy_quantity": max_quantity,
            "max_buy_amount": _json_scalar(capacity.get("max_buy_amount")),
            "order_possible_cash": _json_scalar(capacity.get("order_possible_cash")),
            "buying_power_response": buying_power_response,
        }


def _str_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None


def _date_from_value(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _json_object_or_none(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    import json

    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, dict) else None


def _kis_error_payload(exc: KISError) -> dict[str, Any]:
    payload = dict(exc.payload or {})
    metadata = payload.get("_systemtrade")
    if not isinstance(metadata, dict):
        payload["_systemtrade"] = {"order_tr_id": exc.tr_id, "order_tr_id_attempts": [exc.tr_id]}
    return payload


def _order_attempt_notification_fields(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("_systemtrade") if isinstance(payload, dict) else None
    if not isinstance(metadata, dict):
        return {}
    fields: dict[str, Any] = {}
    if metadata.get("order_tr_id"):
        fields["kis_order_tr_id"] = metadata.get("order_tr_id")
    if metadata.get("order_tr_id_attempts"):
        fields["kis_order_tr_id_attempts"] = metadata.get("order_tr_id_attempts")
    return fields


def _market_buy_limit_price(request: OrderRequest) -> int | None:
    if request.price is not None and request.price > 0:
        return int(request.price)

    candidates: list[Any] = []
    for payload in (request.intent_metadata, request.condition_snapshot):
        if not isinstance(payload, dict):
            continue
        candidates.extend(
            [
                payload.get("limit_price"),
                payload.get("reference_price"),
                payload.get("buy_threshold_price"),
            ]
        )
        features = payload.get("features")
        if isinstance(features, dict):
            candidates.extend(
                [
                    features.get("limit_price"),
                    features.get("reference_price"),
                    features.get("buy_threshold_price"),
                ]
            )

    for candidate in candidates:
        parsed = _positive_int_price(candidate)
        if parsed is not None:
            return parsed
    return None


def _positive_int_price(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        price = int(Decimal(str(value).replace(",", "")))
    except (InvalidOperation, ValueError):
        return None
    return price if price > 0 else None


def _json_scalar(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return value
