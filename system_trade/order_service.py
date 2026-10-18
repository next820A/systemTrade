from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any

from .account_records import buying_power_from_response, cancelable_rows_from_response, execution_rows_from_response
from .domain import OrderRequest, OrderStatus, OrderType, Side, status_from_fill
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

    def sync_daily_executions(
        self,
        *,
        trade_date: date,
        response: dict[str, Any],
        query_request_id: int | None = None,
    ) -> dict[str, Any]:
        settings = self._kis_client._settings
        settings.require_account()
        orders = self._repository.list_orders_for_account_trade_date(
            account_no=str(settings.kis_account_no),
            trade_date=trade_date,
        )
        orders_by_broker_no = {
            str(order["broker_order_no"]): order
            for order in orders
            if order.get("broker_order_no")
        }

        if response.get("rt_cd", "0") != "0" or not isinstance(response.get("output1"), list):
            raise ConfigError("A successful daily execution response is required for reconciliation.")

        synced_order_ids: set[int] = set()
        for execution in execution_rows_from_response(response):
            if execution.get("order_date") and execution["order_date"] != trade_date.strftime("%Y%m%d"):
                continue
            broker_order_no = str(execution.get("broker_order_no") or "")
            order = orders_by_broker_no.get(broker_order_no)
            # A confirmed cancellation has its own order number. Link it only by
            # the broker's explicit original number, never by symbol alone.
            if order is None and execution.get("is_cancel"):
                order = orders_by_broker_no.get(str(execution.get("original_broker_order_no") or ""))
            if order is None or (execution.get("symbol") and execution["symbol"] != order.get("symbol")):
                continue
            is_cancel = bool(execution.get("is_cancel"))
            is_cancel_child = is_cancel and broker_order_no != str(order.get("broker_order_no"))
            rejected_quantity = max(int(execution.get("rejected_quantity") or 0), 0)
            # Rejecting a cancel request does not reject the underlying order.
            if is_cancel and rejected_quantity:
                continue

            order_id = int(order["id"])
            ordered_quantity = int(order.get("quantity") or 0)
            if ordered_quantity <= 0:
                continue
            existing_filled_quantity = int(order.get("filled_quantity") or 0)
            reported_filled_quantity = max(int(execution.get("filled_quantity") or 0), 0)
            reported_order_quantity = int(execution.get("order_quantity") or 0)
            progress = self._repository.get_order_sync_progress(order_id, broker_order_no)
            base_quantity = int(progress.get("base_filled_quantity") or 0)
            base_avg_price = progress.get("base_avg_fill_price")
            if not progress and not is_cancel and (
                existing_filled_quantity > 0
                and 0 < reported_order_quantity <= ordered_quantity - existing_filled_quantity
            ):
                base_quantity = existing_filled_quantity
                base_avg_price = order.get("avg_fill_price")
            filled_quantity = min(base_quantity + reported_filled_quantity, ordered_quantity)
            if is_cancel_child:
                filled_quantity = existing_filled_quantity
            if filled_quantity < existing_filled_quantity:
                confirms_closed_remainder = execution.get("remaining_quantity") == 0 and (
                    is_cancel or rejected_quantity >= max(ordered_quantity - existing_filled_quantity, 1)
                )
                if not confirms_closed_remainder:
                    continue
                # A terminal remainder may report no fills of its own. Preserve
                # executions already known from previous broker responses.
                filled_quantity = existing_filled_quantity
            advanced = filled_quantity > existing_filled_quantity
            avg_fill_price = order.get("avg_fill_price")
            if advanced:
                avg_fill_price = execution.get("fill_price")
                if base_quantity:
                    avg_fill_price = _weighted_avg_fill_price(
                        existing_quantity=base_quantity,
                        existing_avg_price=base_avg_price,
                        new_quantity=reported_filled_quantity,
                        new_avg_price=execution.get("fill_price"),
                    )

            broker_remaining = execution.get("remaining_quantity")
            confirmed_rejected = not is_cancel and rejected_quantity > 0 and (
                broker_remaining == 0
                or (broker_remaining is None and rejected_quantity >= max(reported_order_quantity - reported_filled_quantity, 1))
            )
            confirmed_canceled = is_cancel and broker_remaining == 0
            status = status_from_fill(ordered_quantity, filled_quantity)
            if filled_quantity < ordered_quantity:
                if confirmed_rejected:
                    status = OrderStatus.REJECTED
                elif confirmed_canceled:
                    status = OrderStatus.CANCELED
                elif str(order.get("status")) in {"FILLED", "CANCELED", "REJECTED", "FAILED"}:
                    status = OrderStatus(str(order["status"]))
            needs_terminal_details = (confirmed_rejected or confirmed_canceled) and (
                order.get("remaining_quantity") != max(ordered_quantity - filled_quantity, 0)
                or order.get("closed_at") is None
            )
            if not advanced and status.value == str(order.get("status")) and not needs_terminal_details:
                continue
            # A zero-fill row without explicit terminal evidence is not proof
            # that an order was canceled, rejected, or even accepted.
            if not advanced and not (confirmed_rejected or confirmed_canceled):
                continue

            terminal = status in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED}
            synced_at = _utcnow_naive()
            error_code = order.get("error_code") if status == OrderStatus.REJECTED else None
            error_message = order.get("error_message") if status == OrderStatus.REJECTED else None
            if confirmed_rejected:
                error_code = "BROKER_REJECTED"
                error_message = execution.get("rejection_reason") or "KIS daily execution reports rejected quantity; detailed reason unavailable"
            updates = {
                "filled_quantity": filled_quantity,
                "remaining_quantity": max(ordered_quantity - filled_quantity, 0),
                "avg_fill_price": avg_fill_price,
                "closed_at": (order.get("closed_at") or synced_at) if terminal else None,
                "last_synced_at": synced_at,
                "error_code": error_code,
                "error_message": error_message,
            }
            before = {key: _json_scalar(order.get(key)) for key in ("status", "filled_quantity", "remaining_quantity", "avg_fill_price", "closed_at", "error_code", "error_message")}
            event = dict(
                event_type="FILL_SYNCED" if advanced and not (confirmed_rejected or confirmed_canceled) else "BROKER_STATE_SYNCED",
                request_payload={"before": before},
                response_payload={
                    "query_request_id": query_request_id,
                    "trade_date": trade_date.isoformat(),
                    "broker_order_no": broker_order_no,
                    "original_broker_order_no": execution.get("original_broker_order_no"),
                    "broker_fill_id": execution.get("broker_fill_id"),
                    "filled_quantity": filled_quantity,
                    "reported_filled_quantity": reported_filled_quantity,
                    "rejected_quantity": rejected_quantity,
                    "broker_remaining_quantity": broker_remaining,
                    "is_cancel": is_cancel,
                    "base_filled_quantity": base_quantity,
                    "base_avg_fill_price": _json_scalar(base_avg_price),
                    "fill_price": _json_scalar(avg_fill_price),
                    "fill_amount": _json_scalar(execution.get("fill_amount")),
                    "after": {"status": status.value, **{key: _json_scalar(value) for key, value in updates.items()}},
                },
                note="daily_execution_sync",
                event_at=synced_at,
            )
            applied = self._repository.apply_order_sync(
                order_id=order_id, status=status, updates=updates, event=event,
                expected={key: order.get(key) for key in ("status", "quantity", "filled_quantity", "broker_order_no")},
            )
            if applied:
                order.update(updates, status=status.value)
                synced_order_ids.add(order_id)

        refreshed_orders = self._repository.list_orders_for_account_trade_date(
            account_no=str(settings.kis_account_no), trade_date=trade_date,
        )
        return {
            "trade_date": trade_date.isoformat(),
            "account_alias": settings.account_alias,
            "synced_order_ids": sorted(synced_order_ids),
            "orders": [self._execution_summary(order) for order in refreshed_orders],
        }

    @staticmethod
    def _execution_summary(order: dict[str, Any]) -> dict[str, Any]:
        quantity = int(order.get("quantity") or 0)
        filled = int(order.get("filled_quantity") or 0)
        metadata = _json_object_or_none(order.get("intent_metadata")) or {}
        status = str(order.get("status") or "")
        return {
            "id": int(order["id"]),
            "broker_order_no": order.get("broker_order_no"),
            "side": order.get("side"),
            "symbol": order.get("symbol"),
            "quantity": quantity,
            "requested_quantity": int(order.get("requested_quantity") or metadata.get("target_quantity") or quantity),
            "submitted_quantity": quantity if order.get("broker_order_no") else 0,
            "filled_quantity": filled,
            # remaining_quantity records the unexecuted portion, including a
            # canceled/rejected remainder. It is not an active broker order.
            "remaining_quantity": order.get("remaining_quantity"),
            "active_remaining_quantity": max(quantity - filled, 0) if status in {"SENT", "PARTIALLY_FILLED"} else 0,
            "status": status,
            "error_code": order.get("error_code"),
            "error_message": order.get("error_message"),
        }

    def finalize_closed_session(self, *, trade_date: date, response: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = self._kis_client._settings
        settings.require_account()
        orders = self._repository.list_orders_for_account_trade_date(
            account_no=str(settings.kis_account_no), trade_date=trade_date,
        )
        # Only an explicitly reported open remainder of the closed DAY session
        # can expire. Missing/ambiguous rows require another broker query.
        executions = execution_rows_from_response(response) if response and response.get("rt_cd", "0") == "0" else []
        reported_open = {
            str(row["broker_order_no"]): row for row in executions
            if row.get("broker_order_no") and not row.get("is_cancel")
            and (not row.get("order_date") or row["order_date"] == trade_date.strftime("%Y%m%d"))
            and row.get("remaining_quantity") is not None
            and int(row["remaining_quantity"]) > 0
            and not row.get("rejected_quantity")
        }
        finalized_order_ids: list[int] = []
        review_order_ids: list[int] = []
        finalized_at = _utcnow_naive()
        for order in orders:
            if str(order.get("status")) not in {"SENT", "PARTIALLY_FILLED"}:
                continue
            order_id = int(order["id"])
            evidence = reported_open.get(str(order.get("broker_order_no") or ""))
            if not evidence or evidence.get("symbol") != order.get("symbol"):
                review_order_ids.append(order_id)
                continue
            remaining_quantity = max(int(order.get("quantity") or 0) - int(order.get("filled_quantity") or 0), 0)
            applied = self._repository.apply_order_sync(
                order_id=order_id, status=OrderStatus.CANCELED,
                updates={"remaining_quantity": remaining_quantity, "closed_at": finalized_at, "last_synced_at": finalized_at, "error_code": None, "error_message": None},
                event={
                    "event_type": "SESSION_EXPIRED",
                    "request_payload": {"before_status": order.get("status")},
                    "response_payload": {"remaining_quantity": remaining_quantity, "broker_remaining_quantity": evidence["remaining_quantity"]},
                    "note": "broker_reported_remainder_after_closed_day_session", "event_at": finalized_at,
                },
                expected={key: order.get(key) for key in ("status", "quantity", "filled_quantity", "broker_order_no")},
            )
            (finalized_order_ids if applied else review_order_ids).append(order_id)
        return {
            "trade_date": trade_date.isoformat(), "account_alias": settings.account_alias,
            "finalized_order_ids": finalized_order_ids, "review_order_ids": review_order_ids,
        }

    def reprice_order(
        self,
        order: dict[str, Any],
        *,
        anchor_price: int,
        max_deviation_pct: float,
    ) -> dict[str, Any]:
        settings = self._kis_client._settings
        settings.require_account()
        order_id = int(order["id"])
        status = OrderStatus(str(order.get("status") or ""))
        if status not in {OrderStatus.SENT, OrderStatus.PARTIALLY_FILLED}:
            raise ConfigError(
                "Only SENT/PARTIALLY_FILLED orders can be repriced. "
                f"order_id={order_id}, status={status.value}"
            )
        if str(order.get("order_type") or "") != OrderType.LIMIT.value:
            raise ConfigError(f"Only LIMIT orders can be repriced. order_id={order_id}")
        if not order.get("broker_order_no"):
            raise ConfigError(f"Order has no broker_order_no. order_id={order_id}")
        if anchor_price <= 0:
            raise ConfigError("anchor_price must be positive.")
        if max_deviation_pct < 0:
            raise ConfigError("max_deviation_pct must be non-negative.")

        side = Side(str(order["side"]))
        current_price = int(order.get("price") or anchor_price)
        quote_response = self._kis_client.get_asking_price(str(order["symbol"]))
        quote_price = _marketable_quote_price(quote_response, side=side)
        price_bound_decimal = (
            Decimal(anchor_price) * (Decimal("1") + Decimal(str(max_deviation_pct)))
            if side == Side.BUY
            else Decimal(anchor_price) * (Decimal("1") - Decimal(str(max_deviation_pct)))
        )
        price_bound = int(
            price_bound_decimal.to_integral_value(
                rounding=ROUND_FLOOR if side == Side.BUY else ROUND_CEILING,
            )
        )
        target_price = _bounded_reprice_target(
            side=side,
            current_price=current_price,
            quote_price=quote_price,
            price_bound=price_bound,
        )
        if target_price is None:
            return {
                "action": "no_change",
                "order_id": order_id,
                "side": side.value,
                "symbol": order.get("symbol"),
                "current_order_price": current_price,
                "marketable_quote_price": quote_price,
                "price_bound": price_bound,
            }

        cancelable_response = self._kis_client.get_cancelable_orders(
            query_by="0",
            side_filter="2" if side == Side.BUY else "1",
        )
        cancelable_order = next(
            (
                row
                for row in cancelable_rows_from_response(cancelable_response)
                if str(row.get("broker_order_no") or "") == str(order["broker_order_no"])
            ),
            None,
        )
        if cancelable_order is None:
            return {
                "action": "not_cancelable",
                "order_id": order_id,
                "side": side.value,
                "symbol": order.get("symbol"),
                "current_order_price": current_price,
                "marketable_quote_price": quote_price,
                "price_bound": price_bound,
            }
        remaining_quantity = int(cancelable_order.get("unfilled_quantity") or 0)
        if remaining_quantity <= 0:
            return {
                "action": "no_remaining_quantity",
                "order_id": order_id,
                "side": side.value,
                "symbol": order.get("symbol"),
            }

        response = self._kis_client.revise_or_cancel_order(
            original_order_no=str(cancelable_order["broker_order_no"]),
            revise_or_cancel_code="01",
            quantity=remaining_quantity,
            price=target_price,
            order_type=OrderType.LIMIT,
            exchange_id=str(cancelable_order.get("exchange_id") or "KRX"),
            original_org_no=str(cancelable_order.get("broker_org_order_no") or order.get("broker_org_order_no") or ""),
            all_quantity=True,
        )
        new_broker_order_no, new_broker_org_order_no = extract_order_identifiers(response)
        repriced_at = _utcnow_naive()
        self._repository.update_order_status(
            order_id,
            status,
            price=target_price,
            broker_order_no=new_broker_order_no or order.get("broker_order_no"),
            broker_org_order_no=new_broker_org_order_no or cancelable_order.get("broker_org_order_no"),
            remaining_quantity=remaining_quantity,
            last_synced_at=repriced_at,
            error_code=None,
            error_message=None,
        )
        self._repository.insert_order_event(
            order_id=order_id,
            event_type="REPRICED",
            status=status,
            request_payload={
                "side": side.value,
                "symbol": order.get("symbol"),
                "previous_price": current_price,
                "new_price": target_price,
                "anchor_price": anchor_price,
                "max_deviation_pct": max_deviation_pct,
                "price_bound": price_bound,
                "marketable_quote_price": quote_price,
                "remaining_quantity": remaining_quantity,
                "previous_broker_order_no": order.get("broker_order_no"),
            },
            response_payload=response,
            note="bounded_limit_order_reprice",
            event_at=repriced_at,
        )
        refreshed = self._repository.get_order_by_id(order_id) or {}
        return {
            "action": "repriced",
            "order_id": order_id,
            "previous_price": current_price,
            "new_price": target_price,
            "anchor_price": anchor_price,
            "price_bound": price_bound,
            "marketable_quote_price": quote_price,
            "remaining_quantity": remaining_quantity,
            "order": refreshed,
        }

    def cancel_order(self, order: dict[str, Any]) -> dict[str, Any]:
        settings = self._kis_client._settings
        settings.require_account()
        order_id = int(order["id"])
        status = OrderStatus(str(order.get("status") or ""))
        if status not in {OrderStatus.SENT, OrderStatus.PARTIALLY_FILLED}:
            raise ConfigError(
                "Only SENT/PARTIALLY_FILLED orders can be canceled. "
                f"order_id={order_id}, status={status.value}"
            )
        if not order.get("broker_order_no"):
            raise ConfigError(f"Order has no broker_order_no. order_id={order_id}")

        side = Side(str(order["side"]))
        cancelable_response = self._kis_client.get_cancelable_orders(
            query_by="0",
            side_filter="2" if side == Side.BUY else "1",
        )
        cancelable_order = next(
            (
                row
                for row in cancelable_rows_from_response(cancelable_response)
                if str(row.get("broker_order_no") or "") == str(order["broker_order_no"])
            ),
            None,
        )
        if cancelable_order is None:
            return {
                "action": "not_cancelable",
                "order_id": order_id,
                "side": side.value,
                "symbol": order.get("symbol"),
            }

        remaining_quantity = int(cancelable_order.get("unfilled_quantity") or 0)
        if remaining_quantity <= 0:
            return {
                "action": "no_remaining_quantity",
                "order_id": order_id,
                "side": side.value,
                "symbol": order.get("symbol"),
            }

        response = self._kis_client.revise_or_cancel_order(
            original_order_no=str(cancelable_order["broker_order_no"]),
            revise_or_cancel_code="02",
            quantity=remaining_quantity,
            price=0,
            order_type=OrderType(str(order["order_type"])),
            exchange_id=str(cancelable_order.get("exchange_id") or "KRX"),
            original_org_no=str(
                cancelable_order.get("broker_org_order_no")
                or order.get("broker_org_order_no")
                or ""
            ),
            all_quantity=True,
        )
        canceled_at = _utcnow_naive()
        self._repository.update_order_status(
            order_id,
            OrderStatus.CANCELED,
            closed_at=canceled_at,
            last_synced_at=canceled_at,
            error_code=None,
            error_message=None,
        )
        self._repository.insert_order_event(
            order_id=order_id,
            event_type="CANCELED",
            status=OrderStatus.CANCELED,
            request_payload={
                "side": side.value,
                "symbol": order.get("symbol"),
                "remaining_quantity": remaining_quantity,
                "broker_order_no": order.get("broker_order_no"),
            },
            response_payload=response,
            note="cancel_unfilled_order",
            event_at=canceled_at,
        )
        refreshed = self._repository.get_order_by_id(order_id) or {}
        return {
            "action": "canceled",
            "order_id": order_id,
            "remaining_quantity": remaining_quantity,
            "order": refreshed,
        }

    def _send_order(self, order_id: int, request: OrderRequest, *, event_type: str) -> dict[str, Any]:
        submission_request, adjustment = self._adjust_buy_order(request)
        if adjustment and adjustment.get("blocked"):
            error_code = str(adjustment.get("blocking_error_code") or "BUYING_POWER_EXHAUSTED")
            error_message = str(
                adjustment.get("blocking_error_message")
                or "buying power is insufficient for BUY order"
            )
            self._repository.update_order_status(
                order_id,
                OrderStatus.REJECTED,
                last_synced_at=_utcnow_naive(),
                error_code=error_code,
                error_message=error_message[:255],
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
                note=str(adjustment.get("reason") or "buying_power_blocked_before_submit"),
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

    def _adjust_buy_order(self, request: OrderRequest) -> tuple[OrderRequest, dict[str, Any] | None]:
        if request.side != Side.BUY:
            return request, None

        limit_price = _market_buy_limit_price(request)
        if limit_price is None:
            return request, None

        adjusted_order_type = OrderType.LIMIT

        try:
            buying_power_response = self._kis_client.get_buying_power(
                symbol=request.symbol,
                price=limit_price,
                order_type=adjusted_order_type,
            )
        except KISError as exc:
            return request, {
                "changed": False,
                "blocked": True,
                "reason": "buying_power_check_failed",
                "blocking_error_code": "BUYING_POWER_CHECK_FAILED",
                "blocking_error_message": f"KIS buying-power check failed: {exc.message}",
                "error_code": exc.error_code,
                "error_message": exc.message,
                "original_order_type": request.order_type.value,
                "original_quantity": request.quantity,
                "original_price": request.price,
                "candidate_order_type": adjusted_order_type.value,
                "candidate_price": limit_price,
            }

        capacity = buying_power_from_response(buying_power_response)
        max_quantity = capacity.get("max_buy_quantity")
        if max_quantity is None:
            max_quantity = capacity.get("order_possible_quantity")
        if max_quantity is None:
            return request, {
                "changed": False,
                "blocked": True,
                "reason": "buying_power_quantity_missing",
                "blocking_error_code": "BUYING_POWER_UNAVAILABLE",
                "blocking_error_message": "KIS buying-power response did not include a maximum buy quantity",
                "original_order_type": request.order_type.value,
                "original_quantity": request.quantity,
                "original_price": request.price,
                "candidate_order_type": adjusted_order_type.value,
                "candidate_price": limit_price,
                "buying_power_response": buying_power_response,
            }

        adjusted_quantity = min(request.quantity, max(int(max_quantity), 0))
        adjusted_request = replace(
            request,
            quantity=adjusted_quantity,
            order_type=adjusted_order_type,
            price=limit_price,
        )
        blocked = adjusted_quantity <= 0
        reason = (
            "market_buy_converted_to_limit_with_buying_power_cap"
            if request.order_type == OrderType.MARKET
            else "limit_buy_capped_to_buying_power"
        )
        return adjusted_request, {
            "changed": (
                adjusted_request.quantity != request.quantity
                or adjusted_request.order_type != request.order_type
                or adjusted_request.price != request.price
            ),
            "blocked": blocked,
            "reason": "buying_power_exhausted_before_submit" if blocked else reason,
            "blocking_error_code": "BUYING_POWER_EXHAUSTED" if blocked else None,
            "blocking_error_message": "KIS reports zero buyable quantity for this BUY order" if blocked else None,
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


def _marketable_quote_price(response: dict[str, Any], *, side: Side) -> int | None:
    output = response.get("output1")
    if not isinstance(output, dict):
        output = response.get("output")
    if not isinstance(output, dict):
        return None
    key = "askp1" if side == Side.BUY else "bidp1"
    return _positive_int_price(output.get(key))


def _bounded_reprice_target(
    *,
    side: Side,
    current_price: int,
    quote_price: int | None,
    price_bound: int,
) -> int | None:
    if quote_price is None or quote_price <= 0:
        return None
    if side == Side.BUY:
        if quote_price <= current_price or quote_price > price_bound:
            return None
    elif quote_price >= current_price or quote_price < price_bound:
        return None
    return quote_price


def _weighted_avg_fill_price(
    *,
    existing_quantity: int,
    existing_avg_price: Any,
    new_quantity: int,
    new_avg_price: Any,
) -> Decimal | Any:
    if existing_quantity <= 0 or existing_avg_price is None:
        return new_avg_price
    if new_quantity <= 0 or new_avg_price is None:
        return existing_avg_price
    try:
        existing_price = Decimal(str(existing_avg_price))
        added_price = Decimal(str(new_avg_price))
    except InvalidOperation:
        return new_avg_price
    total_quantity = existing_quantity + new_quantity
    return (
        (existing_price * Decimal(existing_quantity)) + (added_price * Decimal(new_quantity))
    ) / Decimal(total_quantity)


def _json_scalar(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
