from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .exceptions import ConfigError

DEFAULT_ACCOUNT_ALIASES = ("test", "hagfish", "halfrise")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _infer_kis_paper(explicit_value: str | None, base_url: str | None) -> bool:
    if explicit_value is not None:
        return explicit_value.strip().lower() in {"1", "true", "yes", "y", "on"}

    endpoint_mode = _kis_endpoint_paper_mode(base_url)
    if endpoint_mode is not None:
        return endpoint_mode

    return False


def _kis_endpoint_paper_mode(base_url: str | None) -> bool | None:
    normalized = (base_url or "").strip().lower()
    if "openapivts" in normalized:
        return True
    if "openapi.koreainvestment.com" in normalized:
        return False
    return None


def _validate_kis_mode(kis_paper: bool, base_url: str) -> None:
    endpoint_mode = _kis_endpoint_paper_mode(base_url)
    if endpoint_mode is None or endpoint_mode == kis_paper:
        return

    configured_mode = "paper" if kis_paper else "real"
    endpoint_mode_name = "paper" if endpoint_mode else "real"
    raise ConfigError(
        "KIS_PAPER conflicts with KIS_BASE_URL: "
        f"KIS_PAPER selects {configured_mode}, but {base_url} is a {endpoint_mode_name} endpoint."
    )


@dataclass(frozen=True)
class AccountBinding:
    account_alias: str
    account_no: str
    account_product_code: str


@dataclass(frozen=True)
class CredentialBinding:
    account_alias: str
    kis_app_key: str
    kis_app_secret: str


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
    account_alias: str | None = None
    account_bindings: dict[str, AccountBinding] = field(default_factory=dict)
    credential_bindings: dict[str, CredentialBinding] = field(default_factory=dict)

    @classmethod
    def load(cls, explicit_env_file: str | None = None, require_kis: bool = True) -> "Settings":
        env_path = _discover_env_file(explicit_env_file)
        if env_path:
            load_dotenv(env_path, override=True)

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
        _validate_kis_mode(kis_paper, kis_base_url)

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
        account_alias = _normalize_account_alias(os.getenv("SYSTEM_TRADE_ACCOUNT_ALIAS") or os.getenv("KIS_ACCOUNT_ALIAS"))
        account_bindings = _load_account_bindings(account_alias)
        credential_bindings = _load_credential_bindings(account_alias)
        if account_alias and account_alias in account_bindings:
            account_binding = account_bindings[account_alias]
            kis_account_no = account_binding.account_no
            kis_acnt_prdt = account_binding.account_product_code
        if account_alias and account_alias in credential_bindings:
            credential_binding = credential_bindings[account_alias]
            kis_app_key = credential_binding.kis_app_key
            kis_app_secret = credential_binding.kis_app_secret

        if require_kis and (not kis_app_key or not kis_app_secret):
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
            account_alias=account_alias,
            account_bindings=account_bindings,
            credential_bindings=credential_bindings,
        )

    def require_account(self) -> None:
        if not self.kis_account_no or not self.kis_acnt_prdt:
            raise ConfigError("KIS_ACCOUNT_NO and KIS_ACNT_PRDT are required for order submission.")

    def for_account_alias(self, account_alias: str | None) -> "Settings":
        requested_alias = _normalize_account_alias(account_alias)
        if requested_alias and self.account_alias and requested_alias != self.account_alias:
            raise ConfigError(
                f"account_alias mismatch: request={requested_alias}, env={self.account_alias}"
            )

        selected_alias = requested_alias or self.account_alias
        if not selected_alias:
            return self

        binding = self.account_bindings.get(selected_alias)
        credential = self.credential_bindings.get(selected_alias)
        updates: dict[str, str | None] = {"account_alias": selected_alias}
        if binding:
            updates["kis_account_no"] = binding.account_no
            updates["kis_acnt_prdt"] = binding.account_product_code
        if credential:
            updates["kis_app_key"] = credential.kis_app_key
            updates["kis_app_secret"] = credential.kis_app_secret

        return replace(self, **updates)

    def with_account(
        self,
        *,
        account_alias: str | None,
        account_no: str,
        account_product_code: str,
    ) -> "Settings":
        return replace(
            self,
            account_alias=_normalize_account_alias(account_alias),
            kis_account_no=account_no,
            kis_acnt_prdt=account_product_code,
        )

    def migration_path(self) -> Path:
        return Path(__file__).resolve().parent.parent / "migrations" / "001_init.sql"

    def migration_paths(self) -> list[Path]:
        migration_dir = Path(__file__).resolve().parent.parent / "migrations"
        return sorted(migration_dir.glob("*.sql"))


