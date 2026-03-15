from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .exceptions import ConfigError


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _infer_kis_paper(explicit_value: str | None, base_url: str | None) -> bool:
    if explicit_value is not None:
        return explicit_value.strip().lower() in {"1", "true", "yes", "y", "on"}

    normalized = (base_url or "").strip().lower()
    if "openapivts" in normalized:
        return True
    if "openapi.koreainvestment.com" in normalized:
        return False
    return False


@dataclass(frozen=True)
class Settings:
    kis_app_key: str
    kis_app_secret: str
    kis_paper: bool
    kis_base_url: str
    kis_account_no: str | None
    kis_acnt_prdt: str | None

    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str

    @classmethod
    def load(cls, explicit_env_file: str | None = None) -> "Settings":
        env_path = _discover_env_file(explicit_env_file)
        if env_path:
            load_dotenv(env_path, override=False)

        kis_app_key = os.getenv("KIS_APP_KEY", "").strip()
        kis_app_secret = os.getenv("KIS_APP_SECRET", "").strip()
        raw_kis_paper = os.getenv("KIS_PAPER")
        raw_base_url = os.getenv("KIS_BASE_URL", "").strip()
        kis_paper = _infer_kis_paper(raw_kis_paper, raw_base_url)

        default_base_url = (
            "https://openapivts.koreainvestment.com:29443"
            if kis_paper
            else "https://openapi.koreainvestment.com:9443"
        )
        kis_base_url = raw_base_url or default_base_url

        (
            derived_account_no,
            derived_acnt_prdt,
        ) = _parse_account_fields(
            os.getenv("KIS_ACCOUNT_NO"),
            os.getenv("KIS_ACCOUNT_NUMBER"),
            os.getenv("KIS_ACCOUNT_FULL"),
            os.getenv("KIS_ACCOUNT"),
            os.getenv("KIS_ACNT_PRDT"),
            os.getenv("KIS_ACCOUNT_CODE"),
        )
        kis_account_no = derived_account_no
        kis_acnt_prdt = derived_acnt_prdt

        db_host = os.getenv("SYSTEM_TRADE_DB_HOST", "127.0.0.1").strip()
        db_port = int(os.getenv("SYSTEM_TRADE_DB_PORT", "3306").strip())
        db_user = os.getenv("SYSTEM_TRADE_DB_USER", "root").strip()
        db_password = os.getenv("SYSTEM_TRADE_DB_PASSWORD", "")
        db_name = os.getenv("SYSTEM_TRADE_DB_NAME", "trade").strip()

        if not kis_app_key or not kis_app_secret:
            raise ConfigError("KIS_APP_KEY and KIS_APP_SECRET are required.")

        return cls(
            kis_app_key=kis_app_key,
            kis_app_secret=kis_app_secret,
            kis_paper=kis_paper,
            kis_base_url=kis_base_url,
            kis_account_no=kis_account_no,
            kis_acnt_prdt=kis_acnt_prdt,
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
            db_name=db_name,
        )

    def require_account(self) -> None:
        if not self.kis_account_no or not self.kis_acnt_prdt:
            raise ConfigError("KIS_ACCOUNT_NO and KIS_ACNT_PRDT are required for order submission.")

    def migration_path(self) -> Path:
        return Path(__file__).resolve().parent.parent / "migrations" / "001_init.sql"


def _discover_env_file(explicit_env_file: str | None) -> str:
    if explicit_env_file:
        explicit = Path(explicit_env_file).expanduser().resolve()
        return str(explicit) if explicit.exists() else ""

    discovered = find_dotenv(usecwd=True)
    if discovered:
        return discovered

    cwd = Path.cwd().resolve()
    candidates = [
        cwd / ".env",
        cwd.parent / ".env",
        cwd.parent / "systemAlgo" / ".env",
        Path(__file__).resolve().parents[3] / "systemAlgo" / ".env",
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def _parse_account_fields(
    account_no: str | None,
    account_number: str | None,
    account_full: str | None,
    account_alias: str | None,
    acnt_prdt: str | None,
    account_code: str | None,
) -> tuple[str | None, str | None]:
    first = (account_no or account_number or "").strip()
    second = (acnt_prdt or account_code or "").strip()
    combined = (account_full or account_alias or "").strip()

    if combined and "-" in combined:
        left, right = combined.split("-", 1)
        if not first:
            first = left.strip()
        if not second:
            second = right.strip()

    return first or None, second or None
