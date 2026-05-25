from decimal import Decimal

from system_trade.account_records import (
    balance_summary_from_response,
    buying_power_from_response,
    holding_rows_from_response,
    sellable_from_response,
)


def test_balance_summary_from_kis_output2() -> None:
    payload = {
        "output1": [],
        "output2": [
            {
                "dnca_tot_amt": "100000",
                "nxdy_excc_amt": "95000",
                "prvs_rcdl_excc_amt": "90000",
                "pchs_amt_smtl_amt": "120000",
                "scts_evlu_amt": "130000",
                "tot_evlu_amt": "220000",
                "evlu_pfls_smtl_amt": "10000",
            }
        ],
    }

    summary = balance_summary_from_response(payload)

    assert summary["cash_total"] == Decimal("100000")
    assert summary["cash_available"] == Decimal("95000")
    assert summary["cash_withdrawable"] == Decimal("90000")
    assert summary["purchase_amount"] == Decimal("120000")
    assert summary["securities_value"] == Decimal("130000")
    assert summary["total_value"] == Decimal("220000")
    assert summary["eval_pnl"] == Decimal("10000")


def test_holding_rows_from_kis_output1() -> None:
    payload = {
        "output1": [
            {
                "pdno": "005930",
                "prdt_name": "삼성전자",
                "hldg_qty": "3",
                "ord_psbl_qty": "2",
                "pchs_avg_pric": "70000.00",
                "prpr": "72000",
            }
        ]
    }

    rows = holding_rows_from_response(payload)

    assert rows == [
        {
            "symbol": "005930",
            "product_name": "삼성전자",
            "quantity": 3,
            "available_quantity": 2,
            "avg_price": Decimal("70000.00"),
            "current_price": Decimal("72000"),
            "purchase_amount": None,
            "evaluation_amount": None,
            "pnl": None,
            "pnl_rate": None,
            "raw_payload": payload["output1"][0],
        }
    ]


def test_buying_power_from_kis_output() -> None:
    payload = {
        "output": {
            "max_buy_amt": "1,000,000",
            "max_buy_qty": "14",
            "ord_psbl_cash": "900000",
        }
    }

    capacity = buying_power_from_response(payload)

    assert capacity["max_buy_amount"] == Decimal("1000000")
    assert capacity["max_buy_quantity"] == 14
    assert capacity["order_possible_cash"] == Decimal("900000")
    assert capacity["order_possible_quantity"] == 14


def test_sellable_from_kis_output() -> None:
    payload = {
        "output": {
            "ord_psbl_qty": "7",
            "cblc_qty": "10",
            "now_pric": "72000",
        }
    }

    capacity = sellable_from_response(payload)

    assert capacity["sellable_quantity"] == 7
    assert capacity["holding_quantity"] == 10
    assert capacity["current_price"] == Decimal("72000")
