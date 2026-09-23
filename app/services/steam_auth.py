"""
Steam authentication service – extracted from api.py.
Contains steampy-based login automation, profile fetching,
and auto-relogin logic.
"""
import re
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from typing import Optional, Tuple
from urllib.parse import unquote, urlparse
import urllib3
import requests as _req
from app.state import log
from app.config_loader import (
    get_steam_credentials,
    load_app_config_validated,
    update_steam_creds,
)
from app.accounts import (
    get_account,
    get_current_account,
    get_profile_dir,
    set_current,
    update_account,
)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_STEAM_AUTH_POLL_MAX_ATTEMPTS = 30
_STEAM_AUTH_POLL_INTERVAL_SECONDS = 1.0
_STEAM_AUTH_POLL_TIMEOUT_SECONDS = 60.0
_STEAM_AUTH_JOIN_TIMEOUT_SECONDS = 120.0
_STEAM_LOGIN_REQUEST_TIMEOUT = (10, 25)
_steampy_login_patch_lock = threading.Lock()


class SteamAuthTokenPending(RuntimeError):
    """Steam has not returned a refresh token within the polling window."""


def _verify_steam_cookies_valid(cookie_str: str, steam_id: str = "") -> Optional[bool]:
    """Return True/False for confirmed validity, None for an inconclusive check."""
    cookie_dict = {}
    for part in (cookie_str or "").split(";"):
        s = part.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            cookie_dict[k.strip()] = v.strip()
    if not cookie_dict.get("steamLoginSecure"):
        return False
    cookie_steam_id = steam_id_from_cookie_str(cookie_str)
    if not cookie_steam_id or (steam_id and cookie_steam_id != steam_id):
        return False
    session = _req.Session()
    session.verify = False
    session.cookies.update(cookie_dict)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    try:
        response = session.get(
            "https://steamcommunity.com/my/profile",
            timeout=12,
            allow_redirects=True,
        )
        final_url = urlparse(response.url or "")
        if final_url.scheme != "https" or final_url.hostname != "steamcommunity.com":
            return None
        if final_url.path.startswith("/login"):
            return False
        if response.status_code in (401, 403):
            return False
        if response.status_code != 200:
            return None
        profile = re.fullmatch(r"/profiles/(\d+)(?:/.*)?", final_url.path)
        if profile:
            return profile.group(1) == cookie_steam_id
        # /my/ redirects to the authenticated user's numeric or vanity profile.
        if response.history and re.fullmatch(r"/id/[^/]+(?:/.*)?", final_url.path):
            return True
        return None
    except _req.RequestException:
        return None
    finally:
        session.close()
def fetch_steam_profile_via_api(steam_id: str, cookies_str: str) -> tuple:
    if not steam_id:
        return "", ""
    display_name, avatar_url = "", ""
    session = _req.Session()
    session.verify = False
    cookie_dict = {}
    for part in (cookies_str or "").split(";"):
        s = part.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            cookie_dict[k.strip()] = v.strip()
    session.cookies.update(cookie_dict)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    })
    try:
        r = session.get(f"https://steamcommunity.com/miniprofile/{int(steam_id) - 76561197960265728}/json", timeout=15)
        if r.status_code == 200:
            data = r.json()
            display_name = (data.get("persona_name") or "").strip()
            avatar_url = (data.get("avatar_url") or "").strip()
            if avatar_url and not avatar_url.startswith("http"):
                avatar_url = "https://avatars.steamstatic.com/" + avatar_url
            if avatar_url and "_medium" in avatar_url:
                avatar_url = avatar_url.replace("_medium", "_full")
    except Exception:
        pass
    if display_name and avatar_url:
        return display_name, avatar_url
    try:
        r = session.get(f"https://steamcommunity.com/profiles/{steam_id}", params={"xml": "1"}, timeout=15)
        if r.status_code == 200 and r.text:
            if not display_name:
                name_m = re.search(r"<steamID><!\[CDATA\[(.+?)\]\]></steamID>", r.text)
                if name_m:
                    display_name = name_m.group(1).strip()
            if not avatar_url:
                avatar_m = re.search(r"<avatarFull><!\[CDATA\[(.+?)\]\]></avatarFull>", r.text)
                if avatar_m:
                    avatar_url = avatar_m.group(1).strip()
    except Exception:
        pass
    if display_name and avatar_url:
        return display_name, avatar_url
    try:
        r = session.get(f"https://steamcommunity.com/profiles/{steam_id}", timeout=15)
        if r.status_code == 200 and r.text:
            html = r.text
            if not display_name:
                for pat in [
                    r'class="actual_persona_name"[^>]*>([^<]+)<',
                    r'"personaname"\s*:\s*"([^"]+)"',
                    r'<title>Steam Community :: (.+?)</title>',
                ]:
                    m = re.search(pat, html)
                    if m:
                        display_name = m.group(1).strip()
                        break
            if not avatar_url:
                for pat in [
                    r'class="playerAvatarAutoSizeInner"[^>]*>\s*<img[^>]+src="([^"]+)"',
                    r'"avatarfull"\s*:\s*"([^"]+)"',
                    r'property="og:image"[^>]+content="([^"]+)"',
                ]:
                    m = re.search(pat, html)
                    if m:
                        avatar_url = m.group(1).strip().replace("\\/", "/")
                        break
    except Exception:
        pass
    return display_name, avatar_url
