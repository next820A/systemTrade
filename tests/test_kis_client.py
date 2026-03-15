from system_trade.config import Settings
from system_trade.kis_client import extract_order_identifiers


def test_extract_order_identifiers_uppercase() -> None:
    response = {
        "output": {
            "ODNO": "12345",
            "KRX_FWDG_ORD_ORGNO": "67890",
        }
    }
    order_no, org_no = extract_order_identifiers(response)
    assert order_no == "12345"
    assert org_no == "67890"


def test_extract_order_identifiers_missing_output() -> None:
    order_no, org_no = extract_order_identifiers({})
    assert order_no is None
    assert org_no is None


def test_kis_client_uses_fixed_real_tr_ids() -> None:
    from system_trade.kis_client import KISClient

    settings = Settings(
        kis_app_key="key",
        kis_app_secret="secret",
        kis_paper=False,
        kis_base_url="https://openapi.koreainvestment.com:9443",
        kis_account_no="63611886",
        kis_acnt_prdt="01",
        db_host="127.0.0.1",
        db_port=3306,
        db_user="root",
        db_password="",
        db_name="trade",
    )

    client = KISClient(settings)

    assert client.tr_ids.order_buy == "TTTC0012U"
    assert client.tr_ids.order_sell == "TTTC0011U"
    assert client.tr_ids.order_rvsecncl == "TTTC0013U"
    assert client.tr_ids.balance == "TTTC8434R"