def _discover_env_file(explicit_env_file: str | None) -> str:
    if explicit_env_file is not None:
        if explicit_env_file.strip() == "":
            return ""
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
    elif combined and not first:
        first = combined

    return first or None, second or None


def _normalize_account_alias(account_alias: str | None) -> str | None:
    normalized = (account_alias or "").strip().lower()
    return normalized or None


def _env_suffix(account_alias: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", account_alias.upper()).strip("_")


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _known_account_aliases(account_alias: str | None) -> set[str]:
    aliases = set(DEFAULT_ACCOUNT_ALIASES)
    if account_alias:
        aliases.add(account_alias)

    split_prefixes = (
        "SYSTEM_TRADE_ACCOUNT_NO_",
        "SYSTEM_TRADE_ACNT_PRDT_",
        "SYSTEM_TRADE_ACCOUNT_CODE_",
        "SYSTEM_TRADE_APP_KEY_",
        "SYSTEM_TRADE_APP_SECRET_",
        "SYSTEM_TRADE_ACCOUNT_APP_KEY_",
        "SYSTEM_TRADE_ACCOUNT_APP_SECRET_",
        "KIS_ACCOUNT_NO_",
        "KIS_ACCOUNT_NUMBER_",
        "KIS_ACNT_PRDT_",
        "KIS_ACCOUNT_CODE_",
        "KIS_APP_KEY_",
        "KIS_APP_SECRET_",
    )
    full_prefixes = ("SYSTEM_TRADE_ACCOUNT_", "KIS_ACCOUNT_")
    reserved_full_suffixes = {
        "ALIAS",
        "FULL",
        "NO",
        "NUMBER",
        "CODE",
        "APP_KEY",
        "APP_SECRET",
    }

    for name in os.environ:
        split_suffix = next((name[len(prefix):] for prefix in split_prefixes if name.startswith(prefix)), None)
        if split_suffix:
            aliases.add(split_suffix.lower())
            continue

        full_suffix = next((name[len(prefix):] for prefix in full_prefixes if name.startswith(prefix)), None)
        if full_suffix and full_suffix not in reserved_full_suffixes:
            aliases.add(full_suffix.lower())

    return aliases


def _load_account_bindings(account_alias: str | None) -> dict[str, AccountBinding]:
    bindings: dict[str, AccountBinding] = {}
    for alias in sorted(_known_account_aliases(account_alias)):
        suffix = _env_suffix(alias)
        if not suffix:
            continue

        account_no, account_product_code = _parse_account_fields(
            _first_env(f"SYSTEM_TRADE_ACCOUNT_NO_{suffix}", f"KIS_ACCOUNT_NO_{suffix}"),
            _first_env(f"KIS_ACCOUNT_NUMBER_{suffix}"),
            _first_env(f"SYSTEM_TRADE_ACCOUNT_{suffix}", f"KIS_ACCOUNT_{suffix}"),
            None,
            _first_env(
                f"SYSTEM_TRADE_ACNT_PRDT_{suffix}",
                f"SYSTEM_TRADE_ACCOUNT_CODE_{suffix}",
                f"KIS_ACNT_PRDT_{suffix}",
                f"KIS_ACCOUNT_CODE_{suffix}",
            ),
            None,
        )
        if account_no and account_product_code:
            bindings[alias] = AccountBinding(
                account_alias=alias,
                account_no=account_no,
                account_product_code=account_product_code,
            )

    return bindings


def _load_credential_bindings(account_alias: str | None) -> dict[str, CredentialBinding]:
    bindings: dict[str, CredentialBinding] = {}
    for alias in sorted(_known_account_aliases(account_alias)):
        suffix = _env_suffix(alias)
        if not suffix:
            continue

        app_key = _first_env(
            f"SYSTEM_TRADE_APP_KEY_{suffix}",
            f"SYSTEM_TRADE_ACCOUNT_APP_KEY_{suffix}",
            f"SYSTEM_TRADE_ACCOUNT_{suffix}_APP_KEY",
            f"KIS_APP_KEY_{suffix}",
            f"KIS_ACCOUNT_{suffix}_APP_KEY",
        )
        app_secret = _first_env(
            f"SYSTEM_TRADE_APP_SECRET_{suffix}",
            f"SYSTEM_TRADE_ACCOUNT_APP_SECRET_{suffix}",
            f"SYSTEM_TRADE_ACCOUNT_{suffix}_APP_SECRET",
            f"KIS_APP_SECRET_{suffix}",
            f"KIS_ACCOUNT_{suffix}_APP_SECRET",
        )
        if app_key and app_secret:
            bindings[alias] = CredentialBinding(
                account_alias=alias,
                kis_app_key=app_key,
                kis_app_secret=app_secret,
            )

    return bindings
