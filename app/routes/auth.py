"""Auth routes – Steam & Buff relogin, Steam Guard."""
import base64
import hashlib
import hmac
import re
import shutil
import struct
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel
from app.state import log, set_buff_auth_expired, set_buff_verification_required
from app.config_loader import (
    get_buff_credentials,
    get_steam_credentials,
    load_app_config_validated,
    update_buff_creds,
    update_steam_creds,
)
from app.accounts import get_current_account, get_profile_dir, set_current, update_account
from app.services.steam_auth import (
    fetch_steam_profile_via_api,
    try_steam_auto_relogin,
)
from app.services.buff_auth import (
    BUFF_BROWSER_LAUNCH_ARGS,
    BUFF_ACCOUNT_ID,
    BUFF_ORIGIN,
    browser_buff_verification_allowed,
    buff_credential_replacement_block_reason,
    clear_buff_request_policy_after_verification,
    get_buff_auth_lock,
    manual_buff_probe_allowed,
    read_buff_browser_user_agent,
    verify_buff_browser_session,
    verify_manual_buff_credentials,
)
from app.runtime_env import get_runtime_profile
router = APIRouter()
_relogin_lock = threading.Lock()
_relogin_type = None
_relogin_playwright = None
_relogin_browser = None
_relogin_context = None
_relogin_ready = threading.Event()
_relogin_wake = threading.Event()
_relogin_done = threading.Event()
_relogin_success = False
_relogin_error = None
_relogin_thread = None
_BROWSER_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
_RELOGIN_TOTAL_TIMEOUT_SECONDS = 10 * 60
_RELOGIN_FINISH_WAIT_SECONDS = 30
def _manual_cookie_required_response(relogin_type: str, reason: str):
    label = "Steam" if relogin_type == "steam" else "Buff"
    return {
        "ok": False,
        "code": "manual_cookie_required",
        "manual_cookie_required": True,
        "error": f"{label} 登录需要图形浏览器，但当前运行环境不支持。请使用手动 Cookie 登录。",
        "reason": reason,
        "runtime": get_runtime_profile().as_dict(),
    }
class ReloginFinishBody(BaseModel):
    success: bool
class ManualCookieBody(BaseModel):
    cookies: str
    session_id: str = ""
    steam_id: str = ""
    user_agent: str = ""
def _normalize_cookie_input(raw: str) -> str:
    text = (raw or "").strip()
    text = re.sub(r"^\s*cookie\s*:\s*", "", text, flags=re.I)
    pieces = []
    for part in re.split(r";|\r?\n", text):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if name:
            pieces.append(f"{name}={value}")
    return "; ".join(pieces)
def _normalize_user_agent(raw: str) -> str:
    # A header value must be one line.  Keep a generous upper bound while
    # preventing accidental pasted request dumps from entering credentials.
    return " ".join((raw or "").split())[:1024]
def _cookie_value(cookie_str: str, name: str) -> str:
    wanted = name.lower()
    for part in (cookie_str or "").split(";"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key.strip().lower() == wanted:
            return value.strip()
    return ""
def _cookie_header_from_browser(cookies: list) -> str:
    pieces = []
    for cookie in cookies or []:
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "").strip()
        if name:
            pieces.append(f"{name}={value}")
    return "; ".join(pieces)
def _has_browser_cookie(cookies: list, name: str) -> bool:
    wanted = name.lower()
    return any(
        str(cookie.get("name") or "").strip().lower() == wanted
        and str(cookie.get("value") or "").strip()
        for cookie in (cookies or [])
    )
def _steam_id_from_cookie_str(cookie_str: str) -> str:
    value = _cookie_value(cookie_str, "steamLoginSecure")
    if "%7C%7C" in value:
        return value.split("%7C%7C", 1)[0].strip()
    if "||" in value:
        return value.split("||", 1)[0].strip()
    return value.strip() if value.strip().isdigit() else ""
def _looks_like_browser_launch_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "browser",
            "chromium",
            "launch_persistent_context",
            "target page, context or browser has been closed",
            "process did exit",
            "executable doesn't exist",
            "processsingleton",
            "user data dir",
        )
    )
def _should_retry_browser_launch(exc: Exception) -> bool:
    text = str(exc).lower()
    if "executable doesn't exist" in text or "playwright install" in text:
        return False
    return any(
        marker in text
        for marker in (
            "target page, context or browser has been closed",
            "process did exit",
            "processsingleton",
            "user data dir",
            "profile",
        )
    )
