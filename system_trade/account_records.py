from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def first_dict(value: Any) -> dict[str, Any]:
    rows = as_list(value)
    return rows[0] if rows else as_dict(value)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def decimal_or_none(value: Any) -> Decimal | None:
    text = clean_text(value)
    if text is None:
        return None
    normalized = text.replace(",", "")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def int_or_none(value: Any) -> int | None:
    parsed = decimal_or_none(value)
    return int(parsed) if parsed is not None else None


def pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if clean_text(value) is not None:
            return value
    return None


def side_or_none(value: Any) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    upper = text.upper()
    if upper in {"BUY", "2", "02"} or "매수" in text:
        return "BUY"
    if upper in {"SELL", "1", "01"} or "매도" in text:
        return "SELL"
    return None


def balance_summary_from_response(payload: dict[str, Any]) -> dict[str, Any]:
    output = first_dict(payload.get("output2"))
    return {
        "cash_total": decimal_or_none(pick(output, "dnca_tot_amt")),
        "cash_available": decimal_or_none(pick(output, "ord_psbl_cash", "nxdy_excc_amt", "dnca_tot_amt")),
        "cash_withdrawable": decimal_or_none(pick(output, "prvs_rcdl_excc_amt", "nxdy_excc_amt")),
        "purchase_amount": decimal_or_none(pick(output, "pchs_amt_smtl_amt")),
        "securities_value": decimal_or_none(pick(output, "scts_evlu_amt", "evlu_amt_smtl_amt")),
        "total_value": decimal_or_none(pick(output, "tot_evlu_amt", "nass_amt", "bfdy_tot_asst_evlu_amt")),
        "eval_pnl": decimal_or_none(pick(output, "evlu_pfls_smtl_amt", "asst_icdc_amt")),
        "raw_payload": output or None,
    }


def holding_rows_from_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    holdings = []
    for row in as_list(payload.get("output1")):
        symbol = clean_text(pick(row, "pdno", "PDNO", "stck_shrn_iscd", "symbol"))
        if symbol is None:
            continue
        holdings.append(
            {
                "symbol": symbol,
                "product_name": clean_text(pick(row, "prdt_name", "PRDT_NAME", "hts_kor_isnm")),
                "quantity": int_or_none(pick(row, "hldg_qty", "HLDG_QTY", "quantity")) or 0,
                "available_quantity": int_or_none(pick(row, "ord_psbl_qty", "ORD_PSBL_QTY")),
                "avg_price": decimal_or_none(pick(row, "pchs_avg_pric", "PCHS_AVG_PRIC", "avg_price")),
                "current_price": decimal_or_none(pick(row, "prpr", "now_pric", "current_price")),
                "purchase_amount": decimal_or_none(pick(row, "pchs_amt", "purchase_amount")),
                "evaluation_amount": decimal_or_none(pick(row, "evlu_amt", "evaluation_amount")),
                "pnl": decimal_or_none(pick(row, "evlu_pfls_amt", "pnl")),
                "pnl_rate": decimal_or_none(pick(row, "evlu_pfls_rt", "pnl_rate")),
                "raw_payload": row,
            }
        )
    return holdings


def buying_power_from_response(payload: dict[str, Any]) -> dict[str, Any]:
    output = as_dict(payload.get("output"))
    return {
        "max_buy_amount": decimal_or_none(pick(output, "max_buy_amt")),
        "max_buy_quantity": int_or_none(pick(output, "max_buy_qty")),
        "order_possible_cash": decimal_or_none(pick(output, "ord_psbl_cash")),
        "order_possible_quantity": int_or_none(pick(output, "max_buy_qty", "nrcvb_buy_qty")),
        "raw_payload": output or None,
    }


def sellable_from_response(payload: dict[str, Any]) -> dict[str, Any]:
    output = as_dict(payload.get("output"))
    return {
        "order_possible_quantity": int_or_none(pick(output, "ord_psbl_qty")),
        "sellable_quantity": int_or_none(pick(output, "ord_psbl_qty", "sll_qty", "nsvg_qty")),
        "holding_quantity": int_or_none(pick(output, "cblc_qty", "buy_qty")),
        "current_price": decimal_or_none(pick(output, "now_pric")),
        "raw_payload": output or None,
    }


def cancelable_rows_from_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in as_list(payload.get("output")):
        rows.append(
            {
                "broker_order_no": clean_text(pick(row, "odno", "ODNO", "ord_no")),
                "broker_org_order_no": clean_text(pick(row, "orgn_odno", "ORGN_ODNO")),
                "side": side_or_none(pick(row, "sll_buy_dvsn_cd", "sll_buy_dvsn_cd_name", "ord_dvsn_name")),
                "symbol": clean_text(pick(row, "pdno", "PDNO")),
                "order_type": clean_text(pick(row, "ord_dvsn_cd", "ord_dvsn_name")),
                "order_quantity": int_or_none(pick(row, "ord_qty", "ORD_QTY")),
                "unfilled_quantity": int_or_none(pick(row, "rmn_qty", "tot_ccld_qty", "unfilled_qty")),
                "order_price": decimal_or_none(pick(row, "ord_unpr", "ORD_UNPR")),
                "raw_payload": row,
            }
        )
    return rows


def execution_rows_from_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in as_list(payload.get("output1")):
        rows.append(
            {
                "broker_order_no": clean_text(pick(row, "odno", "ODNO", "ord_no")),
                "broker_fill_id": clean_text(pick(row, "ccld_no", "exec_no", "broker_fill_id")),
                "side": side_or_none(pick(row, "sll_buy_dvsn_cd", "sll_buy_dvsn_cd_name")),
                "symbol": clean_text(pick(row, "pdno", "PDNO")),
                "order_quantity": int_or_none(pick(row, "ord_qty", "ORD_QTY")),
                "filled_quantity": int_or_none(pick(row, "tot_ccld_qty", "ccld_qty")),
                "fill_price": decimal_or_none(pick(row, "avg_prvs", "ccld_unpr")),
                "fill_amount": decimal_or_none(pick(row, "tot_ccld_amt", "ccld_amt")),
                "raw_payload": row,
            }
        )
    return rows
