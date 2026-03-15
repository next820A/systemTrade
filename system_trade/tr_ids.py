from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradingTRIDs:
    order_buy: str
    order_sell: str
    order_rvsecncl: str
    daily_ccld_inner: str
    daily_ccld_before: str
    balance: str
    buying_power: str
    sellable: str
    cancelable_orders: str
    fill_notice: str


REAL_TR_IDS = TradingTRIDs(
    order_buy="TTTC0012U",
    order_sell="TTTC0011U",
    order_rvsecncl="TTTC0013U",
    daily_ccld_inner="TTTC0081R",
    daily_ccld_before="CTSC9215R",
    balance="TTTC8434R",
    buying_power="TTTC8908R",
    sellable="TTTC8408R",
    cancelable_orders="TTTC0084R",
    fill_notice="H0STCNI0",
)


DEMO_TR_IDS = TradingTRIDs(
    order_buy="VTTC0012U",
    order_sell="VTTC0011U",
    order_rvsecncl="VTTC0013U",
    daily_ccld_inner="VTTC0081R",
    daily_ccld_before="VTSC9215R",
    balance="VTTC8434R",
    buying_power="VTTC8908R",
    sellable="VTTC8408R",
    cancelable_orders="VTTC0084R",
    fill_notice="H0STCNI9",
)
