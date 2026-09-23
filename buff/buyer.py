import json
import time
import logging
import copy
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional
from urllib.parse import urljoin

logger = logging.getLogger(__name__)
import requests
import urllib3
from requests.cookies import RequestsCookieJar

from .request_policy import (
    BuffAuthExpired,
    BuffRateLimited,
    BuffRequestBlocked,
    BuffRequestPolicy,
    BuffRiskControlTriggered,
    BuffVerificationRequired,
    BuffWriteResultUnknown,
    account_fingerprint,
    get_global_policy,
    parse_retry_after,
)
from utils.delay import jittered_sleep

BUFF_COOKIE_DOMAIN = "buff.163.com"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def _is_auth_error(status_code: int, data: dict) -> bool:
    if status_code == 401:
        return True
    code = str(data.get("code", "")).strip().casefold()
    message = str(
        data.get("error") or data.get("msg") or data.get("message") or ""
    ).strip().casefold()
    # Do not match every response containing "login/登录".  BUFF also uses
    # login-related wording for verification states (for example a Steam login
    # verification), and treating those as an expired BUFF session creates the
    # wrong recovery flow.
    return code in {
        "login required",
        "login_required",
        "unauthorized",
        "not logged in",
    } or any(marker in message for marker in ("未登录", "登录失效"))


def _is_definitive_login_rejection(status_code: int, data: dict) -> bool:
    """Return true only for BUFF's verified application-level auth rejection.

    The current BUFF web client handles HTTP 200 + ``Login Required`` as a
    rejected API operation and immediately opens its login UI.  Transport
    failures, redirects, HTML and non-2xx responses remain ambiguous after a
    write and must not use this narrow classification.
    """

    return (
        status_code == 200
        and str(data.get("code") or "").strip().casefold() == "login required"
    )


def _timezone_offset_dst_ms() -> str:
    """Match the millisecond UTC offset sent by BUFF's browser client."""

    local = time.localtime()
    seconds_west = (
        time.altzone
        if local.tm_isdst > 0 and time.daylight
        else time.timezone
    )
    return str(int(-seconds_west * 1000))


def _history_item_is_awaiting_payment(item: dict) -> bool:
    """Apply the state rule used by BUFF's current buy-order frontend."""

    state = str(item.get("state") or "").strip().upper()
    try:
        expires = float(item.get("pay_expire_timeout", 0))
    except (TypeError, ValueError):
        return False
    return state == "PAYING" and expires > -1

def _is_verification_required(data: dict) -> bool:
    text = str(data.get("error") or data.get("msg") or data.get("message") or "").lower()
    code = str(data.get("code", "")).lower()
    haystack = f"{code} {text}"
    markers = (
        "页面已过期",
        "刷新当前页面",
        "人机验证",
        "captcha",
        "challenge",
        "risk",
        "访问过于频繁",
        "安全验证",
    )
    return any(marker in haystack for marker in markers)

def _is_rate_limited(status_code: int, data: dict) -> bool:
    if status_code == 429:
        return True
    code = str(data.get("code", "")).lower()
    text = str(data.get("error") or data.get("msg") or data.get("message") or "").lower()
    haystack = f"{code} {text}"
    return any(
        marker in haystack
        for marker in (
            "too many requests",
            "rate limit",
            "rate_limit",
            "cooling down",
            "请求过于频繁",
            "请求频繁",
            "操作频繁",
        )
    )

def _header_value(headers, name: str) -> str:
    if not headers:
        return ""
    direct = headers.get(name)
    if direct is not None:
        return str(direct)
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            return str(value)
    return ""

def _response_navigation(response) -> tuple[list[str], bool]:
    urls = []
    history = list(getattr(response, "history", None) or [])
    for item in history:
        item_url = str(getattr(item, "url", "") or "")
        if item_url:
            urls.append(item_url)
        location = _header_value(getattr(item, "headers", {}) or {}, "Location")
        if location:
            urls.append(urljoin(item_url, location))
    final_url = str(getattr(response, "url", "") or "")
    if final_url:
        urls.append(final_url)
    status_code = int(getattr(response, "status_code", 0) or 0)
    location = _header_value(getattr(response, "headers", {}) or {}, "Location")
    if location:
        urls.append(urljoin(final_url, location))
    return urls, bool(history or 300 <= status_code < 400)

def _navigation_kind(response) -> tuple[str, bool]:
    urls, redirected = _response_navigation(response)
    haystack = " ".join(urls).lower()
    if any(marker in haystack for marker in ("/login", "/account/login", "passport")):
        return "login", redirected
    if any(marker in haystack for marker in ("/verify", "captcha", "challenge", "risk")):
        return "verification", redirected
    return "", redirected

def _looks_like_html(headers, text: str) -> bool:
    content_type = _header_value(headers, "Content-Type").lower()
    stripped = str(text or "").lstrip().lower()
    return "text/html" in content_type or stripped.startswith(("<!doctype html", "<html"))

def _html_looks_like_login(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in ("/login", "登录", "未登录", "sign in"))
# BUFF labels 49 as the general Alipay channel.  Method 51 is the separate
# "Alipay - credit card/Huabei" channel and must not be used as the default.
PAY_METHOD_ALIPAY = 49
PAY_METHOD_WECHAT = 6
API_HISTORY = "https://buff.163.com/api/market/buy_order/history"
API_USER_INFO = "https://buff.163.com/account/api/user/info/v2"
API_SELL_ORDER = "https://buff.163.com/api/market/goods/sell_order"
API_GOODS = "https://buff.163.com/api/market/goods"
API_BUY_PREVIEW = "https://buff.163.com/api/market/goods/buy/preview"
API_BUY = "https://buff.163.com/api/market/goods/buy"
API_PAGE_PAY = "https://buff.163.com/api/market/bill_order/page_pay"
API_WX_PAY_QRCODE = "https://buff.163.com/api/market/bill_order/wx_pay_qrcode"
API_BATCH_WX_PAY_QRCODE = "https://buff.163.com/api/market/goods/batch_buy/wx_pay_qrcode"
API_BATCH_PREVIEW = "https://buff.163.com/api/market/goods/batch_buy/preview"
API_BATCH_CREATE = "https://buff.163.com/api/market/goods/batch_buy/create"
API_BATCH_PAGE_PAY = "https://buff.163.com/api/market/goods/batch_buy/page_pay"
API_BATCH_CHECK_STATE = "https://buff.163.com/api/market/goods/batch_buy/check_state"
API_ASK_SELLER_SEND = "https://buff.163.com/api/market/bill_order/ask_seller_to_send_offer"
API_STEAM_TRADE = "https://buff.163.com/api/market/steam_trade"
def _parse_cookies(cookie_str: str) -> dict:
    out = {}
    for item in cookie_str.split(";"):
        s = item.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out
