from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import requests

from .config import Settings
from .domain import OrderType, Side
from .exceptions import KISError
from .tr_ids import DEMO_TR_IDS, REAL_TR_IDS, TradingTRIDs

TOKEN_SAFETY_MARGIN_SEC = 300


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
        self._token_cache_file = Path.home() / ".kis_token_cache_systemtrade.json"
        self._load_cached_token()

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
        try:
            self._token_cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            return

    def _issue_token(self) -> str:
        url = f"{self._settings.kis_base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._settings.kis_app_key,
            "appsecret": self._settings.kis_app_secret,
        }
        resp = requests.post(url, json=payload, timeout=20)
        data = resp.json() if resp.text else {}
        if resp.status_code >= 400:
            msg = data.get("error_description") or resp.text
            if self._token and time.time() < self._token_expire_ts - TOKEN_SAFETY_MARGIN_SEC:
                return self._token
            raise KISError("TOKEN", msg, payload=data)

        access_token = data.get("access_token") or data.get("accessToken")
        if not access_token:
            raise KISError("TOKEN", "access_token is missing", payload=data)

        expires_in = int(data.get("expires_in", 24 * 60 * 60))
        self._token_expire_ts = time.time() + expires_in
        self._token = access_token
        self._save_cached_token()
        return access_token

    def get_access_token(self) -> str:
        with self._lock:
            now = time.time()
            if self._token and now < self._token_expire_ts - TOKEN_SAFETY_MARGIN_SEC:
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

        return self._request(
            "POST",
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id,
            body=body,
            use_hashkey=True,
        )

    @property
    def tr_ids(self) -> TradingTRIDs:
        return self._tr_ids

    @staticmethod
    def _to_kis_order_code(order_type: OrderType) -> str:
        return "01" if order_type == OrderType.MARKET else "00"
