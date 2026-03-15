from system_trade.domain import OrderStatus, status_from_fill


def test_status_from_fill_none() -> None:
    assert status_from_fill(10, 0) == OrderStatus.SENT


def test_status_from_fill_partial() -> None:
    assert status_from_fill(10, 3) == OrderStatus.PARTIALLY_FILLED


def test_status_from_fill_done() -> None:
    assert status_from_fill(10, 10) == OrderStatus.FILLED