def _csrf(cookies_dict: dict) -> str:
    return cookies_dict.get("csrf_token", "").strip('"')

def _normalize_server_identifier(value) -> str:
    """Accept only scalar, non-empty IDs from successful write responses."""

    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    normalized = str(value).strip()
    return normalized if normalized and normalized != "0" else ""

def _cookie_jar_dict(cookie_jar: RequestsCookieJar) -> dict:
    """Select the most specific cookies applicable to BUFF API paths."""

    selected = {}
    for cookie in cookie_jar:
        if cookie.is_expired():
            continue
        domain = str(cookie.domain or "").lstrip(".").lower()
        if domain and domain != BUFF_COOKIE_DOMAIN:
            continue
        path = str(cookie.path or "/")
        if not "/api/".startswith(path.rstrip("/") + "/") and path != "/":
            continue
        priority = (len(path), 1 if domain == BUFF_COOKIE_DOMAIN else 0)
        previous = selected.get(cookie.name)
        if previous is None or priority >= previous[0]:
            selected[cookie.name] = (priority, cookie.value)
    return {name: value for name, (_, value) in selected.items()}

class BuffBuyer:
    def __init__(
        self,
        cookie_str: str,
        pay_method: int = PAY_METHOD_ALIPAY,
        use_ssl: bool = True,
        *,
        user_agent: Optional[str] = None,
        session: Optional[requests.Session] = None,
        request_policy: Optional[BuffRequestPolicy] = None,
        account_id: Optional[str] = None,
        request_timeout: float = 10.0,
        steam_id: Optional[str] = None,
    ):
        self.cookies_dict = _parse_cookies(cookie_str)
        self.csrf_token = _csrf(self.cookies_dict)
        self.pay_method = pay_method
        self.use_ssl = use_ssl
        self.session = session if session is not None else requests.Session()
        self._owns_session = session is None
        self._closed = False
        if self._owns_session:
            # Authenticated BUFF traffic must not silently switch exits through
            # HTTP_PROXY/HTTPS_PROXY or OS proxy settings between requests.
            self.session.trust_env = False
        for name, value in self.cookies_dict.items():
            for old_cookie in list(self.session.cookies):
                old_domain = str(old_cookie.domain or "").lstrip(".").lower()
                if (
                    old_cookie.name == name
                    and old_domain in {"", BUFF_COOKIE_DOMAIN}
                    and (old_cookie.path or "/") == "/"
                ):
                    self.session.cookies.clear(
                        domain=old_cookie.domain,
                        path=old_cookie.path,
                        name=old_cookie.name,
                    )
            self.session.cookies.set(
                name,
                value,
                domain=BUFF_COOKIE_DOMAIN,
                path="/",
                secure=True,
            )
        self.request_policy = (
            request_policy if request_policy is not None else get_global_policy()
        )
        self.request_timeout = max(1.0, float(request_timeout))
        self.account_key = account_fingerprint(self.cookies_dict, account_id)
        self._user_agent = user_agent or DEFAULT_USER_AGENT
        self.steam_id = str(steam_id or "").strip()
        self._buff_cashier_trace_id = ""
        self._batch_quote = None
        self._batch_context = None
        self.headers = {
            "Host": "buff.163.com",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "X-Csrftoken": self.csrf_token,
            "User-Agent": self._user_agent,
            "Content-Type": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self.session.headers.update({"User-Agent": self.headers["User-Agent"]})

    @property
    def user_agent(self) -> str:
        """The stable UA bound to this authenticated session."""

        return self._user_agent

    def export_cookie_string(self) -> str:
        """Return the session's latest cookies after any Set-Cookie rotations."""

        self._sync_session_cookies()
        return "; ".join(
            f"{name}={value}" for name, value in self.cookies_dict.items()
        )

    def close(self) -> None:
        """Release an internally-created Session during credential hot reload."""

        if self._owns_session and not self._closed:
            self.session.close()
            self._closed = True

    def _sync_session_cookies(self) -> None:
        self.session.cookies.clear_expired_cookies()
        self.cookies_dict = _cookie_jar_dict(self.session.cookies)
        self.csrf_token = _csrf(self.cookies_dict)

    def _merge_response_cookies(self, response_cookies: RequestsCookieJar) -> None:
        """Apply rotated cookies without retaining an older duplicate by domain."""

        for response_cookie in list(response_cookies):
            new_cookie = copy.copy(response_cookie)
            if not new_cookie.domain:
                new_cookie.domain = BUFF_COOKIE_DOMAIN
                new_cookie.domain_specified = True
                new_cookie.domain_initial_dot = False
            if not new_cookie.path:
                new_cookie.path = "/"
                new_cookie.path_specified = True
            new_domain = str(new_cookie.domain or "").lstrip(".").lower()
            # Collapse only equivalent root-scoped BUFF cookies.  Legitimate
            # same-name cookies on a narrower path remain untouched.
            for old_cookie in list(self.session.cookies):
                old_domain = str(old_cookie.domain or "").lstrip(".").lower()
                if (
                    old_cookie.name == new_cookie.name
                    and old_domain == new_domain == BUFF_COOKIE_DOMAIN
                    and (old_cookie.path or "/") == (new_cookie.path or "/")
                ):
                    self.session.cookies.clear(
                        domain=old_cookie.domain,
                        path=old_cookie.path,
                        name=old_cookie.name,
                    )
            if not new_cookie.is_expired():
                self.session.cookies.set_cookie(new_cookie)

    def reset_request_circuit(self) -> None:
        """Explicitly re-enable this account after successful browser verification."""

        self.request_policy.reset(self.account_key)

    clear_request_circuit = reset_request_circuit

    def _make_request(self, method: str, url: str, **kwargs) -> dict:
        method = method.upper()
        headers_override = kwargs.pop("headers", None)
        with_cashier_trace_id = bool(kwargs.pop("with_cashier_trace_id", False))
        verify = kwargs.pop("verify", self.use_ssl)
        timeout = kwargs.pop("timeout", self.request_timeout)
        is_write = method not in _SAFE_METHODS

        with self.request_policy.request_slot(self.account_key):
            # Cookie state and the outbound CSRF header must be captured only
            # after this account owns the request slot.  Otherwise a waiting
            # writer can retain a token rotated by the preceding response.
            self._sync_session_cookies()
            h = self.headers.copy()
            if method in _SAFE_METHODS:
                h.pop("Content-Type", None)
            if headers_override:
                for key, value in headers_override.items():
                    if value is None:
                        h.pop(key, None)
                    else:
                        h[key] = value
            if is_write:
                if not self.csrf_token:
                    raise BuffAuthExpired(
                        "BUFF csrf_token 缺失，写请求已在本地阻止"
                    )
                self.headers["X-Csrftoken"] = self.csrf_token
                h["X-Csrftoken"] = self.csrf_token
                h.setdefault("Origin", "https://buff.163.com")
                h.setdefault("Timezone-Offset-DST", _timezone_offset_dst_ms())
            if with_cashier_trace_id and self._buff_cashier_trace_id:
                h["Buff-Cashier-Trace-ID"] = self._buff_cashier_trace_id

            try:
                r = self.session.request(
                    method,
                    url,
                    headers=h,
                    verify=verify,
                    timeout=timeout,
                    **kwargs,
                )
            except requests.RequestException as exc:
                if is_write:
                    raise BuffWriteResultUnknown(
                        "BUFF 写请求发生网络异常，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                    ) from exc
                raise

            response_cookies = getattr(r, "cookies", None)
            if response_cookies is not None:
                self._merge_response_cookies(response_cookies)
            self._sync_session_cookies()
            status_code = int(getattr(r, "status_code", 0) or 0)
            response_text = str(getattr(r, "text", "") or "")
            response_headers = getattr(r, "headers", {}) or {}
            response_trace_id = _header_value(
                response_headers, "Buff-Cashier-Trace-ID"
            ).strip()
            if response_trace_id and len(response_trace_id) <= 256:
                # BUFF issues this value in a response and its web client reuses
                # it from sessionStorage.  Never invent a random trace id.
                self._buff_cashier_trace_id = response_trace_id
            json_ok = False
            data = {}
            try:
                parsed = r.json() if response_text else None
                if isinstance(parsed, dict):
                    data = parsed
                    json_ok = True
            except ValueError:
                pass

            if status_code in {403, 412}:
                message = (
                    data.get("error")
                    or data.get("msg")
                    or data.get("message")
                    or f"BUFF 风控响应 HTTP {status_code}"
                )
                self.request_policy.trip_risk_control(
                    self.account_key, status_code=status_code, message=str(message)
                )
                if is_write:
                    raise BuffWriteResultUnknown(
                        f"BUFF 写请求触发风控响应 HTTP {status_code}，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                raise BuffRiskControlTriggered(str(message), status_code=status_code)

            if _is_rate_limited(status_code, data):
                retry_after = parse_retry_after(
                    _header_value(response_headers, "Retry-After") or None
                )
                message = data.get("error") or data.get("msg") or "BUFF 请求过于频繁"
                self.request_policy.trip_rate_limit(
                    self.account_key, retry_after, str(message)
                )
                if is_write:
                    raise BuffWriteResultUnknown(
                        "BUFF 写请求触发限流响应，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                raise BuffRateLimited(retry_after, str(message))

            navigation_kind, redirected = _navigation_kind(r)
            if navigation_kind == "login":
                if is_write:
                    raise BuffWriteResultUnknown(
                        "BUFF 写请求已重定向到登录页，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                raise BuffAuthExpired("BUFF API 已重定向到登录页")
            if navigation_kind == "verification" or redirected:
                message = "BUFF API 重定向到安全验证页" if navigation_kind else "BUFF API 出现非预期重定向"
                self.request_policy.trip_verification(self.account_key, message)
                if is_write:
                    raise BuffWriteResultUnknown(
                        f"{message}，写请求结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                raise BuffVerificationRequired(message)

            if _is_definitive_login_rejection(status_code, data):
                raise BuffAuthExpired(
                    "BUFF 明确拒绝请求：登录状态已失效，订单未创建"
                )
            if _is_auth_error(status_code, data):
                if is_write:
                    raise BuffWriteResultUnknown(
                        "BUFF 写请求返回登录失效状态，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                raise BuffAuthExpired()
            if _is_verification_required(data) or _is_verification_required(
                {"message": response_text}
            ):
                msg = (
                    data.get("error")
                    or data.get("msg")
                    or data.get("message")
                    or "Buff 需要刷新页面或完成人机验证"
                )
                self.request_policy.trip_verification(self.account_key, str(msg))
                if is_write:
                    raise BuffWriteResultUnknown(
                        "BUFF 写请求返回安全验证状态，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                raise BuffVerificationRequired(str(msg))

            if is_write and status_code >= 500:
                raise BuffWriteResultUnknown(
                    f"BUFF 写请求返回 HTTP {status_code}，结果未知，禁止自动重试",
                    method=method,
                    url=url,
                    status_code=status_code,
                )

            if _looks_like_html(response_headers, response_text):
                if _html_looks_like_login(response_text):
                    if is_write:
                        raise BuffWriteResultUnknown(
                            "BUFF 写请求返回登录页面，结果未知，禁止自动重试",
                            method=method,
                            url=url,
                            status_code=status_code,
                        )
                    raise BuffAuthExpired("BUFF API 返回登录页面")
                if is_write:
                    message = "BUFF 写请求返回非预期 HTML，已停止后续请求"
                    self.request_policy.trip_verification(
                        self.account_key, message
                    )
                    raise BuffWriteResultUnknown(
                        "BUFF 写请求返回 HTML，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                message = "BUFF API 返回非预期 HTML，已停止后续请求"
                self.request_policy.trip_verification(self.account_key, message)
                raise BuffVerificationRequired(message)

            if is_write and not json_ok:
                raise BuffWriteResultUnknown(
                    "BUFF 写请求未返回有效 JSON，结果未知，禁止自动重试",
                    method=method,
                    url=url,
                    status_code=status_code,
                )

            if not json_ok:
                return {
                    "code": "HTTP_" + str(status_code),
                    "error": f"接口返回非预期内容 (Status: {status_code})",
                }
            return data

    def verify_session(self, game: str = "csgo") -> bool:
        """Verify credentials through BUFF's current user-info endpoint."""

        # BUFF's own web client uses this read-only endpoint to load the signed-
        # in user and buy-order metadata.  The history endpoint is unsuitable as
        # a login probe because its state choices change independently of auth.
        response = self._make_request(
            "GET",
            API_USER_INFO,
            params={"meta_list": "buy_order_state"},
            headers={"Referer": "https://buff.163.com/"},
        )
        data = response.get("data")
        user_info = data.get("user_info") if isinstance(data, dict) else None
        verified = (
            response.get("code") == "OK"
            and isinstance(user_info, dict)
            and bool(user_info)
        )
        if verified:
            steam_id = _normalize_server_identifier(user_info.get("steamid"))
            if steam_id:
                # BUFF's own selected/bound Steam account is authoritative for
                # purchase preview and checkout.  Do not substitute an ID from
                # an unrelated local Steam session.
                self.steam_id = steam_id
        return verified

    def get_steam_trades(self) -> Optional[list]:
        """Return pending BUFF-to-Steam trade records through this session."""

        headers = {
            "Referer": "https://buff.163.com/market/buy_order/to_receive?game=csgo"
        }
        params = {"_": str(int(time.time() * 1000))}
        try:
            response = self._make_request(
                "GET", API_STEAM_TRADE, params=params, headers=headers
            )
            if response.get("code") != "OK":
                return None
            data = response.get("data")
            return data if isinstance(data, list) else None
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except Exception:
            return None

    def check_wait_pay_orders(self, game: str = "csgo") -> bool:
        params = {
            "game": game,
            "page_num": "1",
            "page_size": "10",
            "_": str(int(time.time() * 1000)),
        }
        try:
            res = self._make_request("GET", API_HISTORY, params=params)
            if res.get("code") != "OK":
                logger.warning(
                    "检查待付款订单失败 code=%s: %s",
                    res.get("code", "UNKNOWN"),
                    res.get("error") or res.get("msg") or "未知错误",
                )
                return False
            all_items = res.get("data", {}).get("items", [])
            items = [
                item
                for item in all_items
                if isinstance(item, dict)
                and _history_item_is_awaiting_payment(item)
                and _normalize_server_identifier(item.get("id"))
            ]
            if items:
                logger.info("检测到 %d 个待付款订单，正在获取支付链接...", len(items))
                for item in items:
                    if self.pay_method == PAY_METHOD_WECHAT:
                        self._fetch_wechat_url(game, item["id"])
                    else:
                        self._fetch_pay_url(game, item["id"])
                return True
            return False
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except Exception as e:
            logger.exception("检查订单失败: %s", e)
        return False
    def get_sell_orders(self, goods_id: int, game: str = "csgo") -> Optional[list]:
        params = {
            "game": str(game),
            "goods_id": str(goods_id),
            "page_num": "1",
            "sort_by": "default",
            "mode": "",
            "allow_tradable_cooldown": "1",
            "_": str(int(time.time() * 1000)),
        }
        h = {"Referer": f"https://buff.163.com/goods/{goods_id}"}
        try:
            data = self._make_request("GET", API_SELL_ORDER, params=params, headers=h)
            if data.get("code") != "OK":
                logger.warning(
                    "BUFF 卖单接口返回非 OK，停止本轮请求: %s",
                    data.get("code", "UNKNOWN"),
                )
                return None
            return data.get("data", {}).get("items", []) or None
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except Exception:
            return None
    def get_goods_steam_price_cny(self, search_name: str, game: str = "csgo") -> Optional[float]:
        params = {
            "game": game,
            "page_num": "1",
            "search": search_name.strip(),
            "tab": "selling",
            "_": str(int(time.time() * 1000)),
        }
        h = {"Referer": "https://buff.163.com/market/csgo"}
        try:
            data = self._make_request("GET", API_GOODS, params=params, headers=h)
            if data.get("code") != "OK":
                return None
            items = data.get("data", {}).get("items", [])
            if not items:
                return None
            goods_info = items[0].get("goods_info") or {}
            raw = goods_info.get("steam_price_cny")
            if raw is None:
                return None
            return float(raw)
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except (ValueError, TypeError, KeyError):
            return None
        except Exception:
            return None

    def preview_buy(
        self,
        game: str,
        goods_id: int,
        sell_order_id: str,
        price: str,
    ) -> dict:
        """Run BUFF's current read-only purchase preview before creating an order."""

        if not self.steam_id:
            if not self.verify_session():
                raise BuffAuthExpired(
                    "BUFF 在线会话预检失败，未发送锁单请求"
                )
        if not self.steam_id:
            raise BuffRequestBlocked(
                "BUFF 账户未返回已绑定的 SteamID，已在本地阻止锁单"
            )
        params = {
            "game": str(game),
            "sell_order_id": str(sell_order_id),
            "goods_id": str(goods_id),
            "price": str(price),
            "allow_tradable_cooldown": "0",
            "cdkey_id": "",
            "steamid": self.steam_id or None,
        }
        headers = {
            "Referer": f"https://buff.163.com/goods/{goods_id}?from=market"
        }
        return self._make_request(
            "GET",
            API_BUY_PREVIEW,
            params=params,
            headers=headers,
        )

    def _preview_payment_error(self, preview_data: dict) -> str:
        pay_methods = preview_data.get("pay_methods")
        if not isinstance(pay_methods, list):
            return "BUFF 购买预检未返回支付方式"
        selected = next(
            (
                item
                for item in pay_methods
                if isinstance(item, dict)
                and str(item.get("value")) == str(self.pay_method)
            ),
            None,
        )
        if selected is None:
            return f"BUFF 购买预检不支持当前支付方式 {self.pay_method}"
        if selected.get("btn_clickable") is not True:
            return str(
                selected.get("error")
                or selected.get("btn_text")
                or "BUFF 购买预检显示当前支付方式不可用"
            )
        return ""

    def get_and_buy(
        self,
        goods_id: int,
        price_tolerance: float,
        game: str = "csgo",
    ) -> None:
        """Compatibility helper that performs at most one purchase write.

        Callers that need candidate fallback must reconcile the first write
        before selecting another sell order.  A failed BUFF response alone is
        not sufficient evidence that no order was created.
        """
        try:
            items = self.get_sell_orders(goods_id, game) or []
            if not items:
                logger.info("当前无人上架 (ID: %s)", goods_id)
                return

            base_price = float(items[0]["price"])
            first = items[0]
            current_price = float(first["price"])
            price_diff = current_price - base_price
            if price_diff > float(price_tolerance):
                logger.warning(
                    "价格熔断：%s (差价 %.2f) > %s",
                    current_price,
                    price_diff,
                    price_tolerance,
                )
                return

            logger.info(
                "单次尝试购买 [%s] 价格: %s",
                first.get("user_id", "unknown"),
                current_price,
            )
            result = self._execute_post_buy(
                game, goods_id, first["id"], first["price"]
            )
            if result == "SUCCESS":
                logger.info("购买流程结束。")
            elif result == "COOLING_DOWN":
                logger.warning("触发频率限制/存在未付款订单，停止购买。")
            else:
                logger.warning("购买结果未成功，停止购买且不尝试下一卖单。")
        except (BuffAuthExpired, BuffRequestBlocked, BuffWriteResultUnknown):
            raise
        except Exception as e:
            logger.exception("运行流程异常: %s", e)
    def _execute_post_buy(
        self,
        game: str,
        goods_id: int,
        order_id: str,
        price: str,
    ) -> str:
        preview = self.preview_buy(game, goods_id, order_id, price)
        preview_data = preview.get("data")
        preview_error = (
            self._preview_payment_error(preview_data)
            if preview.get("code") == "OK" and isinstance(preview_data, dict)
            else ""
        )
        if (
            preview.get("code") != "OK"
            or not isinstance(preview_data, dict)
            or preview_error
        ):
            logger.warning(
                "购买预检未通过，未发送锁单请求: %s",
                preview_error
                or preview.get("error")
                or preview.get("msg")
                or preview.get("code"),
            )
            return "FAIL"
        payload = {
            "game": game,
            "goods_id": str(goods_id),
            "sell_order_id": order_id,
            "price": price,
            "pay_method": self.pay_method,
            "allow_tradable_cooldown": 0,
            "token": "",
            "cdkey_id": "",
            "hide_non_epay": True,
            "steamid": self.steam_id or None,
        }
        h = {"Referer": f"https://buff.163.com/goods/{goods_id}?from=market"}
        try:
            res = self._make_request(
                "POST",
                API_BUY,
                headers=h,
                data=json.dumps(payload),
                with_cashier_trace_id=True,
            )
            if res.get("code") == "OK":
                data = res.get("data")
                if not isinstance(data, dict):
                    raise BuffWriteResultUnknown(
                        "BUFF 锁单返回成功但 data 格式异常，结果未知",
                        method="POST",
                        url=API_BUY,
                    )
                new_order_id = _normalize_server_identifier(data.get("id"))
                if not new_order_id:
                    raise BuffWriteResultUnknown(
                        "BUFF 锁单返回成功但缺少订单号，结果未知",
                        method="POST",
                        url=API_BUY,
                    )
                logger.info("锁单成功！订单号: %s", new_order_id)
                if self.pay_method == PAY_METHOD_WECHAT:
                    jittered_sleep(0.5)
                    self._fetch_wechat_url(game, new_order_id)
                else:
                    self._fetch_pay_url(game, new_order_id)
                return "SUCCESS"
            error_code = str(res.get("code", ""))
            err_msg = res.get("error") or res.get("msg") or f"接口返回异常 Code: {error_code}"
            msg = str(err_msg)
            if "Cooling Down" in error_code or "Cooling Down" in msg:
                return "COOLING_DOWN"
            if error_code == "Error":
                logger.warning("卖家不支持当前支付方式 (Code: Error)")
                return "FAIL"
            logger.warning("锁单失败: %s", err_msg)
            return "FAIL"
        except (BuffAuthExpired, BuffRequestBlocked, BuffWriteResultUnknown):
            raise
        except Exception as e:
            logger.exception("锁单异常: %s", e)
            return "FAIL"
    def _fetch_pay_url(self, game: str, order_id: str) -> Optional[str]:
        params = {
            "bill_order_id": str(order_id),
            "_": str(int(time.time() * 1000)),
        }
        h = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://buff.163.com/market/buy_order/history?game={game}",
        }
        try:
            logger.info("正在请求订单 %s 的支付链接 (GET)...", order_id)
            res = self._make_request("GET", API_PAGE_PAY, params=params, headers=h)
            if res.get("code") == "OK":
                data = res.get("data", {})
                elements_v2 = data.get("elements_v2", {})
                alipay_info = elements_v2.get("alipay", {})
                pay_url = (
                    alipay_info.get("url")
                    or data.get("elements", {}).get("url")
                    or data.get("url")
                )
                if pay_url:
                    logger.info("获取成功！支付链接如下：\n%s", pay_url)
                    logger.info("剩余支付时间: %ss", data.get('pay_expire_timeout', 'N/A'))
                    return pay_url
                logger.warning("接口返回 OK 但未找到 URL: %s", res)
            else:
                err = res.get("error") or res.get("msg") or f"未返回 error 字段 (Code: {res.get('code', 'N/A')})"
                logger.warning("支付接口返回错误: %s", err)
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except Exception as e:
            logger.exception("获取支付链接异常: %s", e)
        return None
    def lock_and_get_pay_url(
        self,
        game: str,
        goods_id: int,
        sell_order_id: str,
        price: str,
        *,
        on_created: Optional[Callable[[str], None]] = None,
        preview: Optional[dict] = None,
    ) -> dict:
        if preview is None:
            preview = self.preview_buy(game, goods_id, sell_order_id, price)
        if not isinstance(preview, dict):
            return {
                "success": False,
                "code": "PREVIEW_INVALID",
                "msg": "BUFF 购买预检返回格式异常",
                "created": False,
            }
        if preview.get("code") != "OK":
            return {
                "success": False,
                "code": str(preview.get("code") or "PREVIEW_REJECTED"),
                "msg": preview.get("error")
                or preview.get("msg")
                or "BUFF 购买预检未通过",
                "created": False,
            }
        preview_data = preview.get("data")
        if not isinstance(preview_data, dict):
            return {
                "success": False,
                "code": "PREVIEW_INVALID",
                "msg": "BUFF 购买预检返回格式异常",
                "created": False,
            }
        preview_error = self._preview_payment_error(preview_data)
        if preview_error:
            return {
                "success": False,
                "code": "PAY_METHOD_UNAVAILABLE",
                "msg": preview_error,
                "created": False,
            }
        payload = {
            "game": game,
            "goods_id": str(goods_id),
            "sell_order_id": sell_order_id,
            "price": price,
            "pay_method": self.pay_method,
            "allow_tradable_cooldown": 0,
            "token": "",
            "cdkey_id": "",
            "hide_non_epay": True,
            "steamid": self.steam_id or None,
        }
        h = {"Referer": f"https://buff.163.com/goods/{goods_id}?from=market"}
        try:
            res = self._make_request(
                "POST",
                API_BUY,
                headers=h,
                data=json.dumps(payload),
                with_cashier_trace_id=True,
            )
            if res.get("code") != "OK":
                err_msg = res.get("error") or res.get("msg") or f"接口代码非 OK (Code: {res.get('code', 'N/A')})"
                msg_str = str(err_msg)
                if "Cooling Down" in msg_str:
                    return {"success": False, "code": "COOLING_DOWN"}
                return {"success": False, "code": "FAIL", "msg": err_msg}
            data = res.get("data")
            if not isinstance(data, dict):
                raise BuffWriteResultUnknown(
                    "BUFF 锁单返回成功但 data 格式异常，结果未知",
                    method="POST",
                    url=API_BUY,
                )
            new_order_id = _normalize_server_identifier(data.get("id"))
            if not new_order_id:
                raise BuffWriteResultUnknown(
                    "BUFF 锁单返回成功但缺少订单号，结果未知",
                    method="POST",
                    url=API_BUY,
                )
            if on_created is not None:
                try:
                    on_created(new_order_id)
                except Exception as exc:
                    error = BuffWriteResultUnknown(
                        "BUFF 订单已创建，但订单号未能写入本地对账门禁",
                        method="POST",
                        url=API_BUY,
                    )
                    error.order_id = new_order_id
                    raise error from exc
            pay_type = "wechat" if self.pay_method == PAY_METHOD_WECHAT else "alipay"
            try:
                if self.pay_method == PAY_METHOD_WECHAT:
                    jittered_sleep(0.5)
                    pay_url = self._get_wechat_pay_url(game, new_order_id)
                else:
                    pay_url = self._get_alipay_url(game, new_order_id)
            except Exception as exc:
                logger.warning(
                    "BUFF 订单 %s 已创建，但获取支付链接失败: %s",
                    new_order_id,
                    exc,
                )
                pay_url = None
            if not isinstance(pay_url, str) or not pay_url.strip():
                return {
                    "success": False,
                    "code": "CREATED_WITHOUT_PAY_URL",
                    "created": True,
                    "pay_url": None,
                    "pay_type": pay_type,
                    "order_id": new_order_id,
                }
            pay_url = pay_url.strip()
            return {
                "success": True,
                "pay_url": pay_url,
                "pay_type": pay_type,
                "order_id": new_order_id,
            }
        except (BuffAuthExpired, BuffRequestBlocked, BuffWriteResultUnknown):
            raise
        except Exception as e:
            return {"success": False, "code": "FAIL", "msg": str(e)}
    def _get_alipay_url(self, game: str, order_id: str) -> Optional[str]:
        params = {"bill_order_id": str(order_id), "_": str(int(time.time() * 1000))}
        h = {
            "Accept": "application/json, text/javascript, */*; q=0.01", 
            "X-Requested-With": "XMLHttpRequest", 
            "Referer": f"https://buff.163.com/market/buy_order/history?game={game}"
        }
        try:
            res = self._make_request("GET", API_PAGE_PAY, params=params, headers=h)
            if res.get("code") == "OK":
                data = res.get("data", {})
                return data.get("elements_v2", {}).get("alipay", {}).get("url") or data.get("elements", {}).get("url") or data.get("url")
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except Exception:
            pass
        return None
    def _get_wechat_pay_url(self, game: str, order_id: str) -> Optional[str]:
        params = {"bill_order_id": str(order_id), "_": str(int(time.time() * 1000))}
        h = {"Referer": f"https://buff.163.com/market/buy_order/history?game={game}"}
        try:
            res = self._make_request("GET", API_WX_PAY_QRCODE, params=params, headers=h)
            if res.get("code") == "OK":
                data = res.get("data", {})
                return data.get("url") or data.get("elements_v2", {}).get("wechatpay", {}).get("url")
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except Exception:
            pass
        return None
    def _fetch_wechat_url(self, game: str, order_id: str) -> None:
        pay_url = self._get_wechat_pay_url(game, order_id)
        if pay_url:
            logger.info("链接获取成功，正在生成二维码...")
            self._generate_qr_code(pay_url)
        else:
            logger.warning("未找到微信支付 URL")
            
    def _generate_qr_code(self, url: str) -> None:
        try:
            import qrcode
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            img.show()
            logger.info("二维码已弹出，请用微信扫码！")
            logger.info("进入支付等待期 30 秒...")
            time.sleep(30)
        except ImportError:
            logger.warning("未安装 qrcode，请执行: pip install qrcode[pil]")
            logger.info("支付链接: %s", url)
            time.sleep(30)
        except Exception as e:
            logger.exception("生成二维码失败: %s", e)
    @staticmethod
    def _batch_money(value) -> Decimal:
        try:
            amount = Decimal(str(value))
            if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
                raise ValueError
            return amount
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise BuffRequestBlocked("BUFF 批量价格无效，未发送购买请求") from exc

    def prepare_batch_buy(
        self, goods_id: int, game: str, orders: list, max_price: float, num: int,
    ) -> dict:
        """Preview only. Never fund a batch or silently switch to wallet spending."""
        active = getattr(self, "_batch_context", None)
        if active and len(active["completed"]) != len(active["orders"]):
            raise BuffRequestBlocked("BUFF 仍有未完成批次，请先核对订单和付款状态")
        self._batch_quote = None

        def rejected(message, code="NOT_SUPPORTED"):
            return {"success": False, "created": False, "code": code,
                    "safe_to_fallback": code == "NOT_SUPPORTED", "msg": message}

        if self.pay_method not in (PAY_METHOD_WECHAT, PAY_METHOD_ALIPAY):
            return rejected("当前支付方式尚不支持外部付款批次，不会自动切换余额支付")
        if isinstance(num, bool) or not isinstance(num, int) or num < 2:
            return rejected("批量购买至少需要两件物品", "PREVIEW_INVALID")
        ceiling = self._batch_money(max_price)
        selected = {}
        for row in orders:
            sell_id = _normalize_server_identifier(row.get("id"))
            try:
                price = self._batch_money(row.get("price"))
            except BuffRequestBlocked:
                continue
            if sell_id and sell_id not in selected and price <= ceiling:
                selected[sell_id] = format(price, ".2f")
            if len(selected) == num:
                break
        if len(selected) != num:
            return rejected("符合价格上限的独立卖单不足，未创建批次")
        if not self.steam_id and not self.verify_session():
            raise BuffAuthExpired("BUFF 在线会话预检失败，未发送批量购买请求")
        if not self.steam_id:
            raise BuffRequestBlocked("BUFF 账户未返回已绑定的 SteamID")
        payload = {"game": game, "goods_id": goods_id, "sell_orders": list(selected),
                   "select_epay": 1, "steamid": self.steam_id}
        try:
            response = self._make_request(
                "POST", API_BATCH_PREVIEW, data=json.dumps(payload),
                headers={"Referer": f"https://buff.163.com/goods/{goods_id}?from=market"},
            )
        except BuffWriteResultUnknown as exc:
            # This particular POST only quotes a purchase. Funding has not begun.
            raise BuffRequestBlocked("BUFF 批量预检请求失败，未发送创建或购买请求") from exc
        if response.get("code") != "OK":
            return rejected(str(response.get("error") or response.get("msg")
                                or "BUFF 批量预检被拒绝"), "PREVIEW_REJECTED")
        data = response.get("data")
        if not isinstance(data, dict) or not _normalize_server_identifier(data.get("batch_id")):
            return rejected("BUFF 批量预检缺少批次编号", "PREVIEW_INVALID")
        total = sum((Decimal(price) for price in selected.values()), Decimal("0"))
        try:
            quoted_total = self._batch_money(data.get("price"))
        except BuffRequestBlocked:
            return rejected("BUFF 批量预检总价无效", "PREVIEW_INVALID")
        if quoted_total != total:
            return rejected("BUFF 批量预检总价已变化，请刷新卖单", "PREVIEW_INVALID")
        methods = data.get("pay_methods")
        if not isinstance(methods, list) or not methods:
            return rejected("BUFF 批量预检未提供可用支付方式")
        methods = [method for method in methods if isinstance(method, dict)]
        # The website prefunds only these external WeChat/Alipay methods.
        # Single-checkout Alipay 51 must not be sent to batch_buy/create.
        supported = (6,) if self.pay_method == PAY_METHOD_WECHAT else (49, 10)
        candidates = [method for value in supported for method in methods
                      if str(method.get("value")) == str(value)]
        payment = next((method for method in candidates
                        if method.get("btn_clickable") is True and not method.get("error")), None)
        if payment is None:
            details = "; ".join(str(method.get("error") or method.get("name")
                                     or method.get("value")) for method in (candidates or methods))
            return rejected(f"BUFF 未提供当前支付方式的批量付款选项: {details}")
        if payment.get("free_password") not in (True, "true"):
            return rejected("批量付款需要在 BUFF 官网完成支付密码确认")
        if payment.get("sub_pay") or payment.get("ejzb_auth"):
            return rejected("批量付款需要额外授权或组合支付，请在 BUFF 官网处理")
        try:
            fee = Decimal(str(payment.get("pay_fee_rate", 0)))
        except InvalidOperation:
            fee = Decimal("NaN")
        if not fee.is_finite() or fee != 0:
            return rejected("批量付款包含未计入预算的手续费，请在 BUFF 官网确认")
        self._batch_quote = {
            "game": game, "goods_id": goods_id, "steamid": self.steam_id,
            "orders": selected, "max_price": ceiling, "total": total,
            "preview_id": str(data["batch_id"]), "pay_method": int(payment["value"]),
            "passback_params": copy.deepcopy(payment.get("passback_params") or ""),
            "hide_non_epay": any(method.get("collapse") for method in methods),
            "prepared_at": time.monotonic(),
        }
        return {"success": True, "created": False, "total_price": float(total)}

    def batch_buy_create(
        self,
        goods_id: int,
        max_price: float,
        num: int,
        game: str = "csgo",
    ) -> Optional[str]:
        quote = getattr(self, "_batch_quote", None)
        if (not quote or quote["goods_id"] != goods_id or quote["game"] != game
                or quote["steamid"] != self.steam_id or len(quote["orders"]) != num
                or quote["max_price"] != self._batch_money(max_price)
                or time.monotonic() - quote["prepared_at"] > 30):
            raise BuffRequestBlocked("BUFF 缺少匹配且有效的批量预检，未创建批次")
        self._batch_quote = None
        context = dict(quote, funding_id="", paid=False, attempted=set(), completed=set())
        self._batch_context = context
        payload = {"game": game, "goods_id": goods_id, "pay_method": quote["pay_method"],
                   "frozen_amount": format(quote["total"], ".2f"),
                   "max_price": format(quote["max_price"], ".2f"), "num": num,
                   "steamid": quote["steamid"]}
        try:
            response = self._make_request(
                "POST", API_BATCH_CREATE, data=json.dumps(payload),
                headers={"Referer": f"https://buff.163.com/goods/{goods_id}?from=market"},
                with_cashier_trace_id=True,
            )
        except (BuffAuthExpired, BuffRequestBlocked):
            self._batch_context = None
            raise
        except BuffWriteResultUnknown:
            raise
        except Exception as exc:
            raise BuffWriteResultUnknown("BUFF 批次创建结果未知，禁止重试", method="POST",
                                         url=API_BATCH_CREATE) from exc
        data = response.get("data")
        funding_id = (_normalize_server_identifier(data.get("batch_buy_id"))
                      if response.get("code") == "OK" and isinstance(data, dict) else "")
        if not funding_id:
            raise BuffWriteResultUnknown("BUFF 批次创建未返回有效付款批次号，请核对订单",
                                         method="POST", url=API_BATCH_CREATE)
        context["funding_id"] = funding_id
        return funding_id

    def batch_buy_pay_url(self, batch_id: str, game: str = "csgo") -> Optional[str]:
        context = self._require_batch_context(batch_id)
        if context["pay_method"] == PAY_METHOD_WECHAT:
            return self.batch_buy_wx_qrcode(batch_id, game)
        response = self._make_request(
            "GET", API_BATCH_PAGE_PAY, params={"batch_buy_id": batch_id},
            headers={"Referer": f"https://buff.163.com/goods/{context['goods_id']}?from=market"},
        )
        if response.get("code") != "OK":
            return None
        data = response.get("data") or {}
        return (data.get("elements_v2", {}).get("alipay", {}).get("url")
                or data.get("elements", {}).get("url") or data.get("url"))

    def _require_batch_context(self, batch_id: str) -> dict:
        context = getattr(self, "_batch_context", None)
        if (not context or not batch_id or context["funding_id"] != str(batch_id)
                or context["steamid"] != self.steam_id):
            raise BuffRequestBlocked("BUFF 批次与当前会话不匹配，请在官网核对已创建批次")
        return context

    def batch_buy_orders_for_finalize(
        self, goods_id: int, game: str, max_price: float, num: int, batch_id: str,
    ) -> list:
        context = self._require_batch_context(batch_id)
        if (context["goods_id"] != goods_id or context["game"] != game
                or context["max_price"] != self._batch_money(max_price)
                or len(context["orders"]) != num or context["attempted"]):
            raise BuffRequestBlocked("BUFF 批次参数已改变或已开始核销，请先核对订单")
        context["paid"] = False
        response = self._make_request(
            "GET", API_BATCH_CHECK_STATE, params={"batch_buy_id": str(batch_id)},
            headers={"Referer": f"https://buff.163.com/goods/{goods_id}?from=market"},
        )
        data = response.get("data")
        if (response.get("code") != "OK" or not isinstance(data, dict)
                or str(data.get("state")) != "2"):
            raise BuffRequestBlocked("BUFF 服务端尚未确认批次付款成功，未发送核销请求")
        context["paid"] = True
        # Keep the approved seller ids/prices. Do not spend a paid batch on
        # replacement listings that were never part of its preview.
        return [{"id": sell_id, "price": price} for sell_id, price in context["orders"].items()]
    def batch_buy_wx_qrcode(self, batch_id: str, game: str = "csgo") -> Optional[str]:
        context = self._require_batch_context(batch_id)
        params = {
            "batch_buy_id": str(batch_id),
            "_": str(int(time.time() * 1000)),
        }
        h = {"Referer": f"https://buff.163.com/goods/{context['goods_id']}?from=market"}
        try:
            res = self._make_request("GET", API_BATCH_WX_PAY_QRCODE, params=params, headers=h)
            if res.get("code") != "OK":
                return None
            data = res.get("data", {})
            return data.get("url") or data.get("qrcode") or None
        except (BuffAuthExpired, BuffRequestBlocked, BuffWriteResultUnknown):
            raise
        except Exception:
            return None
    def batch_buy_finalize(
        self,
        game: str,
        goods_id: int,
        sell_order_id: str,
        price: str,
        batch_buy_id: str,
    ) -> Optional[str]:
        context = self._require_batch_context(batch_buy_id)
        sell_order_id = str(sell_order_id)
        if (not context["paid"] or context["game"] != game or context["goods_id"] != goods_id
                or sell_order_id not in context["orders"] or sell_order_id in context["attempted"]
                or self._batch_money(price) != Decimal(context["orders"][sell_order_id])):
            raise BuffRequestBlocked("BUFF 核销必须匹配已付款批次，且同一卖单只能提交一次")
        payload = {
            "game": game, "goods_id": goods_id, "sell_order_id": sell_order_id,
            "price": context["orders"][sell_order_id], "batch": 1,
            "pay_method": context["pay_method"], "passback_params": context["passback_params"],
            "allow_tradable_cooldown": 0, "hide_non_epay": context["hide_non_epay"],
            "batch_id": context["preview_id"], "batch_buy_id": str(batch_buy_id),
            "steamid": context["steamid"],
        }
        context["attempted"].add(sell_order_id)
        try:
            response = self._make_request(
                "POST", API_BUY, data=json.dumps(payload),
                headers={"Referer": f"https://buff.163.com/goods/{goods_id}?from=market"},
                with_cashier_trace_id=True,
            )
        except (BuffAuthExpired, BuffRequestBlocked, BuffWriteResultUnknown):
            raise
        except Exception as exc:
            raise BuffWriteResultUnknown("BUFF 批量核销结果未知，禁止重试", method="POST",
                                         url=API_BUY) from exc
        data = response.get("data")
        bill_id = (_normalize_server_identifier(data.get("id"))
                   if response.get("code") == "OK" and isinstance(data, dict) else "")
        if not bill_id or bill_id in context["completed"]:
            raise BuffWriteResultUnknown("BUFF 批量核销未返回唯一有效订单号，请核对批次",
                                         method="POST", url=API_BUY)
        context["completed"].add(bill_id)
        return bill_id
    def ask_seller_to_send(self, bill_order_id_or_ids, game: str = "csgo") -> bool:
        if isinstance(bill_order_id_or_ids, (list, tuple)):
            raw_ids = bill_order_id_or_ids
        else:
            raw_ids = (
                [bill_order_id_or_ids]
                if bill_order_id_or_ids is not None
                else []
            )

        # One BUFF bill may only be prompted once in this pass.  Normalize and
        # de-duplicate without changing order so a duplicate local row cannot
        # consume a request slot while another bill is skipped.
        ids = []
        seen_ids = set()
        for raw_id in raw_ids:
            order_id = _normalize_server_identifier(raw_id)
            if not order_id or order_id in seen_ids:
                continue
            seen_ids.add(order_id)
            ids.append(order_id)
        if not ids:
            return False

        h = {"Referer": f"https://buff.163.com/market/buy_order/history?game={game}"}
        payload = {
            "bill_orders": ids,
            "game": game,
            "steamid": getattr(self, "steam_id", "") or None,
        }
        try:
            # BUFF's web client submits the complete bill list once.  Splitting
            # it into N writes both raises risk-control traffic and can make a
            # partially failed response look like full success.
            res = self._make_request(
                "POST",
                API_ASK_SELLER_SEND,
                headers=h,
                data=json.dumps(payload),
            )
        except (BuffAuthExpired, BuffRequestBlocked, BuffWriteResultUnknown):
            raise
        except Exception as exc:
            raise BuffWriteResultUnknown(
                "提醒卖家发货时发生异常，写请求结果未知，禁止自动重试",
                method="POST",
                url=API_ASK_SELLER_SEND,
            ) from exc

        if res.get("code") != "OK":
            logger.warning(
                "提醒卖家发货失败: %s",
                res.get("error")
                or res.get("msg")
                or res.get("message")
                or res.get("code")
                or "未知错误",
            )
            return False
        statuses = res.get("data")
        if not isinstance(statuses, dict):
            logger.warning("提醒卖家发货返回缺少逐订单状态")
            return False
        failed_ids = [
            order_id
            for order_id in ids
            if str(statuses.get(order_id) or "").strip().upper() != "OK"
        ]
        if failed_ids:
            logger.warning(
                "提醒卖家发货仅完成 %s/%s，失败订单: %s",
                len(ids) - len(failed_ids),
                len(ids),
                ", ".join(failed_ids),
            )
            return False
        logger.info("提醒卖家发货全部完成 %s/%s", len(ids), len(ids))
        return True
