from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime

import pymysql

from .account_records import (
    balance_summary_from_response,
    buying_power_from_response,
    cancelable_rows_from_response,
    execution_rows_from_response,
    holding_rows_from_response,
    sellable_from_response,
)
from .config import Settings
from .domain import OrderRequest, OrderType, Side
from .exceptions import ConfigError, KISError
from .kis_client import KISClient
from .order_service import OrderService
from .repository import MySQLRepository


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _new_stack() -> tuple[Settings, KISClient, MySQLRepository, OrderService]:
    settings = Settings.load()
    kis_client = KISClient(settings)
    repository = MySQLRepository(settings)
    service = OrderService(repository=repository, kis_client=kis_client)
    return settings, kis_client, repository, service


def _new_order_stack(*, require_kis: bool = True) -> tuple[Settings, KISClient, MySQLRepository, OrderService]:
    settings = Settings.load(require_kis=require_kis)
    kis_client = KISClient(settings)
    repository = MySQLRepository(settings)
    service = OrderService(repository=repository, kis_client=kis_client)
    return settings, kis_client, repository, service


def _new_repository() -> tuple[Settings, MySQLRepository]:
    settings = Settings.load(require_kis=False)
    repository = MySQLRepository(settings)
    return settings, repository


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
    settings, repository = _new_repository()
    repository.ensure_database()
    for migration in settings.migration_paths():
        applied = repository.apply_migration_file(migration)
        status = "applied" if applied else "skipped"
        print(f"{status} migration: {migration}")
    return 0


def _mask_account_no(account_no: str | None) -> str | None:
    if not account_no:
        return None
    return "***" + account_no[-4:]


def cmd_bind_account(args: argparse.Namespace) -> int:
    settings, repository = _new_repository()
    settings.require_account()
    repository.ensure_database()
    row = repository.bind_trade_account_alias(
        account_alias=args.account_alias,
        account_no=str(settings.kis_account_no),
        account_product_code=str(settings.kis_acnt_prdt),
    )
    output = dict(row)
    output["account_no"] = _mask_account_no(output.get("account_no"))
    print(json.dumps(output, ensure_ascii=False, default=str, indent=2))
    return 0


def _parse_side(value: str) -> Side:
    return Side[value.upper()]


def _parse_order_type(value: str) -> OrderType:
    return OrderType[value.upper()]


def _parse_json_object(value: str | None, field_name: str) -> dict | None:
    if value is None or value.strip() == "":
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{field_name} must be a valid JSON object: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError(f"{field_name} must be a JSON object.")
    return parsed


