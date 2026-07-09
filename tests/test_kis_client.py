import json
import time

from system_trade.config import Settings
from system_trade.domain import OrderType, Side
from system_trade.exceptions import KISError
from system_trade.kis_client import extract_order_identifiers


def _settings(app_key: str = "key") -> Settings:
    return Settings(
        kis_app_key=app_key,
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


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self) -> dict[str, object]:
        return self._payload


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

    client = KISClient(_settings())

    assert client.tr_ids.order_buy == "TTTC0802U"
    assert client.tr_ids.order_sell == "TTTC0801U"
    assert client.tr_ids.order_buy_fallback == "TTTC0012U"
    assert client.tr_ids.order_sell_fallback == "TTTC0011U"
    assert client.tr_ids.order_rvsecncl == "TTTC0013U"
    assert client.tr_ids.balance == "TTTC8434R"


def test_kis_client_uses_fixed_demo_cash_order_tr_ids() -> None:
    from dataclasses import replace

    from system_trade.kis_client import KISClient

    client = KISClient(
        replace(
            _settings(),
            kis_paper=True,
            kis_base_url="https://openapivts.koreainvestment.com:29443",
        )
    )

    assert client.tr_ids.order_buy == "VTTC0802U"
    assert client.tr_ids.order_sell == "VTTC0801U"
    assert client.tr_ids.order_buy_fallback == "VTTC0012U"
    assert client.tr_ids.order_sell_fallback == "VTTC0011U"


def test_kis_client_token_cache_is_scoped_to_app_key() -> None:
    from dataclasses import replace

    from system_trade.kis_client import KISClient

    settings = _settings(app_key="key-a")

    first = KISClient(settings)
    second = KISClient(replace(settings, kis_app_key="key-b"))

    assert first._token_cache_file != second._token_cache_file


def test_get_access_token_reloads_cache_before_issuing(monkeypatch, tmp_path) -> None:
    import system_trade.kis_client as kis_client_module

    monkeypatch.setenv("HOME", str(tmp_path))

    client = kis_client_module.KISClient(_settings(app_key="shared-key"))
    client._token_cache_file.write_text(
        json.dumps(
            {
                "access_token": "cached-token",
                "token_expire_ts": time.time() + 3600,
            }
        ),
        encoding="utf-8",
    )

    def fail_post(*args, **kwargs):
        raise AssertionError("token endpoint should not be called when cache is valid")

    monkeypatch.setattr(kis_client_module.requests, "post", fail_post)

    assert client.get_access_token() == "cached-token"


def test_get_access_token_retries_kis_token_rate_limit(monkeypatch, tmp_path) -> None:
    import system_trade.kis_client as kis_client_module

    monkeypatch.setenv("HOME", str(tmp_path))
    sleeps: list[int] = []
    responses = iter(
        [
            _FakeResponse(
                429,
                {"error_description": "접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)"},
            ),
            _FakeResponse(200, {"access_token": "fresh-token", "expires_in": 86400}),
        ]
    )

    def fake_post(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(kis_client_module.requests, "post", fake_post)
    monkeypatch.setattr(kis_client_module.time, "sleep", lambda seconds: sleeps.append(seconds))

    client = kis_client_module.KISClient(_settings(app_key="retry-key"))

    assert client.get_access_token() == "fresh-token"
    assert sleeps == [kis_client_module.TOKEN_RATE_LIMIT_RETRY_SEC]


def test_place_cash_order_falls_back_when_kis_rejects_primary_tr_id() -> None:
    from system_trade.kis_client import KISClient

    client = KISClient(_settings())
    calls: list[str] = []

    def fake_request(method, path, tr_id, params=None, body=None, use_hashkey=False):
        calls.append(tr_id)
        if len(calls) == 1:
            raise KISError(tr_id, "실전투자 TR 이 아닙니다.", error_code="EGW02005")
        return {"rt_cd": "0", "output": {"ODNO": "12345"}}

    client._request = fake_request  # type: ignore[method-assign]

    response = client.place_cash_order(
        side=Side.BUY,
        symbol="005930",
        quantity=1,
        order_type=OrderType.MARKET,
        price=None,
    )

    assert calls == ["TTTC0802U", "TTTC0012U"]
    assert response["output"]["ODNO"] == "12345"
    assert response["_systemtrade"]["order_tr_id"] == "TTTC0012U"
    assert response["_systemtrade"]["order_tr_id_attempts"] == ["TTTC0802U", "TTTC0012U"]


def test_place_cash_order_records_attempts_when_fallback_is_also_rejected() -> None:
    from system_trade.kis_client import KISClient

    client = KISClient(_settings())
    calls: list[str] = []

    def fake_request(method, path, tr_id, params=None, body=None, use_hashkey=False):
        calls.append(tr_id)
        raise KISError(tr_id, "실전투자 TR 이 아닙니다.", payload={"msg_cd": "EGW02005"}, error_code="EGW02005")

    client._request = fake_request  # type: ignore[method-assign]

    try:
        client.place_cash_order(
            side=Side.BUY,
            symbol="005930",
            quantity=1,
            order_type=OrderType.MARKET,
            price=None,
        )
    except KISError as exc:
        assert exc.tr_id == "TTTC0012U"
        assert exc.payload["_systemtrade"]["order_tr_id"] == "TTTC0012U"
        assert exc.payload["_systemtrade"]["order_tr_id_attempts"] == ["TTTC0802U", "TTTC0012U"]
        assert exc.payload["_systemtrade"]["primary_order_error"]["tr_id"] == "TTTC0802U"
    else:
        raise AssertionError("KISError should be raised")

    assert calls == ["TTTC0802U", "TTTC0012U"]