def _get_shared_secret() -> str:
    try:
        cfg = load_app_config_validated()
        raw = ((cfg.get("steam_guard") or {}).get("shared_secret") or "").strip()
        if raw:
            return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), raw)
        return ""
    except Exception:
        return ""
def _build_steam_guard_dict(cur: dict, cfg: dict) -> Optional[dict]:
    """Build the steam_guard dict that steampy SteamClient.login() expects.
    steampy accepts either a path to a .maFile or a dict with these fields:
    {
        "steamid": "...",
        "shared_secret": "...",
        "identity_secret": "...",
        "device_id": "...",
    }
    We assemble this from the app config and account info.
    """
    shared_secret = ((cfg.get("steam_guard") or {}).get("shared_secret") or "").strip()
    if shared_secret:
        shared_secret = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), shared_secret)
    identity_secret = ((cfg.get("steam_confirm") or {}).get("identity_secret") or "").strip()
    device_id       = ((cfg.get("steam_confirm") or {}).get("device_id") or "").strip()
    steam_id        = (cur.get("steam_id") or "").strip()
    if not shared_secret:
        return None  
    return {
        "steamid": steam_id,
        "shared_secret": shared_secret,
        "identity_secret": identity_secret,
        "device_id": device_id,
    }
def _short_error_detail(exc: Exception, limit: int = 220) -> str:
    detail = str(exc).strip()
    detail = re.sub(r"\s+", " ", detail)
    detail = re.sub(
        r"(https?://)[^/@\s]+@",
        r"\g<1>***@",
        detail,
        flags=re.IGNORECASE,
    )
    if len(detail) > limit:
        detail = detail[: limit - 3] + "..."
    return detail


def _classify_steam_login_exception(exc: Exception) -> str:
    detail = _short_error_detail(exc)
    err = detail.lower()
    if (
        "status code: 443" in err
        or "status code 443" in err
        or "http 443" in err
    ):
        return (
            "network_error: Steam 登录请求收到非标准 HTTP 443 响应；"
            "这通常由代理、加速器或安全网关返回，不是 Steam 账号错误。"
            f" 原始错误: {detail}"
        )
    if isinstance(exc, _req.exceptions.SSLError) or "ssl" in err or "certificate" in err:
        return (
            "network_error: Steam 登录 HTTPS/SSL 握手失败；通常是代理、加速器、"
            "证书拦截或本机网络环境导致。"
            f" 原始错误: {detail}"
        )
    network_markers = (
        "max retries exceeded",
        "newconnectionerror",
        "failed to establish a new connection",
        "connection refused",
        "connection reset",
        "connection aborted",
        "name resolution",
        "temporary failure in name resolution",
        "getaddrinfo failed",
        "timed out",
        "read timed out",
        "connect timeout",
        "proxy not working",
        "proxyconnectionerror",
    )
    request_network_types = (
        _req.exceptions.ConnectionError,
        _req.exceptions.Timeout,
        _req.exceptions.ProxyError,
    )
    if isinstance(exc, request_network_types) or any(m in err for m in network_markers):
        return (
            "network_error: Steam 登录网络连接失败，未能建立到 Steam 的 HTTPS 连接；"
            "错误中的 :443 是 HTTPS 端口，不是账号验证错误码。"
            "这不是账号密码或 Steam Guard 错误。"
            "请检查本机直连、加速器/代理、DNS 或稍后重试。"
            f" 原始错误: {detail}"
        )
    return detail[:120]