def _friendly_browser_launch_error(exc: Exception, relogin_type: str, retried: bool = False) -> str:
    label = "Steam" if relogin_type == "steam" else "Buff"
    text = str(exc)
    lower = text.lower()
    if "executable doesn't exist" in lower or "playwright install" in lower:
        return f"{label} 登录浏览器未安装，请在项目环境执行：python -m playwright install chromium"
    if "processsingleton" in lower or "user data dir" in lower:
        return f"{label} 登录浏览器配置目录正在被占用。请关闭残留的 Chromium/Chrome 窗口后重试，或先使用手动 Cookie 登录。"
    if "target page, context or browser has been closed" in lower or "process did exit" in lower:
        prefix = "临时登录目录重试后仍失败" if retried else "浏览器启动后立即关闭"
        return (
            f"{label} {prefix}。常见原因是 Playwright 浏览器目录被占用/损坏、"
            "系统安全软件拦截或浏览器组件异常；请先使用手动 Cookie 登录，完整错误见调试日志。"
        )
    short = " ".join(text.split())
    if len(short) > 180:
        short = short[:180] + "..."
    return f"{label} 登录浏览器打开失败：{short or type(exc).__name__}"
def _launch_relogin_context(playwright, profile_dir: Path, relogin_type: str):
    label = "Steam" if relogin_type == "steam" else "Buff"
    launch_args = (
        BUFF_BROWSER_LAUNCH_ARGS
        if relogin_type == "buff"
        else _BROWSER_LAUNCH_ARGS
    )
    try:
        return playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            args=launch_args,
        ), None
    except Exception as first_exc:
        log(
            f"{label} 登录浏览器使用固定目录启动失败: {str(first_exc)[:2000]}",
            "warn",
            category="auth",
        )
        if not _should_retry_browser_launch(first_exc):
            raise RuntimeError(_friendly_browser_launch_error(first_exc, relogin_type)) from first_exc
        tmp_parent = profile_dir.parent / "playwright_tmp"
        tmp_parent.mkdir(parents=True, exist_ok=True)
        temp_profile = Path(tempfile.mkdtemp(prefix=f"{relogin_type}_", dir=str(tmp_parent)))
        try:
            context = playwright.chromium.launch_persistent_context(
                str(temp_profile),
                headless=False,
                args=launch_args,
            )
            log(
                f"{label} 登录浏览器已使用临时目录启动，原目录可能被占用或损坏: {profile_dir}",
                "info",
                category="auth",
            )
            return context, temp_profile
        except Exception as second_exc:
            log(
                f"{label} 登录浏览器临时目录重试失败: {str(second_exc)[:2000]}",
                "error",
                category="auth",
            )
            shutil.rmtree(temp_profile, ignore_errors=True)
            raise RuntimeError(_friendly_browser_launch_error(second_exc, relogin_type, retried=True)) from second_exc
def _maybe_resume_after_buff_cookie_update() -> None:
    set_buff_auth_expired(False)
    set_buff_verification_required(False)
    try:
        from app.state import get_status
        from app.pipeline import start_pipeline
        st = get_status()
        err_msg = str(st.get("step") or "")
        if st.get("status") == "error" and err_msg in ("BUFF_AUTH_EXPIRED", "BUFF_VERIFICATION_REQUIRED"):
            log("检测到 Buff Cookie 已手动更新，尝试自动恢复流水线...", "info", category="system")
            try:
                if not start_pipeline(load_app_config_validated()):
                    log(
                        "Buff 验证已完成，但流水线当前不可自动恢复；请确认状态后手动启动。",
                        "warn",
                        category="system",
                    )
            except Exception as resume_err:
                log(f"自动恢复流水线失败: {resume_err}", "warn", category="system")
    except Exception as resume_err:
        log(f"Buff Cookie 更新后的恢复检查失败: {resume_err}", "warn", category="system")
