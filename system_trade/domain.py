from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    SUBMITTING = "SUBMITTING"
    SENT = "SENT"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class OrderRequest:
    side: Side
    symbol: str
    quantity: int
    order_type: OrderType
    price: int | None
    idempotency_key: str
    strategy_id: int | None = None
    strategy_name: str | None = None
    reason: str | None = None
    requested_at: datetime | None = None


def status_from_fill(ordered_qty: int, filled_qty: int) -> OrderStatus:
    if filled_qty <= 0:
        return OrderStatus.SENT
    if filled_qty < ordered_qty:
        return OrderStatus.PARTIALLY_FILLED
    return OrderStatus.FILLED
