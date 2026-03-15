from __future__ import annotations


class KISError(RuntimeError):
    def __init__(self, tr_id: str, message: str, payload: dict | None = None, error_code: str | None = None):
        super().__init__(f"KIS API Error [{tr_id}]: {message}")
        self.tr_id = tr_id
        self.message = message
        self.payload = payload or {}
        self.error_code = error_code


class ConfigError(RuntimeError):
    pass