def _poll_steam_refresh_token(
    executor,
    client_id: str,
    request_id: str,
    *,
    max_attempts: int = _STEAM_AUTH_POLL_MAX_ATTEMPTS,
    interval_seconds: float = _STEAM_AUTH_POLL_INTERVAL_SECONDS,
    sleeper=None,
    clock=None,
    timeout_seconds: float = _STEAM_AUTH_POLL_TIMEOUT_SECONDS,
) -> str:
    """Poll Steam until the auth session actually contains a refresh token.

    steampy 1.2.0 polls ``PollAuthSessionStatus`` only once and indexes
    ``response["refresh_token"]`` directly.  Steam is allowed to return an
    otherwise successful, still-pending response first, which used to leak a
    bare ``KeyError('refresh_token')`` to the account verification endpoint.
    """
    if sleeper is None:
        sleeper = time.sleep
    if clock is None:
        clock = time.monotonic
    deadline = clock() + max(0.0, float(timeout_seconds))
    attempts = max(1, int(max_attempts))
    delay = max(0.0, float(interval_seconds))
    current_client_id = client_id
    last_status = None

    for attempt in range(attempts):
        if attempt and clock() >= deadline:
            break
        response = executor._api_call(
            "POST",
            "IAuthenticationService",
            "PollAuthSessionStatus",
            params={
                "client_id": current_client_id,
                "request_id": request_id,
            },
        )
        last_status = getattr(response, "status_code", None)
        if last_status not in (None, 200):
            raise RuntimeError(f"Steam 登录状态查询失败（HTTP {last_status}），请稍后重试")
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("Steam 登录状态响应无法解析，请稍后重试") from None
        response_data = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(response_data, dict):
            raise RuntimeError("Steam 登录状态响应格式异常，请稍后重试")

        refresh_token = response_data.get("refresh_token")
        if refresh_token:
            executor.refresh_token = refresh_token
            return refresh_token

        # Steam may rotate the client id while the auth session is pending.
        new_client_id = response_data.get("new_client_id")
        if new_client_id:
            current_client_id = new_client_id

        if attempt + 1 < attempts:
            remaining = deadline - clock()
            if remaining <= 0:
                break
            sleeper(min(delay, remaining))

    raise SteamAuthTokenPending(
        "等待 Steam 登录凭证超时；本次已停止等待，请稍后重试。"
        "这不代表账号密码或令牌一定有误"
    )


def _steampy_pool_sessions_with_retry(self, client_id: str, request_id: str) -> None:
    _poll_steam_refresh_token(
        self,
        client_id,
        request_id,
        max_attempts=_STEAM_AUTH_POLL_MAX_ATTEMPTS,
        interval_seconds=_STEAM_AUTH_POLL_INTERVAL_SECONDS,
    )


