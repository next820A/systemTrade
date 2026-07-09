from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

DEFAULT_ENV_PATH = Path.home() / "Library" / "Application Support" / "systemTrade" / "trading_telegram.env"
DEFAULT_MESSAGE_LIMIT = 3500

RequestFunc = Callable[[str, dict[str, str], float], dict[str, Any]]


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str
    env_path: str | None = None


@dataclass(frozen=True)
class TelegramSendResult:
    status: str
    configured: bool
    chat_id: str | None = None
    message_length: int = 0
    description: str | None = None


def default_env_path() -> Path:
    configured = os.getenv("SYSTEM_TRADE_TELEGRAM_ENV")
    return Path(configured).expanduser() if configured else DEFAULT_ENV_PATH


def parse_env_file(path: Path | str) -> dict[str, str]:
    env_path = Path(path).expanduser()
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_telegram_config(*, env_path: Path | str | None = None) -> TelegramConfig | None:
    path = Path(env_path).expanduser() if env_path else default_env_path()
    file_values = parse_env_file(path)
    token = (
        os.getenv("SYSTEM_TRADE_TELEGRAM_BOT_TOKEN")
        or file_values.get("SYSTEM_TRADE_TELEGRAM_BOT_TOKEN")
        or os.getenv("TELEGRAM_BOT_TOKEN")
        or file_values.get("TELEGRAM_BOT_TOKEN")
    )
    chat_id = (
        os.getenv("SYSTEM_TRADE_TELEGRAM_CHAT_ID")
        or file_values.get("SYSTEM_TRADE_TELEGRAM_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT_ID")
        or file_values.get("TELEGRAM_CHAT_ID")
    )
    if not token or not chat_id:
        return None
    return TelegramConfig(bot_token=token, chat_id=chat_id, env_path=str(path))


def trim_message(text: str, *, limit: int = DEFAULT_MESSAGE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n\n[이하 생략]"
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def _post_form(url: str, payload: dict[str, str], timeout: float) -> dict[str, Any]:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return dict(json.loads(raw))


def send_telegram_message(
    text: str,
    *,
    env_path: Path | str | None = None,
    timeout: float = 10.0,
    request_func: RequestFunc | None = None,
) -> TelegramSendResult:
    config = load_telegram_config(env_path=env_path)
    if not config:
        return TelegramSendResult(status="skipped_missing_config", configured=False)

    message = trim_message(text)
    url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
    payload = {
        "chat_id": config.chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }
    try:
        response = (request_func or _post_form)(url, payload, timeout)
    except Exception as exc:
        return TelegramSendResult(
            status="failed",
            configured=True,
            chat_id=config.chat_id,
            message_length=len(message),
            description=str(exc),
        )

    return TelegramSendResult(
        status="sent" if response.get("ok") else "failed",
        configured=True,
        chat_id=config.chat_id,
        message_length=len(message),
        description=response.get("description"),
    )


def build_order_event_message(order: dict[str, Any], *, event_type: str) -> str:
    status = str(order.get("status") or "")
    header = "[systemTrade 주문 알림]"
    if status == "SENT":
        title = "주문 접수"
    elif status == "REJECTED":
        title = "주문 거절"
    else:
        title = f"주문 상태 {status or '-'}"

    lines = [
        f"{header} {title}",
        f"event: {event_type}",
        f"order_id: {order.get('id')}",
        f"trade_date: {order.get('trade_date')}",
        f"account_alias: {order.get('account_alias')}",
        f"strategy: {order.get('strategy_name') or order.get('strategy_family') or '-'}",
        f"side/symbol/qty: {order.get('side')} {order.get('symbol')} x {order.get('quantity')}",
    ]
    if order.get("broker_order_no"):
        lines.append(f"broker_order_no: {order.get('broker_order_no')}")
    if order.get("kis_order_tr_id_attempts"):
        attempts = order.get("kis_order_tr_id_attempts")
        if isinstance(attempts, (list, tuple)):
            attempts_text = " -> ".join(str(item) for item in attempts)
        else:
            attempts_text = str(attempts)
        lines.append(f"tr_id_attempts: {attempts_text}")
    elif order.get("kis_order_tr_id"):
        lines.append(f"tr_id: {order.get('kis_order_tr_id')}")
    if order.get("error_code") or order.get("error_message"):
        lines.append(f"error: {order.get('error_code') or '-'} / {order.get('error_message') or '-'}")
    if order.get("reason"):
        lines.append(f"reason: {order.get('reason')}")
    return "\n".join(lines)


def notify_order_event(order: dict[str, Any], *, event_type: str) -> TelegramSendResult:
    return send_telegram_message(build_order_event_message(order, event_type=event_type))


def build_order_summary_message(
    orders: list[dict[str, Any]],
    *,
    trade_date: date | str,
    generated_at: datetime | None = None,
) -> str:
    generated = generated_at or datetime.now()
    sent = [row for row in orders if str(row.get("status")) == "SENT"]
    rejected = [row for row in orders if str(row.get("status")) == "REJECTED"]
    other = [row for row in orders if str(row.get("status")) not in {"SENT", "REJECTED"}]

    lines = [
        "[systemTrade 거래 이력]",
        f"trade_date: {trade_date}",
        f"generated_at: {generated.isoformat(timespec='seconds')}",
        f"summary: SENT {len(sent)} / REJECTED {len(rejected)} / OTHER {len(other)}",
    ]
    for row in orders:
        base = (
            f"- #{row.get('id')} {row.get('status')} "
            f"{row.get('account_alias')} {row.get('side')} {row.get('symbol')} x {row.get('quantity')}"
        )
        if row.get("broker_order_no"):
            base += f" odno={row.get('broker_order_no')}"
        if row.get("error_message"):
            base += f" error={row.get('error_code') or '-'}:{row.get('error_message')}"
        lines.append(base)
    return "\n".join(lines)
