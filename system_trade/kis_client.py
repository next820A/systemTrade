from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import requests

from .config import Settings
from .domain import OrderType, Side
from .exceptions import KISError
from .tr_ids import DEMO_TR_IDS, REAL_TR_IDS, TradingTRIDs

TOKEN_SAFETY_MARGIN_SEC = 300
TOKEN_RATE_LIMIT_RETRY_SEC = 65


def extract_order_identifiers(response: dict[str, Any]) -> tuple[str | None, str | None]:
    output = response.get("output") if isinstance(response, dict) else None
    if not isinstance(output, dict):
        return None, None

    order_no = (
        output.get("ODNO")
        or output.get("odno")
        or output.get("ord_no")
        or output.get("order_no")
    )
    org_no = (
        output.get("KRX_FWDG_ORD_ORGNO")
        or output.get("krx_fwdg_ord_orgno")
        or output.get("orgn_odno")
        or output.get("origin_order_no")
    )
    return (str(order_no) if order_no else None, str(org_no) if org_no else None)


class KISClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._tr_ids: TradingTRIDs = DEMO_TR_IDS if settings.kis_paper else REAL_TR_IDS
        self._token: str | None = None
        self._token_expire_ts: float = 0
        self._lock = threading.Lock()
        self._token_cache_file = Path.home() / f".kis_token_cache_systemtrade_{self._token_cache_key()}.json"
        self._token_lock_file = Path.home() / f".kis_token_issue_systemtrade_{self._token_issue_lock_key()}.lock"
        self._load_cached_token()

    @contextmanager
    def _token_issue_lock(self) -> Iterator[None]:
        self._token_lock_file.parent.mkdir(parents=True, exist_ok=True)
        with self._token_lock_file.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _has_valid_token(self) -> bool:
        return bool(self._token and time.time() < self._token_expire_ts - TOKEN_SAFETY_MARGIN_SEC)

    def _load_cached_token(self) -> None:
        if not self._token_cache_file.exists():
            return
        try:
            cache = json.loads(self._token_cache_file.read_text(encoding="utf-8"))
            token = cache.get("access_token")
            expire_ts = float(cache.get("token_expire_ts", 0))
            if token and expire_ts > time.time() + TOKEN_SAFETY_MARGIN_SEC:
                self._token = str(token)
                self._token_expire_ts = expire_ts
        except Exception:
            return

    def _save_cached_token(self) -> None:
        payload = {
            "access_token": self._token,
            "token_expire_ts": self._token_expire_ts,
        }
        tmp_file = self._token_cache_file.with_name(
            f"{self._token_cache_file.name}.{os.getpid()}.tmp"
        )
        try:
            self._token_cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file.write_text(json.dumps(payload), encoding="utf-8")
            tmp_file.chmod(0o600)
            tmp_file.replace(self._token_cache_file)
        except Exception:
            try:
                tmp_file.unlink(missing_ok=True)
            except Exception:
                pass
            return

    def _token_cache_key(self) -> str:
        raw = "|".join(
            [
                self._settings.kis_base_url,
                self._settings.kis_app_key,
                "paper" if self._settings.kis_paper else "real",
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _token_issue_lock_key(self) -> str:
        raw = "|".join(
            [
                self._settings.kis_base_url,
                "paper" if self._settings.kis_paper else "real",
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _is_token_rate_limit(payload: dict[str, Any], message: str) -> bool:
        parts = [
            message,
            str(payload.get("error_description") or ""),
            str(payload.get("msg1") or ""),
            str(payload.get("msg_cd") or ""),
            str(payload.get("error_code") or ""),
        ]
        text = " ".join(parts)
        lowered = text.lower()
        return (
            ("1분당 1회" in text and ("접근토큰" in text or "token" in lowered))
            or "too many requests" in lowered
            or "rate limit" in lowered
        )

    def _post_token(self) -> tuple[int, dict[str, Any], str]:
        url = f"{self._settings.kis_base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._settings.kis_app_key,
            "appsecret": self._settings.kis_app_secret,
        }
        resp = requests.post(url, json=payload, timeout=20)
        try:
            data = resp.json() if resp.text else {}
        except ValueError:
            data = {}
        return resp.status_code, data, resp.text

    def _apply_issued_token(self, data: dict[str, Any]) -> str:
        access_token = data.get("access_token") or data.get("accessToken")
        if not access_token:
            raise KISError("TOKEN", "access_token is missing", payload=data)

        expires_in = int(data.get("expires_in", 24 * 60 * 60))
        self._token_expire_ts = time.time() + expires_in
        self._token = access_token
        self._save_cached_token()
        return access_token

    @staticmethod
    def _token_error_message(data: dict[str, Any], raw_text: str) -> str:
        return str(
            data.get("error_description")
            or data.get("msg1")
            or data.get("message")
            or raw_text
            or "token request failed"
        )

    def _issue_token(self) -> str:
        status_code, data, raw_text = self._post_token()
        has_access_token = bool(data.get("access_token") or data.get("accessToken"))
        if status_code < 400 and has_access_token:
            return self._apply_issued_token(data)

        msg = self._token_error_message(data, raw_text)
        if status_code >= 400 or self._is_token_rate_limit(data, msg):
            if self._has_valid_token():
                return self._token
            if self._is_token_rate_limit(data, msg):
                time.sleep(TOKEN_RATE_LIMIT_RETRY_SEC)
                self._load_cached_token()
                if self._has_valid_token():
                    return self._token

                status_code, data, raw_text = self._post_token()
                has_access_token = bool(data.get("access_token") or data.get("accessToken"))
                if status_code < 400 and has_access_token:
                    return self._apply_issued_token(data)
                msg = self._token_error_message(data, raw_text)
            raise KISError("TOKEN", msg, payload=data)

        return self._apply_issued_token(data)

    def get_access_token(self) -> str:
        with self._lock:
            if self._has_valid_token():
                return self._token
            with self._token_issue_lock():
                self._load_cached_token()
                if self._has_valid_token():
                    return self._token
                return self._issue_token()

    def _hashkey(self, body: dict[str, Any]) -> str:
        url = f"{self._settings.kis_base_url}/uapi/hashkey"
        headers = {
            "content-type": "application/json",
            "appkey": self._settings.kis_app_key,
            "appsecret": self._settings.kis_app_secret,
        }
        resp = requests.post(url, data=json.dumps(body), headers=headers, timeout=20)
        data = resp.json() if resp.text else {}
        if resp.status_code >= 400:
            msg = data.get("msg1") or data.get("error_description") or resp.text
            raise KISError("HASHKEY", msg, payload=data)

        hashkey = data.get("HASH")
        if not hashkey:
            raise KISError("HASHKEY", "HASH key is missing", payload=data)
        return hashkey

    def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        use_hashkey: bool = False,
    ) -> dict[str, Any]:
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_access_token()}",
            "appkey": self._settings.kis_app_key,
            "appsecret": self._settings.kis_app_secret,
            "tr_id": tr_id,
        }
        if use_hashkey and body:
            headers["hashkey"] = self._hashkey(body)

        url = f"{self._settings.kis_base_url}{path}"
        resp = requests.request(method, url, params=params, json=body, headers=headers, timeout=20)

        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text}

        if resp.status_code >= 400 or str(data.get("rt_cd", "0")) != "0":
            msg = data.get("msg1") or data.get("error_description") or resp.text
            err_cd = data.get("msg_cd") or data.get("error_code")
            raise KISError(tr_id, msg, payload=data, error_code=str(err_cd) if err_cd else None)

        return data

    def get_current_price(self, symbol: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
            },
        )

    def get_stock_balance(self) -> dict[str, Any]:
        self._settings.require_account()
        return self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            self._tr_ids.balance,
            params={
                "CANO": self._settings.kis_account_no,
                "ACNT_PRDT_CD": self._settings.kis_acnt_prdt,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )

    def get_daily_ccld(
        self,
        start_date: str,
        end_date: str,
        query_scope: str = "inner",
        side_filter: str = "00",
        symbol: str = "",
        fill_filter: str = "00",
        sort_order: str = "00",
        asset_filter: str = "00",
    ) -> dict[str, Any]:
        self._settings.require_account()

        tr_id = (
            self._tr_ids.daily_ccld_inner
            if query_scope == "inner"
            else self._tr_ids.daily_ccld_before
        )

        return self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id,
            params={
                "CANO": self._settings.kis_account_no,
                "ACNT_PRDT_CD": self._settings.kis_acnt_prdt,
                "INQR_STRT_DT": start_date,
                "INQR_END_DT": end_date,
                "SLL_BUY_DVSN_CD": side_filter,
                "PDNO": symbol,
                "CCLD_DVSN": fill_filter,
                "INQR_DVSN": sort_order,
                "INQR_DVSN_3": asset_filter,
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )

    def get_buying_power(
        self,
        symbol: str,
        price: int,
        order_type: OrderType,
    ) -> dict[str, Any]:
        self._settings.require_account()
        return self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            self._tr_ids.buying_power,
            params={
                "CANO": self._settings.kis_account_no,
                "ACNT_PRDT_CD": self._settings.kis_acnt_prdt,
                "PDNO": symbol,
                "ORD_UNPR": str(int(price)),
                "ORD_DVSN": self._to_kis_order_code(order_type),
                "OVRS_ICLD_YN": "N",
                "CMA_EVLU_AMT_ICLD_YN": "N",
            },
        )

    def get_sellable_quantity(self, symbol: str) -> dict[str, Any]:
        self._settings.require_account()
        return self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-sell",
            self._tr_ids.sellable,
            params={
                "CANO": self._settings.kis_account_no,
                "ACNT_PRDT_CD": self._settings.kis_acnt_prdt,
                "PDNO": symbol,
            },
        )

    def get_cancelable_orders(
        self,
        query_by: str = "1",
        side_filter: str = "0",
    ) -> dict[str, Any]:
        self._settings.require_account()
        return self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
            self._tr_ids.cancelable_orders,
            params={
                "CANO": self._settings.kis_account_no,
                "ACNT_PRDT_CD": self._settings.kis_acnt_prdt,
                "INQR_DVSN_1": query_by,
                "INQR_DVSN_2": side_filter,
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )

    def revise_or_cancel_order(
        self,
        original_order_no: str,
        revise_or_cancel_code: str,
        quantity: int,
        price: int,
        order_type: OrderType,
        exchange_id: str = "KRX",
        original_org_no: str = "",
        all_quantity: bool = False,
    ) -> dict[str, Any]:
        self._settings.require_account()

        body = {
            "CANO": self._settings.kis_account_no,
            "ACNT_PRDT_CD": self._settings.kis_acnt_prdt,
            "KRX_FWDG_ORD_ORGNO": original_org_no,
            "ORGN_ODNO": original_order_no,
            "ORD_DVSN": self._to_kis_order_code(order_type),
            "RVSE_CNCL_DVSN_CD": revise_or_cancel_code,
            "ORD_QTY": str(int(quantity)),
            "ORD_UNPR": str(int(price)),
            "QTY_ALL_ORD_YN": "Y" if all_quantity else "N",
            "EXCG_ID_DVSN_CD": exchange_id,
        }
        return self._request(
            "POST",
            "/uapi/domestic-stock/v1/trading/order-rvsecncl",
            self._tr_ids.order_rvsecncl,
            body=body,
            use_hashkey=True,
        )

    def place_cash_order(
        self,
        side: Side,
        symbol: str,
        quantity: int,
        order_type: OrderType,
        price: int | None,
    ) -> dict[str, Any]:
        self._settings.require_account()

        if quantity <= 0:
            raise ValueError("quantity must be positive")

        if order_type == OrderType.LIMIT and (price is None or price <= 0):
            raise ValueError("price must be positive for LIMIT order")

        tr_id = self._tr_ids.order_buy if side == Side.BUY else self._tr_ids.order_sell
        ord_dvsn = self._to_kis_order_code(order_type)
        ord_unpr = 0 if order_type == OrderType.MARKET else int(price or 0)

        body = {
            "CANO": self._settings.kis_account_no,
            "ACNT_PRDT_CD": self._settings.kis_acnt_prdt,
            "PDNO": symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(int(quantity)),
            "ORD_UNPR": str(ord_unpr),
            "EXCG_ID_DVSN_CD": "KRX",
        }

        attempted_tr_ids = [tr_id]
        try:
            response = self._request(
                "POST",
                "/uapi/domestic-stock/v1/trading/order-cash",
                tr_id,
                body=body,
                use_hashkey=True,
            )
            return self._with_order_attempt_metadata(
                response,
                tr_id=tr_id,
                attempted_tr_ids=attempted_tr_ids,
            )
        except KISError as exc:
            fallback_tr_id = (
                self._tr_ids.order_buy_fallback
                if side == Side.BUY
                else self._tr_ids.order_sell_fallback
            )
            if fallback_tr_id and fallback_tr_id != tr_id and self._is_rejected_tr_id_error(exc):
                attempted_tr_ids.append(fallback_tr_id)
                try:
                    response = self._request(
                        "POST",
                        "/uapi/domestic-stock/v1/trading/order-cash",
                        fallback_tr_id,
                        body=body,
                        use_hashkey=True,
                    )
                except KISError as fallback_exc:
                    self._attach_order_attempt_metadata(
                        fallback_exc,
                        attempted_tr_ids=attempted_tr_ids,
                        primary_error=exc,
                    )
                    raise
                return self._with_order_attempt_metadata(
                    response,
                    tr_id=fallback_tr_id,
                    attempted_tr_ids=attempted_tr_ids,
                    primary_error=exc,
                )
            self._attach_order_attempt_metadata(exc, attempted_tr_ids=attempted_tr_ids)
            raise

    @property
    def tr_ids(self) -> TradingTRIDs:
        return self._tr_ids

    @staticmethod
    def _is_rejected_tr_id_error(exc: KISError) -> bool:
        error_code = str(exc.error_code or exc.payload.get("msg_cd") or "").upper()
        message = exc.message.lower()
        return (
            error_code == "EGW02005"
            or "실전투자 tr" in message
            or "모의투자 tr" in message
        )

    @staticmethod
    def _to_kis_order_code(order_type: OrderType) -> str:
        return "01" if order_type == OrderType.MARKET else "00"

    @staticmethod
    def _with_order_attempt_metadata(
        payload: dict[str, Any],
        *,
        tr_id: str,
        attempted_tr_ids: list[str],
        primary_error: KISError | None = None,
    ) -> dict[str, Any]:
        enriched = dict(payload)
        existing_meta = enriched.get("_systemtrade")
        metadata = dict(existing_meta) if isinstance(existing_meta, dict) else {}
        metadata["order_tr_id"] = tr_id
        metadata["order_tr_id_attempts"] = list(attempted_tr_ids)
        if primary_error is not None:
            metadata["primary_order_error"] = {
                "tr_id": primary_error.tr_id,
                "error_code": primary_error.error_code,
                "message": primary_error.message,
                "payload": primary_error.payload,
            }
        enriched["_systemtrade"] = metadata
        return enriched

    def _attach_order_attempt_metadata(
        self,
        exc: KISError,
        *,
        attempted_tr_ids: list[str],
        primary_error: KISError | None = None,
    ) -> None:
        exc.payload = self._with_order_attempt_metadata(
            exc.payload,
            tr_id=exc.tr_id,
            attempted_tr_ids=attempted_tr_ids,
            primary_error=primary_error,
        )
