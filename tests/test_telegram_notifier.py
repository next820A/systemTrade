from datetime import date, datetime

from system_trade.telegram_notifier import (
    build_order_event_message,
    build_order_summary_message,
    load_telegram_config,
    send_telegram_message,
)


def test_load_telegram_config_returns_none_when_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("SYSTEM_TRADE_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SYSTEM_TRADE_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert load_telegram_config(env_path=tmp_path / "missing.env") is None


def test_send_telegram_message_uses_configured_endpoint(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "telegram.env"
    env_path.write_text(
        "SYSTEM_TRADE_TELEGRAM_BOT_TOKEN=token-1\n"
        "SYSTEM_TRADE_TELEGRAM_CHAT_ID=-10042\n",
        encoding="utf-8",
    )
    calls: list[tuple[str, dict[str, str], float]] = []

    def fake_post(url: str, payload: dict[str, str], timeout: float) -> dict[str, object]:
        calls.append((url, payload, timeout))
        return {"ok": True}

    result = send_telegram_message("hello", env_path=env_path, request_func=fake_post)

    assert result.status == "sent"
    assert result.chat_id == "-10042"
    assert calls[0][0] == "https://api.telegram.org/bottoken-1/sendMessage"
    assert calls[0][1]["chat_id"] == "-10042"
    assert calls[0][1]["text"] == "hello"


def test_build_order_messages() -> None:
    order = {
        "id": 16,
        "status": "SENT",
        "trade_date": "2026-07-08",
        "account_alias": "halfrise",
        "strategy_name": "halfrise_v2",
        "side": "BUY",
        "symbol": "005490",
        "quantity": 6,
        "broker_order_no": "0008678100",
        "reason": "halfrise_v2_legacy_close_entry",
    }

    event_message = build_order_event_message(order, event_type="SENT")
    assert "주문 접수" in event_message
    assert "005490 x 6" in event_message
    assert "0008678100" in event_message

    rejected_message = build_order_event_message(
        {
            **order,
            "status": "REJECTED",
            "broker_order_no": None,
            "error_code": "EGW02005",
            "error_message": "실전투자 TR 이 아닙니다.",
            "kis_order_tr_id_attempts": ["TTTC0802U", "TTTC0012U"],
        },
        event_type="REJECTED",
    )
    assert "주문 거절" in rejected_message
    assert "tr_id_attempts: TTTC0802U -> TTTC0012U" in rejected_message

    summary = build_order_summary_message(
        [order],
        trade_date=date(2026, 7, 8),
        generated_at=datetime(2026, 7, 8, 9, 45, 0),
    )
    assert "SENT 1" in summary
    assert "#16 SENT" in summary
