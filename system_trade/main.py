from __future__ import annotations

import argparse
import json
from datetime import datetime

import pymysql

from .config import Settings
from .domain import OrderRequest, OrderType, Side
from .exceptions import ConfigError, KISError
from .kis_client import KISClient
from .order_service import OrderService
from .repository import MySQLRepository


def _new_stack() -> tuple[Settings, KISClient, MySQLRepository, OrderService]:
    settings = Settings.load()
    kis_client = KISClient(settings)
    repository = MySQLRepository(settings)
    service = OrderService(repository=repository, kis_client=kis_client)
    return settings, kis_client, repository, service


def cmd_health_check(args: argparse.Namespace) -> int:
    _, kis_client, _, _ = _new_stack()
    token = kis_client.get_access_token()
    price = kis_client.get_current_price(args.symbol)
    output = price.get("output", {})

    result = {
        "token_prefix": token[:12] + "...",
        "symbol": args.symbol,
        "current_price": output.get("stck_prpr"),
        "base_date": output.get("stck_bsop_date"),
        "base_time": output.get("stck_cntg_hour"),
        "rt_cd": price.get("rt_cd"),
        "msg1": price.get("msg1"),
    }
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_init_db(args: argparse.Namespace) -> int:
    settings, _, repository, _ = _new_stack()
    migration = settings.migration_path()
    repository.apply_migration_file(migration)
    print(f"applied migration: {migration}")
    return 0


def _parse_side(value: str) -> Side:
    return Side[value.upper()]


def _parse_order_type(value: str) -> OrderType:
    return OrderType[value.upper()]


def cmd_balance(args: argparse.Namespace) -> int:
    _, kis_client, _, _ = _new_stack()
    payload = kis_client.get_stock_balance()
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_daily_ccld(args: argparse.Namespace) -> int:
    _, kis_client, _, _ = _new_stack()
    payload = kis_client.get_daily_ccld(
        start_date=args.start_date,
        end_date=args.end_date,
        query_scope=args.query_scope,
        side_filter=args.side_filter,
        symbol=args.symbol or "",
        fill_filter=args.fill_filter,
        sort_order=args.sort_order,
        asset_filter=args.asset_filter,
    )
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_buying_power(args: argparse.Namespace) -> int:
    _, kis_client, _, _ = _new_stack()
    payload = kis_client.get_buying_power(
        symbol=args.symbol,
        price=args.price,
        order_type=_parse_order_type(args.order_type),
    )
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_sellable(args: argparse.Namespace) -> int:
    _, kis_client, _, _ = _new_stack()
    payload = kis_client.get_sellable_quantity(symbol=args.symbol)
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_cancelable_orders(args: argparse.Namespace) -> int:
    _, kis_client, _, _ = _new_stack()
    payload = kis_client.get_cancelable_orders(
        query_by=args.query_by,
        side_filter=args.side_filter,
    )
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_tr_ids(args: argparse.Namespace) -> int:
    settings, kis_client, _, _ = _new_stack()
    payload = {
        "kis_paper": settings.kis_paper,
        "kis_base_url": settings.kis_base_url,
        "tr_ids": kis_client.tr_ids.__dict__,
    }
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_order(args: argparse.Namespace) -> int:
    settings, _, repository, service = _new_stack()
    settings.require_account()
    if not settings.kis_paper and not args.allow_live:
        raise ConfigError("Live order is blocked by default. Re-run with --allow-live if intended.")
    repository.ping()

    idempotency_key = args.idempotency_key or f"{args.side.upper()}-{args.symbol}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    request = OrderRequest(
        side=_parse_side(args.side),
        symbol=args.symbol,
        quantity=args.qty,
        order_type=_parse_order_type(args.order_type),
        price=args.price,
        idempotency_key=idempotency_key,
        strategy_id=args.strategy_id,
        strategy_name=args.strategy,
        reason=args.reason,
    )
    order = service.submit_order(request)
    print(json.dumps(order, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_list_orders(args: argparse.Namespace) -> int:
    _, _, repository, _ = _new_stack()
    rows = repository.list_orders(limit=args.limit)
    print(json.dumps(rows, ensure_ascii=False, default=str, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="systemTrade CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health-check", help="check KIS token and quote API")
    health.add_argument("--symbol", default="005930")
    health.set_defaults(func=cmd_health_check)

    tr_ids = sub.add_parser("tr-ids", help="show the fixed KIS TR ID set")
    tr_ids.set_defaults(func=cmd_tr_ids)

    init_db = sub.add_parser("init-db", help="apply mysql migration")
    init_db.set_defaults(func=cmd_init_db)

    balance = sub.add_parser("balance", help="query account balance and holdings")
    balance.set_defaults(func=cmd_balance)

    daily_ccld = sub.add_parser("daily-ccld", help="query daily order/execution history")
    daily_ccld.add_argument("--start-date", required=True, help="YYYYMMDD")
    daily_ccld.add_argument("--end-date", required=True, help="YYYYMMDD")
    daily_ccld.add_argument("--query-scope", choices=["inner", "before"], default="inner")
    daily_ccld.add_argument("--side-filter", default="00", help="00 all, 01 sell, 02 buy")
    daily_ccld.add_argument("--symbol", default="")
    daily_ccld.add_argument("--fill-filter", default="00", help="00 all, 01 filled, 02 open")
    daily_ccld.add_argument("--sort-order", default="00", help="00 desc, 01 asc")
    daily_ccld.add_argument("--asset-filter", default="00")
    daily_ccld.set_defaults(func=cmd_daily_ccld)

    buying_power = sub.add_parser("buying-power", help="query buyable amount/quantity")
    buying_power.add_argument("--symbol", required=True)
    buying_power.add_argument("--price", type=int, required=True)
    buying_power.add_argument("--order-type", required=True, choices=["MARKET", "LIMIT", "market", "limit"])
    buying_power.set_defaults(func=cmd_buying_power)

    sellable = sub.add_parser("sellable", help="query sellable quantity")
    sellable.add_argument("--symbol", required=True)
    sellable.set_defaults(func=cmd_sellable)

    cancelable = sub.add_parser("cancelable-orders", help="query revise/cancelable orders")
    cancelable.add_argument("--query-by", default="1", help="0 order, 1 symbol")
    cancelable.add_argument("--side-filter", default="0", help="0 all, 1 sell, 2 buy")
    cancelable.set_defaults(func=cmd_cancelable_orders)

    order = sub.add_parser("order", help="submit an order")
    order.add_argument("--side", required=True, choices=["BUY", "SELL", "buy", "sell"])
    order.add_argument("--symbol", required=True)
    order.add_argument("--qty", type=int, required=True)
    order.add_argument("--order-type", required=True, choices=["MARKET", "LIMIT", "market", "limit"])
    order.add_argument("--price", type=int)
    order.add_argument("--idempotency-key")
    order.add_argument("--strategy-id", type=int)
    order.add_argument("--strategy")
    order.add_argument("--reason")
    order.add_argument("--allow-live", action="store_true")
    order.set_defaults(func=cmd_order)

    list_orders = sub.add_parser("list-orders", help="show recent orders")
    list_orders.add_argument("--limit", type=int, default=20)
    list_orders.set_defaults(func=cmd_list_orders)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"[config] {exc}")
        return 1
    except KISError as exc:
        print(str(exc))
        return 1
    except pymysql.err.OperationalError as exc:
        print(f"[db] {exc}")
        print(
            "[db] Check SYSTEM_TRADE_DB_HOST/PORT/USER/PASSWORD/NAME "
            "and make sure MySQL is running."
        )
        return 1
    except Exception as exc:
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
