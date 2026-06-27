from __future__ import annotations

from typing import Any

import pytest

from system_trade.config import Settings
from system_trade.exceptions import ConfigError
from system_trade.main import _resolve_runtime_account


class FakeRepository:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row
        self.lookup: str | None = None

    def find_active_trade_account_by_alias(self, *, account_alias: str, broker: str = "KIS") -> dict[str, Any] | None:
        self.lookup = account_alias
        return self.row


def _settings(**overrides: Any) -> Settings:
    payload = {
        "kis_app_key": "key",
        "kis_app_secret": "secret",
        "kis_paper": False,
        "kis_base_url": "https://openapi.koreainvestment.com:9443",
        "kis_account_no": None,
        "kis_acnt_prdt": None,
        "db_host": "127.0.0.1",
        "db_port": 3306,
        "db_user": "root",
        "db_password": "",
        "db_name": "trade",
        "account_alias": "hagfish",
    }
    payload.update(overrides)
    return Settings(**payload)


def test_runtime_account_is_loaded_from_db_alias() -> None:
    repository = FakeRepository(
        {
            "account_alias": "hagfish",
            "account_no": "22222222",
            "account_product_code": "01",
        }
    )

    settings = _resolve_runtime_account(_settings(kis_account_no="11111111"), repository)  # type: ignore[arg-type]

    assert repository.lookup == "hagfish"
    assert settings.kis_account_no == "22222222"
    assert settings.kis_acnt_prdt == "01"


def test_runtime_account_requires_bound_db_alias() -> None:
    repository = FakeRepository(None)

    with pytest.raises(ConfigError, match="bind-account"):
        _resolve_runtime_account(_settings(), repository)  # type: ignore[arg-type]