def _do_steampy_login_once(
    username: str,
    password: str,
    steam_guard_dict: Optional[dict],
    request_proxies: Optional[dict] = None,
) -> Tuple[bool, str, dict]:
    """Core Steam login using steampy's SteamClient with JWT/Protobuf protocol.
    Applies SSL, timeout, and proxy settings only to this SteamClient session,
    including the LoginExecutor requests that reuse it.
    Returns (ok, error_code, cookie_dict).
    """
    import json
    import requests as _req
    import requests.utils as rutils
    import urllib3
    urllib3.disable_warnings()
    _steampy_login_patch_lock.acquire()
    _old_pool_sessions = None
    client = None
    old_client_request = None
    active_proxies = dict(request_proxies or {})

    def _steam_request(method, url, **kwargs):
        kwargs['verify'] = False
        kwargs['proxies'] = dict(active_proxies)
        kwargs.setdefault('timeout', _STEAM_LOGIN_REQUEST_TIMEOUT)
        return old_client_request(method, url, **kwargs)

    try:
        from steampy.client import SteamClient
        from steampy.login import LoginExecutor
        _old_pool_sessions = getattr(LoginExecutor, "_pool_sessions_steam", None)
        if _old_pool_sessions is not None:
            LoginExecutor._pool_sessions_steam = _steampy_pool_sessions_with_retry
        sg_str = json.dumps(steam_guard_dict) if steam_guard_dict else None
        client = SteamClient(api_key="", username=username, password=password,
                             steam_guard=sg_str)
        old_client_request = client._session.request
        client._session.request = _steam_request
        client._session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Origin': 'https://steamcommunity.com',
            'Referer': 'https://steamcommunity.com/',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })
        client.login()
        if not client.is_session_alive():
            return False, 'session_dead', {}
        comm_cookies = client._session.cookies.get_dict(domain='steamcommunity.com')
        store_cookies = client._session.cookies.get_dict(domain='store.steampowered.com')
        merged = {**store_cookies, **comm_cookies}
        if not merged.get('steamLoginSecure'):
            merged = rutils.dict_from_cookiejar(client._session.cookies)
        return True, '', merged
    except Exception as e:
        err = str(e).lower()
        if isinstance(e, SteamAuthTokenPending) or (
            isinstance(e, KeyError) and e.args == ("refresh_token",)
        ):
            detail = str(e).strip("'\" ")
            if not detail or detail == "refresh_token":
                detail = (
                    "Steam 登录确认尚未返回凭证；本次已停止等待，请稍后重试"
                )
            return False, f"auth_pending: {detail}", {}
        network_error = _classify_steam_login_exception(e)
        if network_error.startswith("network_error:"):
            return False, network_error, {}
        if 'invalid' in err or 'incorrect' in err or 'wrong' in err or 'bad credentials' in err or 'client_id' in err or 'client id' in err:
            return False, 'wrong_creds', {}
        if 'two-factor' in err or 'twofactor' in err or '2fa' in err or 'guard' in err:
            return False, 'need_2fa', {}
        if 'captcha' in err:
            return False, 'captcha', {}
        if 'expecting value' in err or 'no response' in err:
            return False, 'ip_blocked: Steam API无响应，请尝试重启加速器或更换IP', {}
        return False, network_error, {}
    finally:
        if _old_pool_sessions is not None:
            LoginExecutor._pool_sessions_steam = _old_pool_sessions
        if client is not None and old_client_request is not None:
            client._session.request = old_client_request
        _steampy_login_patch_lock.release()


def _get_steam_login_proxy_manager():
    from utils.proxy_manager import get_proxy_manager

    return get_proxy_manager()


def _do_steampy_login(
    username: str,
    password: str,
    steam_guard_dict: Optional[dict],
) -> Tuple[bool, str, dict]:
    """Log in once or retry through the configured proxy policy.

    Strategy 1 retries through one proxy only after a network failure.
    Strategy 2 uses a proxy immediately and rotates once on a network failure.
    Disabled/strategy 3 keeps the original direct path. Authentication errors
    are never retried, which avoids unnecessary Steam login attempts.
    """
    try:
        proxy_cfg = (load_app_config_validated().get("proxy_pool") or {})
        strategy = int(proxy_cfg.get("strategy", 3))
        proxy_enabled = bool(proxy_cfg.get("enabled")) and strategy in (1, 2)
    except Exception as exc:
        log(
            f"Steam 登录读取代理配置失败，将使用本机网络: {_short_error_detail(exc)}",
            "warn",
            category="steam",
        )
        proxy_enabled = False
        strategy = 3

    if not proxy_enabled:
        return _do_steampy_login_once(username, password, steam_guard_dict)

    try:
        proxy_manager = _get_steam_login_proxy_manager()
        first_proxy = proxy_manager.get_proxies_for_request(failed=False)
    except Exception as exc:
        detail = _short_error_detail(exc)
        if strategy == 2:
            return (
                False,
                "network_error: Steam 登录已配置为完全走代理，但代理池初始化失败。"
                f" 原始错误: {detail}",
                {},
            )
        log(
            f"Steam 登录代理池初始化失败，将仅尝试本机网络: {detail}",
            "warn",
            category="steam",
        )
        return _do_steampy_login_once(username, password, steam_guard_dict)

    if strategy == 2 and not first_proxy:
        return (
            False,
            "network_error: Steam 登录已配置为完全走代理，但代理池当前没有可用节点；"
            "请检查代理配置和节点检测结果。",
            {},
        )

    first_result = _do_steampy_login_once(
        username,
        password,
        steam_guard_dict,
        first_proxy,
    )
    if first_result[0] or not first_result[1].startswith("network_error:"):
        return first_result

    try:
        retry_proxy = proxy_manager.get_proxies_for_request(failed=True)
    except Exception as exc:
        log(
            f"Steam 登录切换代理失败: {_short_error_detail(exc)}",
            "warn",
            category="steam",
        )
        return first_result
    if not retry_proxy:
        return first_result

    if strategy == 1:
        log(
            "Steam 登录本机连接失败，按代理策略切换节点重试一次",
            "info",
            category="steam",
        )
    else:
        log(
            "Steam 登录代理连接失败，轮换节点重试一次",
            "info",
            category="steam",
        )
    return _do_steampy_login_once(
        username,
        password,
        steam_guard_dict,
        retry_proxy,
    )