def _relogin_worker(relogin_type: str) -> None:
    global _relogin_playwright, _relogin_browser, _relogin_context, _relogin_error, _relogin_success, _relogin_thread
    p = None
    context = None
    temp_profile_dir = None
    buff_auth_lock_acquired = False
    deadline = time.monotonic() + _RELOGIN_TOTAL_TIMEOUT_SECONDS
    try:
        if relogin_type == "buff":
            buff_auth_lock_acquired = get_buff_auth_lock().acquire(blocking=False)
            if not buff_auth_lock_acquired:
                _relogin_error = "另一项 Buff 登录或凭证更新正在进行，请稍后重试。"
                _relogin_ready.set()
                return
            checkout_block = buff_credential_replacement_block_reason()
            if checkout_block:
                _relogin_error = checkout_block
                _relogin_ready.set()
                return
            browser_allowed, _browser_status, browser_message = (
                browser_buff_verification_allowed()
            )
            if not browser_allowed:
                _relogin_error = browser_message
                _relogin_ready.set()
                return
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()
        if relogin_type == "steam":
            cur = get_current_account()
            profile_dir = get_profile_dir(cur.get("id") if cur else None)
        else:
            profile_dir = Path(__file__).resolve().parent.parent.parent / "config" / "playwright_buff"
        profile_dir.mkdir(parents=True, exist_ok=True)
        context, temp_profile_dir = _launch_relogin_context(p, profile_dir, relogin_type)
        page = context.pages[0] if context.pages else context.new_page()
        url = "https://store.steampowered.com/login/" if relogin_type == "steam" else "https://buff.163.com/"
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if relogin_type == "steam":
            pass
        with _relogin_lock:
            _relogin_playwright, _relogin_browser, _relogin_context = p, None, context
        _relogin_ready.set()
        remaining = max(0.0, deadline - time.monotonic())
        if not _relogin_wake.wait(timeout=remaining):
            _relogin_error = "登录操作已超时，浏览器会话已关闭，请重新发起登录。"
            return
        if _relogin_success:
            if relogin_type == "steam":
                try:
                    page.goto("https://steamcommunity.com/market/", wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    pass
            cookies = (
                context.cookies([BUFF_ORIGIN])
                if relogin_type == "buff"
                else context.cookies()
            )
            if relogin_type == "steam":
                steam_cookies = [c for c in cookies if "steamcommunity" in (c.get("domain") or "") or "steampowered" in (c.get("domain") or "")]
                selected = steam_cookies if steam_cookies else cookies
                has_secure = _has_browser_cookie(selected, "steamLoginSecure")
                cookie_str = _cookie_header_from_browser(selected)
                session_id = next((c["value"] for c in selected if c.get("name") == "sessionid"), None) or next((c["value"] for c in cookies if c.get("name") == "sessionid"), None)
                if not session_id or not has_secure:
                    _relogin_error = "未检测到 Steam 登录 Cookie，请确认弹出的浏览器已经登录完成后再点击完成。"
                else:
                    if not _cookie_value(cookie_str, "sessionid"):
                        cookie_str = f"{cookie_str}; sessionid={session_id}"
                    update_steam_creds(cookie_str, session_id)
                    cur = get_current_account()
                    if cur:
                        steam_id = None
                        for c in cookies:
                            if c.get("name") == "steamLoginSecure":
                                v = c.get("value", "")
                                if "%7C%7C" in v:
                                    steam_id = v.split("%7C%7C")[0].strip()
                                elif "||" in v:
                                    steam_id = v.split("||")[0].strip()
                                break
                        display_name, avatar_url = fetch_steam_profile_via_api(steam_id or "", cookie_str)
                        update_account(cur["id"], steam_id=steam_id or "", display_name=display_name, avatar_url=avatar_url)
            else:
                verified, verify_status, verify_message = verify_buff_browser_session(page)
                if not verified:
                    if verify_status == "verification":
                        set_buff_verification_required(True, verify_message)
                    elif verify_status == "expired":
                        set_buff_auth_expired(True)
                    _relogin_error = f"Buff 登录在线验证未通过：{verify_message}"
                else:
                    refreshed_cookies = context.cookies([BUFF_ORIGIN])
                    if not _has_browser_cookie(refreshed_cookies, "session"):
                        _relogin_error = "未检测到 Buff 登录 session，请确认弹出的浏览器已经完成登录或验证后再点击完成。"
                    else:
                        checkout_block = buff_credential_replacement_block_reason()
                        if checkout_block:
                            _relogin_error = checkout_block
                        else:
                            user_agent = read_buff_browser_user_agent(page)
                            cookie_str = _cookie_header_from_browser(refreshed_cookies)
                            update_buff_creds(
                                cookie_str,
                                user_agent=user_agent or None,
                                source="playwright" if temp_profile_dir is None else "playwright_ephemeral",
                            )
                            if not clear_buff_request_policy_after_verification(BUFF_ACCOUNT_ID):
                                _relogin_error = "Buff 登录已验证，但请求熔断重置失败，请稍后重试。"
                            else:
                                set_buff_auth_expired(False)
                                set_buff_verification_required(False)
                                _maybe_resume_after_buff_cookie_update()
    except Exception as e:
        if _looks_like_browser_launch_error(e):
            _relogin_error = _friendly_browser_launch_error(e, relogin_type)
        else:
            _relogin_error = str(e)
        _relogin_ready.set()
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if p is not None:
            try:
                p.stop()
            except Exception:
                pass
        if temp_profile_dir is not None:
            shutil.rmtree(temp_profile_dir, ignore_errors=True)
        with _relogin_lock:
            _relogin_playwright = None
            _relogin_browser = None
            _relogin_context = None
            if _relogin_thread is threading.current_thread():
                _relogin_thread = None
        if buff_auth_lock_acquired:
            get_buff_auth_lock().release()
        _relogin_done.set()
def _relogin_start(relogin_type: str):
    global _relogin_type, _relogin_error, _relogin_success, _relogin_playwright, _relogin_browser, _relogin_context, _relogin_thread
    profile = get_runtime_profile()
    if not profile.can_launch_headful_browser:
        return _manual_cookie_required_response(relogin_type, profile.reason)
    with _relogin_lock:
        if (_relogin_thread is not None and _relogin_thread.is_alive()) or _relogin_context or _relogin_browser:
            return {
                "ok": False,
                "code": "relogin_busy",
                "error": "已有登录窗口正在等待处理，请先完成或取消当前登录。",
            }
        if relogin_type == "buff":
            checkout_block = buff_credential_replacement_block_reason()
            if checkout_block:
                return {
                    "ok": False,
                    "code": "buff_checkout_pending",
                    "error": checkout_block,
                }
            browser_allowed, browser_status, browser_message = (
                browser_buff_verification_allowed()
            )
            if not browser_allowed:
                return {
                    "ok": False,
                    "code": f"buff_{browser_status}",
                    "error": browser_message,
                }
        _relogin_type = relogin_type
        _relogin_error = None
        _relogin_success = False
        _relogin_ready.clear()
        _relogin_done.clear()
        _relogin_wake.clear()
        t = threading.Thread(target=_relogin_worker, args=(relogin_type,), daemon=True)
        _relogin_thread = t
        t.start()
    if not _relogin_ready.wait(timeout=60):
        _relogin_wake.set()
        return {"ok": False, "error": "打开浏览器超时"}
    if _relogin_error:
        return {"ok": False, "error": _relogin_error}
    msg = "请在弹出的浏览器中完成 Steam 登录" if relogin_type == "steam" else "请在弹出的浏览器中完成 Buff 登录/验证"
    return {"ok": True, "message": msg}
def _relogin_finish(success: bool):
    global _relogin_success, _relogin_context, _relogin_error
    with _relogin_lock:
        if not _relogin_context:
            return {"ok": False, "error": "未在重新登录流程中"}
        _relogin_success = success
    _relogin_wake.set()
    if not _relogin_done.wait(timeout=_RELOGIN_FINISH_WAIT_SECONDS):
        return {"ok": False, "error": "更新登录信息超时，请稍后重试"}
    if _relogin_error:
        return {"ok": False, "error": _relogin_error}
    return {"ok": True}
def _normalize_secret(raw: str) -> str:
    return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), raw)
