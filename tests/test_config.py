from system_trade.config import Settings


def test_settings_infers_real_mode_from_base_url(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "test-key")
    monkeypatch.setenv("KIS_APP_SECRET", "test-secret")
    monkeypatch.setenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
    monkeypatch.delenv("KIS_PAPER", raising=False)

    settings = Settings.load("")

    assert settings.kis_paper is False


def test_settings_parses_full_account_value(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "test-key")
    monkeypatch.setenv("KIS_APP_SECRET", "test-secret")
    monkeypatch.setenv("KIS_ACCOUNT_FULL", "63611886-01")
    monkeypatch.delenv("KIS_ACCOUNT_NO", raising=False)
    monkeypatch.delenv("KIS_ACNT_PRDT", raising=False)

    settings = Settings.load("")

    assert settings.kis_account_no == "63611886"
    assert settings.kis_acnt_prdt == "01"