def steam_id_from_cookie_str(cookie_str: str) -> str:
    slc = ""
    for part in (cookie_str or "").split(";"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key.strip().lower() == "steamloginsecure":
            slc = value.strip()
            break
    subject = unquote(slc).split("||", 1)[0].strip()
    return subject if subject.isdigit() else ""
def _extract_creds_from_cookie_dict(cookie_dict: dict) -> Tuple[str, str, str]:
    """From a cookie dict return (cookie_str, session_id, steam_id)."""
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
    session_id = cookie_dict.get("sessionid", "")
    steam_id = steam_id_from_cookie_str(cookie_str)
    return cookie_str, session_id, steam_id
_steam_auth_lock = threading.Lock()
_steam_auth_inflight = None


def _coordinate_steam_login(account: dict) -> tuple:
    """Manual verification and automatic relogin share the same in-flight result."""
    global _steam_auth_inflight
    if not account:
        return False, "no_account", "未设置当前 Steam 账号，无法自动登录"
    account = dict(account)
    account_id = account["id"]
    with _steam_auth_lock:
        if _steam_auth_inflight is not None:
            active_id, future = _steam_auth_inflight
            if active_id != account_id:
                return False, "busy", "另一个账号正在登录，请等待其完成后重试"
            owner = False
        else:
            future = Future()
            _steam_auth_inflight = (account_id, future)
            owner = True
    if not owner:
        try:
            return future.result(timeout=_STEAM_AUTH_JOIN_TIMEOUT_SECONDS)
        except FutureTimeoutError:
            return False, "busy", "Steam 登录仍在进行，请稍后重试"
    result = (False, "error", "Steam 登录流程异常，请稍后重试")
    try:
        result = _try_steam_auto_relogin_impl(account)
        return result
    except Exception as exc:
        log(f"steam_auth: 登录流程异常 ({type(exc).__name__})", "warn", category="steam")
        return result
    finally:
        with _steam_auth_lock:
            future.set_result(result)
            _steam_auth_inflight = None
def try_steam_auto_relogin() -> tuple:
    return _coordinate_steam_login(get_current_account())
def _try_steam_auto_relogin_impl(cur: Optional[dict] = None) -> tuple:
    cur = dict(cur or get_current_account() or {})
    if not cur:
        log("auto_relogin: 未设置当前账号", "warn", category="steam")
        return False, "no_account", "未设置当前 Steam 账号，无法自动登录"
    account_id = cur.get("id")
    username = (cur.get("username") or "").strip()
    password = (cur.get("password") or "").strip()
    set_current(account_id)
    existing = get_steam_credentials()
    existing_cookies = existing.get("cookies") or existing.get("cookie") or ""
    if existing_cookies and "steamLoginSecure" in existing_cookies:
        expected_steam_id = str(cur.get("steam_id") or "").strip()
        existing_steam_id = steam_id_from_cookie_str(existing_cookies)
        can_reuse_existing = bool(expected_steam_id and existing_steam_id)
        if expected_steam_id and existing_steam_id and expected_steam_id != existing_steam_id:
            can_reuse_existing = False
            log(
                "auto_relogin: 现有 Steam Cookie 属于其他账号，跳过复用并重新登录当前账号",
                "info",
                category="steam",
            )
        elif existing_steam_id and not expected_steam_id:
            can_reuse_existing = False
            log(
                "auto_relogin: 当前账号尚未绑定 steam_id，跳过复用旧 Cookie 并重新登录以绑定账号",
                "info",
                category="steam",
            )
        if can_reuse_existing:
            log("auto_relogin: 检测到现有 steamLoginSecure cookie，用 HTTP API 验证是否仍有效…", "info", category="steam")
            valid = _verify_steam_cookies_valid(existing_cookies, expected_steam_id)
            if (get_current_account() or {}).get("id") != account_id:
                return False, "account_changed", "当前账号已切换，本次验证结果不再应用"
            if valid is True:
                log("auto_relogin: HTTP 验证通过，Cookie 仍有效，无需重新登录", "info", category="steam")
                return True, "session_valid", "当前 Steam 会话有效，无需重新登录；本次未校验已保存的密码和令牌"
            if valid is None:
                return False, "network_error", "暂时无法确认 Steam 会话状态，已保留现有凭证；请稍后重试"
            log("auto_relogin: HTTP 验证显示现有 cookie 已过期，继续密码登录", "info", category="steam")
    if not username or not password:
        log("auto_relogin: 无账号或密码", "warn", category="steam")
        return False, "no_creds", "未保存账号或密码，无法自动登录"
    log("auto_relogin: 开始自动登录…", "info", category="steam")
    cfg = load_app_config_validated()
    steam_guard_dict = _build_steam_guard_dict(cur, cfg)
    if steam_guard_dict:
        log("auto_relogin: 已检测到 shared_secret，将自动处理 2FA", "info", category="steam")
    else:
        log("auto_relogin: 未配置 shared_secret，以无 2FA 方式尝试登录", "info", category="steam")
    ok, err_code, cookie_dict = _do_steampy_login(username, password, steam_guard_dict)
    if ok and cookie_dict.get("steamLoginSecure"):
        cookie_str, session_id, steam_id = _extract_creds_from_cookie_dict(cookie_dict)
        if (get_current_account() or {}).get("id") != account_id:
            return False, "account_changed", "当前账号已切换，本次登录凭证未保存"
        if not steam_id or (cur.get("steam_id") and steam_id != str(cur["steam_id"])):
            return False, "account_mismatch", "Steam 返回的登录账号不匹配，凭证未保存"
        update_steam_creds(cookie_str, session_id or "", steam_id=steam_id)
        try:
            dn, av = fetch_steam_profile_via_api(steam_id or cur.get("steam_id", ""), cookie_str)
            update_account(account_id,
                           steam_id=steam_id or cur.get("steam_id", ""),
                           display_name=dn or cur.get("display_name", ""),
                           avatar_url=av or cur.get("avatar_url", ""))
        except Exception:
            pass
        log("auto_relogin: 登录成功", "info", category="steam")
        return True, "auto_ok", "已自动登录并更新凭证"
    if err_code == "wrong_creds":
        log("auto_relogin: 账号或密码错误", "warn", category="steam")
        try:
            from app.notify import notify_manual_intervention_required
            notify_manual_intervention_required("Steam", "系统保存的账号或密码不正确，登录被拒绝，请立刻前往修改密码并手动干预登录")
        except Exception:
            pass
        return False, "wrong_creds", "账号或密码错误"
    if err_code == "need_2fa":
        log("auto_relogin: 需要 2FA 但无 shared_secret 或令牌有误", "warn", category="steam")
        try:
            from app.notify import notify_manual_intervention_required
            notify_manual_intervention_required("Steam", "账号需要 2FA 验证，但 shared_secret 未配置或格式有误，请前往设置页补充 Steam Guard 密钥")
        except Exception:
            pass
        return False, "need_2fa", "需要二次验证且未配置 shared_secret，请配置后重试"
    if err_code == "captcha":
        log("auto_relogin: Steam 要求人机验证（Captcha），自动登录暂时失败", "warn", category="steam")
        return False, "captcha", "Steam 触发了人机验证，请稍后重试或手动登录"
    if err_code.startswith("auth_pending:"):
        msg = err_code.split(": ", 1)[1] if ": " in err_code else err_code
        log(f"auto_relogin: {msg}", "warn", category="steam")
        return False, "auth_pending", msg
    if err_code.startswith("network_error:"):
        msg = err_code.split(": ", 1)[1] if ": " in err_code else err_code
        log(f"auto_relogin: {msg}", "warn", category="steam")
        return False, "network_error", msg
    log(f"auto_relogin: 登录失败 – {err_code}", "warn", category="steam")
    return False, "error", (err_code or "自动登录失败，请检查网络或手动重登")
def verify_steam_auto_login(account_id: str) -> dict:
    acc = get_account(account_id)
    if not acc:
        return {"ok": False, "status": "no_account", "message": "账号不存在"}
    ok, status, message = _coordinate_steam_login(acc)
    return {"ok": ok, "status": status, "message": message}