def _parse_yyyymmdd(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _save_query_request(
    repository: MySQLRepository,
    settings: Settings,
    query_type: str,
    request_payload: dict,
    response_payload: dict,
    requested_at: datetime,
    responded_at: datetime,
    **metadata: object,
) -> int:
    settings.require_account()
    if metadata.get("account_alias") is None:
        metadata["account_alias"] = settings.account_alias
    account_alias = metadata.get("account_alias")
    if account_alias:
        trade_account = repository.find_active_trade_account(
            account_alias=str(account_alias),
            account_no=str(settings.kis_account_no),
            account_product_code=str(settings.kis_acnt_prdt),
        )
        if not trade_account:
            raise ConfigError(
                "No active trade_accounts row matches "
                f"account_alias={account_alias}, account_no={settings.kis_account_no}."
            )
        metadata["trade_account_id"] = int(trade_account["id"])
    return repository.create_account_query_request(
        {
            "account_no": settings.kis_account_no,
            "account_product_code": settings.kis_acnt_prdt,
            "query_type": query_type,
            "status": "SUCCEEDED",
            "rt_cd": response_payload.get("rt_cd"),
            "msg_cd": response_payload.get("msg_cd"),
            "msg1": response_payload.get("msg1"),
            "request_payload": request_payload,
            "response_payload": response_payload,
            "requested_at": requested_at,
            "responded_at": responded_at,
            **metadata,
        }
    )


def cmd_balance(args: argparse.Namespace) -> int:
    settings, kis_client, repository, _ = _new_stack()
    requested_at = _utcnow_naive()
    payload = kis_client.get_stock_balance()
    responded_at = _utcnow_naive()
    if args.save:
        query_request_id = _save_query_request(
            repository,
            settings,
            "BALANCE",
            request_payload={"command": "balance"},
            response_payload=payload,
            requested_at=requested_at,
            responded_at=responded_at,
            account_alias=args.account_alias,
        )
        summary = balance_summary_from_response(payload)
        repository.insert_account_balance_summary(
            {
                **summary,
                "query_request_id": query_request_id,
                "account_no": settings.kis_account_no,
                "as_of": responded_at,
            }
        )
        holdings = [
            {
                **row,
                "query_request_id": query_request_id,
                "account_no": settings.kis_account_no,
                "as_of": responded_at,
            }
            for row in holding_rows_from_response(payload)
        ]
        repository.insert_account_holding_snapshots(holdings)
        print(f"[db] saved account_query_requests.id={query_request_id}")
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_daily_ccld(args: argparse.Namespace) -> int:
    settings, kis_client, repository, _ = _new_stack()
    requested_at = _utcnow_naive()
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
    responded_at = _utcnow_naive()
    if args.save:
        query_request_id = _save_query_request(
            repository,
            settings,
            "DAILY_CCLD",
            request_payload={
                "command": "daily-ccld",
                "start_date": args.start_date,
                "end_date": args.end_date,
                "query_scope": args.query_scope,
                "side_filter": args.side_filter,
                "symbol": args.symbol or "",
                "fill_filter": args.fill_filter,
                "sort_order": args.sort_order,
                "asset_filter": args.asset_filter,
            },
            response_payload=payload,
            requested_at=requested_at,
            responded_at=responded_at,
            symbol=args.symbol or None,
            account_alias=args.account_alias,
            start_date=_parse_yyyymmdd(args.start_date),
            end_date=_parse_yyyymmdd(args.end_date),
        )
        rows = [
            {
                **row,
                "query_request_id": query_request_id,
                "account_no": settings.kis_account_no,
            }
            for row in execution_rows_from_response(payload)
        ]
        repository.insert_broker_execution_snapshots(rows)
        print(f"[db] saved account_query_requests.id={query_request_id}")
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_buying_power(args: argparse.Namespace) -> int:
    settings, kis_client, repository, _ = _new_stack()
    requested_at = _utcnow_naive()
    payload = kis_client.get_buying_power(
        symbol=args.symbol,
        price=args.price,
        order_type=_parse_order_type(args.order_type),
    )
    responded_at = _utcnow_naive()
    if args.save:
        query_request_id = _save_query_request(
            repository,
            settings,
            "BUYING_POWER",
            request_payload={
                "command": "buying-power",
                "symbol": args.symbol,
                "price": args.price,
                "order_type": args.order_type,
            },
            response_payload=payload,
            requested_at=requested_at,
            responded_at=responded_at,
            symbol=args.symbol,
            account_alias=args.account_alias,
            order_type=_parse_order_type(args.order_type).value,
            price=args.price,
        )
        capacity = buying_power_from_response(payload)
        repository.insert_order_capacity_snapshot(
            {
                **capacity,
                "query_request_id": query_request_id,
                "account_no": settings.kis_account_no,
                "capacity_type": "BUYING_POWER",
                "symbol": args.symbol,
                "order_type": _parse_order_type(args.order_type).value,
                "price": args.price,
                "as_of": responded_at,
            }
        )
        print(f"[db] saved account_query_requests.id={query_request_id}")
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_sellable(args: argparse.Namespace) -> int:
    settings, kis_client, repository, _ = _new_stack()
    requested_at = _utcnow_naive()
    payload = kis_client.get_sellable_quantity(symbol=args.symbol)
    responded_at = _utcnow_naive()
    if args.save:
        query_request_id = _save_query_request(
            repository,
            settings,
            "SELLABLE",
            request_payload={"command": "sellable", "symbol": args.symbol},
            response_payload=payload,
            requested_at=requested_at,
            responded_at=responded_at,
            symbol=args.symbol,
            account_alias=args.account_alias,
        )
        capacity = sellable_from_response(payload)
        repository.insert_order_capacity_snapshot(
            {
                **capacity,
                "query_request_id": query_request_id,
                "account_no": settings.kis_account_no,
                "capacity_type": "SELLABLE",
                "symbol": args.symbol,
                "as_of": responded_at,
            }
        )
        print(f"[db] saved account_query_requests.id={query_request_id}")
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_cancelable_orders(args: argparse.Namespace) -> int:
    settings, kis_client, repository, _ = _new_stack()
    requested_at = _utcnow_naive()
    payload = kis_client.get_cancelable_orders(
        query_by=args.query_by,
        side_filter=args.side_filter,
    )
    responded_at = _utcnow_naive()
    if args.save:
        query_request_id = _save_query_request(
            repository,
            settings,
            "CANCELABLE_ORDERS",
            request_payload={
                "command": "cancelable-orders",
                "query_by": args.query_by,
                "side_filter": args.side_filter,
            },
            response_payload=payload,
            requested_at=requested_at,
            responded_at=responded_at,
            account_alias=args.account_alias,
        )
        rows = [
            {
                **row,
                "query_request_id": query_request_id,
                "account_no": settings.kis_account_no,
            }
            for row in cancelable_rows_from_response(payload)
        ]
        repository.insert_cancelable_order_snapshots(rows)
        print(f"[db] saved account_query_requests.id={query_request_id}")
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_tr_ids(args: argparse.Namespace) -> int:
    settings = Settings.load(require_kis=False)
    kis_client = KISClient(settings)
    payload = {
        "kis_paper": settings.kis_paper,
        "kis_base_url": settings.kis_base_url,
        "tr_ids": kis_client.tr_ids.__dict__,
    }
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_order(args: argparse.Namespace) -> int:
    settings, _, repository, service = _new_order_stack(require_kis=not args.record_only)
    settings.require_account()
    if not args.record_only and not settings.kis_paper and not args.allow_live:
        raise ConfigError("Live order is blocked by default. Re-run with --allow-live if intended.")
    repository.ping()

    idempotency_key = args.idempotency_key or f"{args.side.upper()}-{args.symbol}-{_utcnow_naive().strftime('%Y%m%d%H%M%S')}"
    request = OrderRequest(
        side=_parse_side(args.side),
        symbol=args.symbol,
        quantity=args.qty,
        order_type=_parse_order_type(args.order_type),
        price=args.price,
        idempotency_key=idempotency_key,
        strategy_id=args.strategy_id,
        strategy_family=args.strategy_family,
        strategy_name=args.strategy,
        strategy_version=args.strategy_version,
        reason=args.reason,
        account_alias=args.account_alias,
        source_system=args.source_system,
        source_run_id=args.source_run_id,
        source_symbol=args.source_symbol,
        signal_id=args.signal_id,
        condition_id=args.condition_id,
        condition_version=args.condition_version,
        condition_snapshot=_parse_json_object(args.condition_snapshot_json, "--condition-snapshot-json"),
        intent_metadata=_parse_json_object(args.intent_metadata_json, "--intent-metadata-json"),
        trade_date=date.fromisoformat(args.trade_date) if args.trade_date else None,
    )
    order = service.record_order_intent(request) if args.record_only else service.submit_order(request)
    print(json.dumps(order, ensure_ascii=False, default=str, indent=2))
    return 0


def cmd_list_orders(args: argparse.Namespace) -> int:
    _, repository = _new_repository()
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

    bind_account = sub.add_parser("bind-account", help="bind current KIS account env to a trade account alias")
    bind_account.add_argument("--account-alias", required=True)
    bind_account.set_defaults(func=cmd_bind_account)

    balance = sub.add_parser("balance", help="query account balance and holdings")
    balance.add_argument("--account-alias")
    balance.add_argument("--save", action="store_true", help="persist sanitized query records to MySQL")
    balance.set_defaults(func=cmd_balance)

    daily_ccld = sub.add_parser("daily-ccld", help="query daily order/execution history")
    daily_ccld.add_argument("--start-date", required=True, help="YYYYMMDD")
    daily_ccld.add_argument("--end-date", required=True, help="YYYYMMDD")
    daily_ccld.add_argument("--query-scope", choices=["inner", "before"], default="inner")
    daily_ccld.add_argument("--side-filter", default="00", help="00 all, 01 sell, 02 buy")
    daily_ccld.add_argument("--symbol", default="")
    daily_ccld.add_argument("--account-alias")
    daily_ccld.add_argument("--fill-filter", default="00", help="00 all, 01 filled, 02 open")
    daily_ccld.add_argument("--sort-order", default="00", help="00 desc, 01 asc")
    daily_ccld.add_argument("--asset-filter", default="00")
    daily_ccld.add_argument("--save", action="store_true", help="persist sanitized query records to MySQL")
    daily_ccld.set_defaults(func=cmd_daily_ccld)

    buying_power = sub.add_parser("buying-power", help="query buyable amount/quantity")
    buying_power.add_argument("--symbol", required=True)
    buying_power.add_argument("--account-alias")
    buying_power.add_argument("--price", type=int, required=True)
    buying_power.add_argument("--order-type", required=True, choices=["MARKET", "LIMIT", "market", "limit"])
    buying_power.add_argument("--save", action="store_true", help="persist sanitized query records to MySQL")
    buying_power.set_defaults(func=cmd_buying_power)

    sellable = sub.add_parser("sellable", help="query sellable quantity")
    sellable.add_argument("--symbol", required=True)
    sellable.add_argument("--account-alias")
    sellable.add_argument("--save", action="store_true", help="persist sanitized query records to MySQL")
    sellable.set_defaults(func=cmd_sellable)

    cancelable = sub.add_parser("cancelable-orders", help="query revise/cancelable orders")
    cancelable.add_argument("--account-alias")
    cancelable.add_argument("--query-by", default="1", help="0 order, 1 symbol")
    cancelable.add_argument("--side-filter", default="0", help="0 all, 1 sell, 2 buy")
    cancelable.add_argument("--save", action="store_true", help="persist sanitized query records to MySQL")
    cancelable.set_defaults(func=cmd_cancelable_orders)

    order = sub.add_parser("order", help="submit an order")
    order.add_argument("--side", required=True, choices=["BUY", "SELL", "buy", "sell"])
    order.add_argument("--symbol", required=True)
    order.add_argument("--qty", type=int, required=True)
    order.add_argument("--order-type", required=True, choices=["MARKET", "LIMIT", "market", "limit"])
    order.add_argument("--price", type=int)
    order.add_argument("--idempotency-key")
    order.add_argument("--strategy-id", type=int)
    order.add_argument("--strategy-family")
    order.add_argument("--strategy")
    order.add_argument("--strategy-version")
    order.add_argument("--reason")
    order.add_argument("--account-alias")
    order.add_argument("--source-system", default="systemTrade")
    order.add_argument("--source-run-id")
    order.add_argument("--source-symbol")
    order.add_argument("--signal-id")
    order.add_argument("--condition-id")
    order.add_argument("--condition-version")
    order.add_argument("--condition-snapshot-json")
    order.add_argument("--intent-metadata-json")
    order.add_argument("--trade-date", help="intended exchange session date in YYYY-MM-DD format")
    order.add_argument("--record-only", action="store_true", help="persist the order intent without sending it to KIS")
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