def _generate_steam_guard_code(shared_secret: str) -> Optional[str]:
    if not shared_secret:
        return None
    secret_str = _normalize_secret(shared_secret.strip())
    try:
        secret_bytes = base64.b64decode(secret_str)
    except Exception:
        return None
    ts = int(time.time())
    time_buffer = struct.pack(">Q", ts // 30)
    hmac_hash = hmac.new(secret_bytes, time_buffer, hashlib.sha1).digest()
    offset = hmac_hash[19] & 0xF
    code_int = struct.unpack(">I", hmac_hash[offset : offset + 4])[0] & 0x7FFFFFFF
    chars = "23456789BCDFGHJKMNPQRTVWXY"
    out = []
    for _ in range(5):
        out.append(chars[code_int % 26])
        code_int //= 26
    return "".join(out)
@router.post("/api/auth/steam/relogin_start")
def api_auth_steam_relogin_start():
    return _relogin_start("steam")
@router.post("/api/auth/steam/relogin_finish")
def api_auth_steam_relogin_finish(body: ReloginFinishBody):
    return _relogin_finish(body.success)
@router.post("/api/auth/buff/relogin_start")
def api_auth_buff_relogin_start():
    return _relogin_start("buff")
@router.post("/api/auth/buff/relogin_finish")
def api_auth_buff_relogin_finish(body: ReloginFinishBody):
    return _relogin_finish(body.success)
@router.post("/api/auth/{relogin_type}/manual_cookie")
def api_auth_manual_cookie(relogin_type: str, body: ManualCookieBody):
    cookie_str = _normalize_cookie_input(body.cookies)
    if not cookie_str:
        return {"ok": False, "error": "Cookie 不能为空"}
    if relogin_type == "steam":
        session_id = (body.session_id or "").strip() or _cookie_value(cookie_str, "sessionid")
        if not _cookie_value(cookie_str, "steamLoginSecure"):
            return {"ok": False, "error": "Steam Cookie 缺少 steamLoginSecure"}
        if not session_id:
            return {"ok": False, "error": "Steam Cookie 缺少 sessionid"}
        if not _cookie_value(cookie_str, "sessionid"):
            cookie_str = f"{cookie_str}; sessionid={session_id}"
        steam_id = (body.steam_id or "").strip() or _steam_id_from_cookie_str(cookie_str)
        update_steam_creds(cookie_str, session_id, steam_id or None)
        cur = get_current_account()
        if cur:
            display_name, avatar_url = fetch_steam_profile_via_api(steam_id or cur.get("steam_id", ""), cookie_str)
            update_account(cur["id"], steam_id=steam_id or cur.get("steam_id", ""), display_name=display_name, avatar_url=avatar_url)
        return {"ok": True, "message": "Steam Cookie 已保存", "steam_id": steam_id}
    if relogin_type == "buff":
        if not _cookie_value(cookie_str, "session"):
            return {"ok": False, "error": "Buff Cookie 缺少 session"}
        if not _cookie_value(cookie_str, "csrf_token"):
            return {"ok": False, "error": "Buff Cookie 缺少 csrf_token"}
        checkout_block = buff_credential_replacement_block_reason()
        if checkout_block:
            return {
                "ok": False,
                "code": "buff_checkout_pending",
                "error": checkout_block,
            }
        lock = get_buff_auth_lock()
        if not lock.acquire(blocking=False):
            return {"ok": False, "error": "另一项 Buff 登录或保活任务正在进行，请稍后重试"}
        try:
            checkout_block = buff_credential_replacement_block_reason()
            if checkout_block:
                return {
                    "ok": False,
                    "code": "buff_checkout_pending",
                    "error": checkout_block,
                }
            saved_credentials = get_buff_credentials() or {}
            user_agent = _normalize_user_agent(body.user_agent)
            if not user_agent:
                user_agent = _normalize_user_agent(saved_credentials.get("user_agent", ""))
            probe_allowed, probe_status, probe_message = manual_buff_probe_allowed(
                cookie_str,
                str(saved_credentials.get("cookies") or ""),
            )
            if not probe_allowed:
                return {
                    "ok": False,
                    "code": f"buff_cookie_{probe_status}",
                    "error": probe_message or "当前 BUFF 请求熔断尚未解除，请稍后重试或完成浏览器验证。",
                }
            validated_cookie = [cookie_str]

            def remember_validated_cookie(value: str) -> None:
                if value:
                    validated_cookie[0] = value

            verified, verify_status, verify_message = verify_manual_buff_credentials(
                cookie_str,
                user_agent,
                remember_validated_cookie,
            )
            if not verified:
                return {
                    "ok": False,
                    "code": f"buff_cookie_{verify_status}",
                    "error": verify_message,
                }
            checkout_block = buff_credential_replacement_block_reason()
            if checkout_block:
                return {
                    "ok": False,
                    "code": "buff_checkout_pending",
                    "error": checkout_block,
                }
            update_buff_creds(
                validated_cookie[0],
                user_agent=user_agent or None,
                source="manual",
            )
            if not clear_buff_request_policy_after_verification(BUFF_ACCOUNT_ID):
                return {
                    "ok": False,
                    "code": "buff_policy_reset_failed",
                    "credentials_saved": True,
                    "error": "Buff Cookie 已验证并保存，但请求熔断重置失败；流水线未恢复。",
                }
            _maybe_resume_after_buff_cookie_update()
        finally:
            lock.release()
        return {"ok": True, "message": "Buff Cookie 已保存"}
    return {"ok": False, "error": "未知登录类型"}
@router.get("/api/steam_guard")
def api_steam_guard():
    cfg = load_app_config_validated()
    sg = cfg.get("steam_guard") or {}
    shared_secret = (sg.get("shared_secret") or "").strip()
    if not shared_secret:
        return {"ok": False, "error": "未配置 shared_secret"}
    code = _generate_steam_guard_code(shared_secret)
    if not code:
        return {"ok": False, "error": "shared_secret 无效"}
    now_ts = int(time.time())
    return {"ok": True, "code": code, "server_time": now_ts, "period": 30}
