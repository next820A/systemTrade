import pytest

from system_trade.config import Settings
from system_trade.exceptions import ConfigError


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


def test_settings_can_load_db_only_without_kis(monkeypatch) -> None:
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    monkeypatch.setenv("SYSTEM_TRADE_DB_NAME", "trade")

    settings = Settings.load("", require_kis=False)

    assert settings.db_name == "trade"
    assert settings.kis_app_key == ""


def test_settings_loads_account_alias(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "key")
    monkeypatch.setenv("KIS_APP_SECRET", "secret")
    monkeypatch.setenv("SYSTEM_TRADE_ACCOUNT_ALIAS", "hagfish")

    settings = Settings.load("")

    assert settings.account_alias == "hagfish"


def test_settings_selects_alias_account_from_env(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "key")
    monkeypatch.setenv("KIS_APP_SECRET", "secret")
    monkeypatch.setenv("SYSTEM_TRADE_ACCOUNT_ALIAS", "hagfish")
    monkeypatch.setenv("SYSTEM_TRADE_ACCOUNT_HAGFISH", "22222222-01")
    monkeypatch.delenv("KIS_ACCOUNT_NO", raising=False)
    monkeypatch.delenv("KIS_ACNT_PRDT", raising=False)

    settings = Settings.load("")

    assert settings.kis_account_no == "22222222"
    assert settings.kis_acnt_prdt == "01"
    assert settings.account_bindings["hagfish"].account_no == "22222222"


def test_settings_can_select_requested_alias_account(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "key")
    monkeypatch.setenv("KIS_APP_SECRET", "secret")
    monkeypatch.setenv("SYSTEM_TRADE_ACCOUNT_TEST", "11111111-01")
    monkeypatch.setenv("SYSTEM_TRADE_ACCOUNT_HALFRISE", "33333333-01")
    monkeypatch.delenv("SYSTEM_TRADE_ACCOUNT_ALIAS", raising=False)
    monkeypatch.delenv("KIS_ACCOUNT_NO", raising=False)
    monkeypatch.delenv("KIS_ACNT_PRDT", raising=False)

    settings = Settings.load("").for_account_alias("halfrise")

    assert settings.account_alias == "halfrise"
    assert settings.kis_account_no == "33333333"
    assert settings.kis_acnt_prdt == "01"


def test_settings_can_select_requested_alias_credentials(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "global-key")
    monkeypatch.setenv("KIS_APP_SECRET", "global-secret")
    monkeypatch.setenv("SYSTEM_TRADE_APP_KEY_HAGFISH", "hagfish-key")
    monkeypatch.setenv("SYSTEM_TRADE_APP_SECRET_HAGFISH", "hagfish-secret")
    monkeypatch.delenv("SYSTEM_TRADE_ACCOUNT_ALIAS", raising=False)

    settings = Settings.load("").for_account_alias("hagfish")

    assert settings.account_alias == "hagfish"
    assert settings.kis_app_key == "hagfish-key"
    assert settings.kis_app_secret == "hagfish-secret"


def test_settings_blocks_requested_alias_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "key")
    monkeypatch.setenv("KIS_APP_SECRET", "secret")
    monkeypatch.setenv("SYSTEM_TRADE_ACCOUNT_ALIAS", "test")
    monkeypatch.setenv("SYSTEM_TRADE_ACCOUNT_TEST", "11111111-01")
    monkeypatch.setenv("SYSTEM_TRADE_ACCOUNT_HAGFISH", "22222222-01")

    settings = Settings.load("")

    with pytest.raises(ConfigError, match="account_alias mismatch"):
        settings.for_account_alias("hagfish")
