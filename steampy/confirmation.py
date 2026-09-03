import enum
import json
import logging
import time
from typing import List
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from steampy import guard
from steampy.exceptions import (
    ConfirmationActionRejected,
    ConfirmationAuthRequired,
    ConfirmationHttpError,
    ConfirmationMalformedResponse,
    ConfirmationNotFound,
    ConfirmationNotReady,
    ConfirmationRateLimited,
    InvalidAuthenticatorError,
    InvalidConfirmationPageError,
)


logger = logging.getLogger(__name__)


class Confirmation:
    def __init__(self, data_confid, nonce, creator_id=-1):
        self.data_confid = data_confid
        self.nonce = nonce
        self.creator_id = creator_id


class Tag(enum.Enum):
    CONF = "conf"
    DETAILS = "details"
    ALLOW = "allow"
    CANCEL = "cancel"


class ConfirmationExecutor:
    CONF_URL = "https://steamcommunity.com/mobileconf"
    MOBILECONF_USER_AGENT = "okhttp/4.9.2"
    INVALID_AUTHENTICATOR_MESSAGE = "Steam Guard Mobile Authenticator is providing incorrect Steam Guard codes."

    def __init__(self, identity_secret: str, my_steam_id: str, session: requests.Session) -> None:
        self._my_steam_id = my_steam_id
        self._identity_secret = identity_secret
        self._session = session

    def send_trade_allow_request(self, trade_offer_id: str, match_end: bool = False) -> dict:
        confirmations = self._get_confirmations()
        for _ in range(3):
            try:
                confirmation = self._select_trade_offer_confirmation(confirmations, trade_offer_id, match_end)
                return self._send_confirmation(confirmation)
            except ConfirmationNotReady:
                time.sleep(3)
        raise ConfirmationNotFound("查找交易报价移动确认", detail=f"重试后仍未找到报价 {trade_offer_id} 对应的确认")

    def confirm_sell_listing(self, asset_id: str) -> dict:
        confirmations = self._get_confirmations()
        confirmation = self._select_sell_listing_confirmation(confirmations, asset_id)
        return self._send_confirmation(confirmation)

    def _send_confirmation(self, confirmation: Confirmation) -> dict:
        tag = Tag.ALLOW
        params = self._create_confirmation_params(tag.value)
        params["op"] = (tag.value,)
        params["cid"] = confirmation.data_confid
        params["ck"] = confirmation.nonce
        headers = self._mobileconf_headers("XMLHttpRequest")
        response = self._session.get(self.CONF_URL + "/ajaxop", params=params, headers=headers, timeout=15)
        response_json = self._parse_mobileconf_json(response, "提交移动确认")
        if "success" not in response_json:
            raise ConfirmationMalformedResponse(
                "提交移动确认",
                detail="Steam 响应缺少 success 字段",
                http_status=response.status_code,
                response=response,
            )
        if not response_json["success"]:
            raise ConfirmationActionRejected(
                "提交移动确认",
                detail=self._response_message(response_json, "Steam 未接受移动确认操作"),
                http_status=response.status_code,
                response=response,
            )
        return response_json

    def _get_confirmations(self) -> List[Confirmation]:
        for attempt in range(5):
            confirmations_page = self._fetch_confirmations_page()
            try:
                confirmations_json = self._parse_mobileconf_json(confirmations_page, "获取移动确认列表")
            except ConfirmationHttpError:
                if attempt < 4:
                    time.sleep(1)
                    continue
                raise

            if "success" in confirmations_json and not confirmations_json["success"]:
                raise InvalidConfirmationPageError(
                    "获取移动确认列表",
                    detail=self._response_message(confirmations_json, "Steam 未返回有效的确认列表"),
                    http_status=confirmations_page.status_code,
                    response=confirmations_page,
                )

            raw_confirmations = confirmations_json.get("conf", [])
            if raw_confirmations is None:
                raw_confirmations = []
            if not isinstance(raw_confirmations, list):
                raise ConfirmationMalformedResponse(
                    "获取移动确认列表",
                    detail="Steam 响应中的 conf 字段不是列表",
                    http_status=confirmations_page.status_code,
                    response=confirmations_page,
                )

            confirmations = []
            for confirmation_data in raw_confirmations:
                try:
                    data_confid = confirmation_data["id"]
                    nonce = confirmation_data["nonce"]
                except (KeyError, TypeError) as error:
                    raise ConfirmationMalformedResponse(
                        "解析移动确认列表",
                        detail="Steam 返回的确认项缺少 id 或 nonce",
                        http_status=confirmations_page.status_code,
                        response=confirmations_page,
                    ) from error
                creator_id = confirmation_data.get("creator_id", -1)
                confirmations.append(Confirmation(data_confid, nonce, creator_id))
            return confirmations

        return []

    def _fetch_confirmations_page(self) -> requests.Response:
        tag = Tag.CONF.value
        params = self._create_confirmation_params(tag)
        headers = self._mobileconf_headers("com.valvesoftware.android.steam.community")
        return self._session.get(self.CONF_URL + "/getlist", params=params, headers=headers, timeout=15)

    def _fetch_confirmation_details_page(self, confirmation: Confirmation) -> str:
        tag = "details" + confirmation.data_confid
        params = self._create_confirmation_params(tag)
        response = self._session.get(
            self.CONF_URL + "/details/" + confirmation.data_confid,
            params=params,
            headers=self._mobileconf_headers(),
            timeout=15,
        )
        response_json = self._parse_mobileconf_json(response, "获取移动确认详情")
        if "success" in response_json and not response_json["success"]:
            raise InvalidConfirmationPageError(
                "获取移动确认详情",
                detail=self._response_message(response_json, "Steam 未返回有效的确认详情"),
                http_status=response.status_code,
                response=response,
            )
        html = response_json.get("html")
        if not isinstance(html, str):
            raise ConfirmationMalformedResponse(
                "获取移动确认详情",
                detail="Steam 响应缺少 html 字段",
                http_status=response.status_code,
                response=response,
            )
        return html

    @classmethod
    def _mobileconf_headers(cls, requested_with: str = None) -> dict:
        headers = {"User-Agent": cls.MOBILECONF_USER_AGENT}
        if requested_with:
            headers["X-Requested-With"] = requested_with
        return headers

    @classmethod
    def _parse_mobileconf_json(cls, response: requests.Response, stage: str) -> dict:
        response_json = None
        json_error = None
        try:
            response_json = response.json()
        except (requests.exceptions.JSONDecodeError, json.JSONDecodeError, ValueError) as error:
            json_error = error

        cls._log_mobileconf_response(response, stage, response_json)
        retry_after = response.headers.get("Retry-After")
        status_code = response.status_code

        if cls._is_login_response(response) or status_code == 401:
            raise ConfirmationAuthRequired(
                stage,
                detail=cls._response_message(response_json, "Steam 要求重新登录") if isinstance(response_json, dict) else "Steam 要求重新登录",
                http_status=status_code,
                retry_after=retry_after,
                response=response,
            )

        if status_code == 429:
            raise ConfirmationRateLimited(
                stage,
                detail=cls._response_message(response_json, "Steam 移动确认接口请求过于频繁") if isinstance(response_json, dict) else "Steam 移动确认接口请求过于频繁",
                http_status=status_code,
                retry_after=retry_after,
                response=response,
            )
        if status_code != 200:
            reason = getattr(response, "reason", None) or "Steam 移动确认接口返回非成功状态"
            raise ConfirmationHttpError(
                stage,
                detail=cls._response_message(response_json, reason) if isinstance(response_json, dict) else reason,
                http_status=status_code,
                retry_after=retry_after,
                response=response,
            )
        if isinstance(response_json, dict) and response_json.get("needauth"):
            raise ConfirmationAuthRequired(
                stage,
                detail=cls._response_message(response_json, "Steam 要求重新登录"),
                http_status=status_code,
                retry_after=retry_after,
                response=response,
            )
        if cls.INVALID_AUTHENTICATOR_MESSAGE in response.text:
            raise InvalidAuthenticatorError(
                stage,
                detail="Steam 拒绝了当前移动令牌签名，请检查 identity_secret 和系统时间",
                http_status=status_code,
                response=response,
            )
        if json_error is not None or not isinstance(response_json, dict):
            raise ConfirmationMalformedResponse(
                stage,
                detail="Steam 返回的移动确认响应不是有效 JSON 对象",
                http_status=status_code,
                response=response,
            ) from json_error
        return response_json

    @staticmethod
    def _is_login_response(response: requests.Response) -> bool:
        trusted_hosts = {"steamcommunity.com", "login.steampowered.com", "store.steampowered.com"}
        response_url = getattr(response, "url", "")
        response_host = urlsplit(response_url).hostname
        urls = [getattr(response, "url", "")]
        urls.extend(getattr(item, "url", "") for item in getattr(response, "history", []))
        location = response.headers.get("Location")
        if location:
            urls.append(location)
        for url in urls:
            if not url:
                continue
            parsed_url = urlsplit(url)
            host = parsed_url.hostname or response_host
            if host in trusted_hosts and "/login" in parsed_url.path.lower():
                return True
        return False

    @staticmethod
    def _response_message(response_json: dict, default: str) -> str:
        message = response_json.get("message") or response_json.get("detail") or default
        return str(message).replace("\r", " ").replace("\n", " ")[:300]

    @classmethod
    def _log_mobileconf_response(cls, response: requests.Response, stage: str, response_json) -> None:
        parsed_url = urlsplit(getattr(response, "url", ""))
        final_url = urlunsplit((parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", ""))
        success = response_json.get("success") if isinstance(response_json, dict) else None
        needauth = response_json.get("needauth") if isinstance(response_json, dict) else None
        message = cls._response_message(response_json, "") if isinstance(response_json, dict) else ""
        logger.debug(
            "Steam mobileconf响应: stage=%s, status=%s, final_url=%s, content_type=%s, retry_after=%s, success=%s, needauth=%s, message=%r",
            stage,
            response.status_code,
            final_url,
            response.headers.get("Content-Type"),
            response.headers.get("Retry-After"),
            success,
            needauth,
            message,
        )

    def _create_confirmation_params(self, tag_string: str) -> dict:
        timestamp = int(time.time())
        confirmation_key = guard.generate_confirmation_key(self._identity_secret, tag_string, timestamp)
        android_id = guard.generate_device_id(self._my_steam_id)
        return {"p": android_id, "a": self._my_steam_id, "k": confirmation_key, "t": timestamp, "m": "android", "tag": tag_string}

    def _select_trade_offer_confirmation(self, confirmations: List[Confirmation], trade_offer_id: str, match_end: bool = False) -> Confirmation:
        for confirmation in confirmations:
            confirmation_details_page = self._fetch_confirmation_details_page(confirmation)
            confirmation_id = self._get_confirmation_trade_offer_id(confirmation_details_page)
            if confirmation_id == "" or confirmation_id is None or not confirmation_id.isdigit():
                confirmation_id = confirmation.creator_id
            if confirmation_id == trade_offer_id:
                return confirmation
            elif match_end and trade_offer_id.endswith(confirmation_id):
                return confirmation
        raise ConfirmationNotReady("查找交易报价移动确认", detail=f"报价 {trade_offer_id} 对应的确认尚未出现")

    def _select_sell_listing_confirmation(self, confirmations: List[Confirmation], asset_id: str) -> Confirmation:
        for confirmation in confirmations:
            confirmation_details_page = self._fetch_confirmation_details_page(confirmation)
            confirmation_id = self._get_confirmation_sell_listing_id(confirmation_details_page)
            if confirmation_id == asset_id:
                return confirmation
        raise ConfirmationNotFound("查找市场上架移动确认", detail=f"未找到资产 {asset_id} 对应的确认")

    @staticmethod
    def _get_confirmation_sell_listing_id(confirmation_details_page: str) -> str:
        soup = BeautifulSoup(confirmation_details_page, "html.parser")
        scr_raw = soup.select("script")[2].string.strip()
        scr_raw = scr_raw[scr_raw.index("'confiteminfo', ") + 16 :]
        scr_raw = scr_raw[: scr_raw.index(", UserYou")].replace("\n", "")
        return json.loads(scr_raw)["id"]

    @staticmethod
    def _get_confirmation_trade_offer_id(confirmation_details_page: str) -> str:
        soup = BeautifulSoup(confirmation_details_page, "html.parser")
        trade_offer_id = soup.select(".tradeoffer")
        if len(trade_offer_id) != 0:
            full_offer_id = soup.select(".tradeoffer")[0]["id"]
            return full_offer_id.split("_")[1]
        else:
            div = soup.select("div")
            if len(div) > 3:
                return soup.select("div")[3].text.replace("\r", "").replace("\n", "").replace("\t", "")
            else:
                return ""
