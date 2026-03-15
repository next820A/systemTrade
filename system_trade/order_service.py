from __future__ import annotations

from datetime import datetime
from typing import Any

from .domain import OrderRequest, OrderStatus
from .exceptions import KISError
from .kis_client import KISClient, extract_order_identifiers
from .repository import MySQLRepository


class OrderService:
    def __init__(self, repository: MySQLRepository, kis_client: KISClient):
        self._repository = repository
        self._kis_client = kis_client

    def submit_order(self, request: OrderRequest) -> dict[str, Any]:
        self._kis_client._settings.require_account()

        existing = self._repository.get_order_by_idempotency(request.idempotency_key)
        if existing:
            return existing

        now = request.requested_at or datetime.utcnow()
        trade_date = now.date()

        order_id = self._repository.create_order(
            {
                "idempotency_key": request.idempotency_key,
                "account_no": self._kis_client._settings.kis_account_no,
                "side": request.side.value,
                "symbol": request.symbol,
                "order_type": request.order_type.value,
                "quantity": request.quantity,
                "price": request.price,
                "status": OrderStatus.CREATED.value,
                "strategy_id": request.strategy_id,
                "strategy_name": request.strategy_name,
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
                "strategy_name": request.strategy_name,
                "reason": request.reason,
            },
            response_payload=None,
            note=None,
            event_at=now,
        )

        self._repository.update_order_status(order_id, OrderStatus.SUBMITTING, last_synced_at=datetime.utcnow())
        self._repository.insert_order_event(
            order_id=order_id,
            event_type="SUBMITTING",
            status=OrderStatus.SUBMITTING,
            request_payload=None,
            response_payload=None,
            note=None,
            event_at=datetime.utcnow(),
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
                last_synced_at=datetime.utcnow(),
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
                event_at=datetime.utcnow(),
            )
            return self._repository.get_order_by_id(order_id) or {}

        broker_order_no, broker_org_order_no = extract_order_identifiers(response)

        self._repository.update_order_status(
            order_id,
            OrderStatus.SENT,
            broker_order_no=broker_order_no,
            broker_org_order_no=broker_org_order_no,
            sent_at=datetime.utcnow(),
            last_synced_at=datetime.utcnow(),
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
            event_at=datetime.utcnow(),
        )

        return self._repository.get_order_by_id(order_id) or {}
