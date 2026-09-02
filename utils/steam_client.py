import base64
import binascii
import json
import os
import re
import threading
import time
from datetime import datetime
from ssl import SSLError as PythonSSLError
from typing import Optional, Dict, Any

import json5
import requests
from google.protobuf.message import DecodeError
from requests.exceptions import RequestException

import steampy.exceptions
from steampy.client import STEAM_USER_AGENT, SteamClient
from steampy.exceptions import ApiException, CaptchaRequired, EmptyResponse, InvalidCredentials, InvalidResponse, SteamError, SteamLoginError
from steampy.models import GameOptions
from utils import static
from utils.logger import PluginLogger, handle_caught_exception
from utils.notifier import send_notification
from utils.static import SESSION_FOLDER, STEAM_ACCOUNT_INFO_FILE_PATH, CONFIG_FILE_PATH
from utils.tools import accelerator, get_encoding, pause

logger = PluginLogger("SteamClient")

steam_client_mutex = {}  # 每个SteamClient实例对应一个互斥锁
token_refresh_thread = []  # 后台刷新线程引用

STEAM_ACCOUNT_REQUIRED_FIELDS = ("steam_username", "steam_password", "shared_secret", "identity_secret")
STEAM_ACCOUNT_SECRET_FIELDS = ("shared_secret", "identity_secret")
SENSITIVE_LOGIN_RESPONSE_FIELDS = {
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "nonce",
    "auth",
    "password",
    "shared_secret",
    "identity_secret",
    "steamloginsecure",
    "steamrefresh_steam",
}
SENSITIVE_LOGIN_RESPONSE_FIELD_KEYS = {re.sub(r"[^a-z0-9]", "", field.lower()) for field in SENSITIVE_LOGIN_RESPONSE_FIELDS}

LOGIN_ERESULT_ADVICE = {
    5: "请检查 steam_username 和 steam_password 是否正确",
    18: "请检查 steam_username 是否为 Steam 登录名，而不是昵称或邮箱",
    15: "请检查当前 IP、代理设置和 Steam 账户状态后重试",
    17: "该 Steam 账户可能已被封禁，请先在 Steam 客户端或网页确认账户状态",
    43: "该 Steam 账户已被禁用，请先处理账户状态",
    63: "Steam 拒绝了本次登录，请在 Steam 客户端或邮箱中完成身份验证后重试",
    65: "请检查邮箱验证码是否正确，然后重新发起登录",
    66: "Steam 未发送验证邮件，请检查账户邮箱和 Steam Guard 设置",
    71: "邮箱验证码已过期，请获取新验证码后重试",
    72: "当前 IP 受到登录限制，请检查代理或更换网络后重试",
    73: "该 Steam 账户已被锁定，请先通过 Steam 官方渠道解锁",
    74: "请先完成 Steam 账户邮箱验证",
    84: "登录请求过于频繁，请等待一段时间后再试，不要连续重启程序",
    85: "该账户需要 Steam Guard 双因素验证，请检查令牌配置",
    87: "Steam 正在限制登录尝试，请等待一段时间后再试",
    88: "请检查 shared_secret 是否属于当前账号，并确认系统时间准确",
    93: "请同步系统时间和时区后重新登录",
    101: "请先通过 Steam 网页或客户端完成验证码验证，再重新登录",
    105: "当前 IP 被 Steam 封禁，请停止频繁尝试并更换网络或联系 Steam 支持",
}

try:
    with open(CONFIG_FILE_PATH, "r", encoding=get_encoding(CONFIG_FILE_PATH)) as f:
        config = json5.loads(f.read())
except Exception:
    pass


class SteamAccountConfigError(ValueError):
    def __init__(self, reason: str, field: Optional[str] = None):
        self.reason = reason
        self.field = field
        super().__init__(reason)


def _validate_steam_account_info(steam_account_info) -> dict:
    if not isinstance(steam_account_info, dict):
        raise SteamAccountConfigError("配置文件根节点必须是 JSON 对象")

    for field in STEAM_ACCOUNT_REQUIRED_FIELDS:
        if field not in steam_account_info:
            raise SteamAccountConfigError("缺少必填字段", field)
        value = steam_account_info[field]
        if not isinstance(value, str):
            raise SteamAccountConfigError(f"字段类型必须是字符串，当前为 {type(value).__name__}", field)
        if value == "" or (field != "steam_password" and not value.strip()):
            raise SteamAccountConfigError("字段不能为空", field)

    for field in STEAM_ACCOUNT_SECRET_FIELDS:
        try:
            decoded_secret = base64.b64decode(steam_account_info[field], validate=True)
        except (binascii.Error, ValueError) as e:
            raise SteamAccountConfigError("字段不是有效的 Base64 编码", field) from e
        if not decoded_secret:
            raise SteamAccountConfigError("字段解码后为空", field)

    return steam_account_info


def _format_steam_account_config_error(error: SteamAccountConfigError) -> str:
    lines = ["Steam账号配置格式或内容错误", f"  文件：{STEAM_ACCOUNT_INFO_FILE_PATH}"]
    if error.field:
        lines.append(f"  字段：{error.field}")
    lines.append(f"  原因：{error.reason}")
    return "\n".join(lines)


def _get_eresult_advice(error_code: int) -> str:
    if error_code in LOGIN_ERESULT_ADVICE:
        return LOGIN_ERESULT_ADVICE[error_code]
    if error_code in (3, 10, 16, 20, 35, 38, 55, 76, 79):
        return "请检查网络连接并稍后重试；若持续出现，请确认 Steam 服务状态和代理设置"
    if error_code in (27, 126):
        return "登录令牌已过期或失效，请删除对应会话缓存后使用账号密码重新登录"
    return "请根据 Steam 错误码检查账户状态；若持续出现，请查看 DEBUG 日志"


def _get_detail_advice(detail: str) -> str:
    if "邮箱验证码" in detail:
        return "请使用支持邮箱验证码输入的登录方式，或先在 Steam 客户端完成验证"
    if "refresh_token" in detail:
        return "缓存的 refresh_token 可能已经失效，请使用账号密码重新登录"
    if "登录确认" in detail:
        return "请确认 Steam Guard 验证已经完成，然后重新登录"
    if "RSA" in detail:
        return "请检查网络和 Steam 服务状态后重试"
    return "请检查网络和代理设置并重试；若持续出现，可能是 Steam 接口响应异常"


def _get_http_error_details(status):
    if status in (401, 403):
        return "Steam 拒绝了当前登录阶段的请求", "请检查当前 IP、代理设置和 Steam 账户状态后重试"
    if status == 429:
        return "Steam 对登录请求进行了频率限制", "请等待一段时间后再试，不要连续重启程序或频繁切换代理"
    if isinstance(status, int) and status >= 500:
        return "Steam 登录服务暂时不可用", "请稍后重试，并确认 Steam 服务状态"
    return "Steam 登录接口返回了非成功状态", "请检查网络和代理设置；若持续出现，请查看 DEBUG 日志"


def _get_login_error_details(error: Exception, default_stage: str):
    stage = getattr(error, "steam_login_stage", default_stage)
    response = None

    if isinstance(error, SteamLoginError):
        stage = error.stage
        response = error.response
        if error.eresult is not None:
            reason = static.STEAM_ERROR_CODES.get(error.eresult, "未知 Steam 错误")
            return stage, f"EResult {error.eresult}（{reason}）", reason, _get_eresult_advice(error.eresult), response
        if error.http_status is not None:
            status = error.http_status
            reason, advice = _get_http_error_details(status)
            return stage, f"HTTP {status}", reason, advice, response

        detail = error.detail or "Steam 返回了无效的登录响应"
        return stage, "Steam响应异常", detail, _get_detail_advice(detail), response

    if isinstance(error, SteamError):
        reason = static.STEAM_ERROR_CODES.get(error.error_code, "未知 Steam 错误")
        return stage, f"EResult {error.error_code}（{reason}）", reason, _get_eresult_advice(error.error_code), response
    if isinstance(error, InvalidCredentials):
        return stage, "登录凭据无效", "Steam 未接受当前账号、密码或令牌凭据", "请检查 steam_username、steam_password 和 shared_secret 是否属于同一账号", response
    if isinstance(error, CaptchaRequired):
        return stage, "需要验证码", "Steam 要求完成验证码验证", "请先通过 Steam 网页或客户端完成验证码验证，再重新登录", response
    if isinstance(error, requests.exceptions.HTTPError):
        response = error.response
        status = response.status_code if response is not None else "未知"
        reason, advice = _get_http_error_details(status)
        return stage, f"HTTP {status}", reason, advice, response
    if isinstance(error, requests.exceptions.ProxyError):
        return stage, "代理连接失败", "无法通过配置的代理连接 Steam", "请检查 proxies 配置、代理端口和代理服务是否可用", response
    if isinstance(error, (requests.exceptions.SSLError, PythonSSLError)):
        return stage, "SSL证书验证失败", "无法验证 Steam 服务器的 SSL 证书", "请检查代理是否劫持 HTTPS；仅在确认网络安全时关闭 SSL 验证", response
    if isinstance(error, (requests.exceptions.Timeout, TimeoutError)):
        return stage, "连接超时", "Steam 登录请求未在规定时间内完成", "请检查网络和代理稳定性后重试", response
    if isinstance(error, (requests.exceptions.ConnectionError, ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError)):
        return stage, "网络连接失败", "无法建立或维持与 Steam 的连接", "请检查网络、代理以及 Steam 是否可访问", response
    if isinstance(error, EmptyResponse):
        return stage, "Steam响应为空", "Steam 未返回可处理的登录数据", "请稍后重试；若持续出现，请检查 IP 或代理是否被 Steam 限制", response
    if isinstance(error, (InvalidResponse, json.JSONDecodeError, DecodeError, KeyError)):
        return stage, "Steam响应格式异常", "Steam 返回的数据缺少必要字段或无法解析", "请检查网络和代理设置；若持续出现，可能是 Steam 接口发生变化", response
    if isinstance(error, ApiException):
        return stage, "Steam API异常", "Steam API 未能完成当前登录步骤", "请检查网络和 Steam 服务状态，并查看 DEBUG 日志中的具体异常", response
    if isinstance(error, (binascii.Error, UnicodeEncodeError)):
        return stage, "账号配置编码异常", "Steam Guard 密钥或登录信息无法正确编码", "请检查账号配置字段是否完整且使用正确编码", response
    if isinstance(error, FileNotFoundError):
        return stage, "本地文件不存在", "登录所需的本地文件未找到", "请检查错误日志中记录的文件路径", response

    return stage, f"未识别异常（{type(error).__name__}）", "登录过程中发生了未分类异常", "请查看 DEBUG 日志中的异常堆栈，不要据此修改账号配置", response


def _redact_login_response_value(value):
    if isinstance(value, dict):
        return {
            key: "***"
            if re.sub(r"[^a-z0-9]", "", str(key).lower()) in SENSITIVE_LOGIN_RESPONSE_FIELD_KEYS
            else _redact_login_response_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_login_response_value(item) for item in value]
    return value


def _sanitize_login_response(response) -> str:
    try:
        response_text = response.text
    except Exception:
        return "<无法读取响应内容>"

    try:
        response_json = json.loads(response_text)
        sanitized = json.dumps(_redact_login_response_value(response_json), ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        sensitive_names = "|".join(re.escape(field) for field in sorted(SENSITIVE_LOGIN_RESPONSE_FIELDS))
        pattern = rf"(?i)\b({sensitive_names})\b(\s*[\"']?\s*[:=]\s*[\"']?)([^\"'&,\s<>]+)"
        sanitized = re.sub(pattern, r"\1\2***", response_text)

    max_length = 2000
    if len(sanitized) > max_length:
        return sanitized[:max_length] + "...<已截断>"
    return sanitized


def _log_steam_login_issue(error: Exception, default_stage: str, level: str = "error", next_action: Optional[str] = None):
    if error.__traceback__ is not None:
        logger.debug("Steam登录异常堆栈", exc_info=(type(error), error, error.__traceback__))
    else:
        logger.debug("Steam登录异常: %s", error)

    stage, error_name, reason, advice, response = _get_login_error_details(error, default_stage)
    if response is not None:
        url = str(getattr(response, "url", "")).split("?", 1)[0]
        logger.debug(
            "Steam登录响应（已脱敏并截断）: HTTP %s, URL=%s, 内容=%r",
            getattr(response, "status_code", "未知"),
            url,
            _sanitize_login_response(response),
        )

    lines = ["Steam登录失败", f"  阶段：{stage}", f"  错误：{error_name}", f"  原因：{reason}", f"  建议：{advice}"]
    if next_action:
        lines.append(f"  后续：{next_action}")
    getattr(logger, level)("\n".join(lines))

# ================= JWT 解析与缓存辅助 ===================


def _parse_jwt_exp(jwt_token: Optional[str]) -> int:
    if not jwt_token:
        return 0
    try:
        parts = jwt_token.split(".")
        if len(parts) != 3:
            return 0
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        decoded_payload = base64.b64decode(payload)
        payload_data = json.loads(decoded_payload)
        return payload_data.get("exp", 0)
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.warning("解析JWT过期时间失败")
        return 0


def _get_token_cache_path(username: str) -> str:
    return os.path.join(SESSION_FOLDER, f"steam_account_{username.lower()}.json")


def _load_token_cache(username: str) -> dict:
    cache_path = _get_token_cache_path(username)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            handle_caught_exception(e, known=True)
            logger.warning(f"读取token缓存文件失败: {cache_path}")
    return {}


def _save_token_cache(username: str, auth_info: Dict[str, Any]):
    """
    auth_info 期望结构:
    {
        steamid: str,
        access_token: Optional[str],
        refresh_token: Optional[str]
    }
    """
    cache_path = _get_token_cache_path(username)
    steamid = auth_info.get("steamid")
    access_token = auth_info.get("access_token")
    refresh_token = auth_info.get("refresh_token")

    access_exp = _parse_jwt_exp(access_token)
    refresh_exp = _parse_jwt_exp(refresh_token)

    cache_data = {
        "steamid": steamid,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_token_exp_timestamp": access_exp,
        "refresh_token_exp_timestamp": refresh_exp,
    }

    # 友好可读时间
    try:
        if access_exp:
            cache_data["access_token_exp_readable"] = datetime.fromtimestamp(access_exp).strftime("%Y-%m-%d %H:%M:%S")
        if refresh_exp:
            cache_data["refresh_token_exp_readable"] = datetime.fromtimestamp(refresh_exp).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
        logger.info("已保存token缓存: %s", cache_path)
        if access_exp:
            logger.info(" access_token 过期时间: %s", cache_data.get("access_token_exp_readable"))
        if refresh_exp:
            logger.info(" refresh_token 过期时间: %s", cache_data.get("refresh_token_exp_readable"))
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error(f"保存token缓存失败: {cache_path}")


def _bind_client_credentials(client: SteamClient, username: str, password: str):
    client.username = username
    client._password = password


def _refresh_steam_session(client: SteamClient) -> bool:
    """
    刷新当前 SteamClient 的登录会话。
    优先使用 refresh_token 续期 access_token，失败后再回退到账密重登。
    """
    username = client.username
    if not username:
        logger.error("无法刷新Steam会话：缺少Steam用户名")
        return False

    cache = _load_token_cache(username)
    refresh_token = cache.get("refresh_token")
    steamid = cache.get("steamid")

    if refresh_token and steamid:
        logger.info("尝试使用 refresh_token 刷新 Steam 会话...")
        try:
            auth_info = client.loginByRefreshToken(refresh_token, steamid, client.steam_guard)
            if auth_info and isinstance(auth_info, dict):
                _save_token_cache(username, auth_info)
                logger.info("Steam 会话 refresh_token 刷新成功")
                return True
            raise SteamLoginError("验证 refresh_token 会话", detail="refresh_token 未能恢复有效的 Steam 会话")
        except Exception as e:
            _log_steam_login_issue(e, "使用 refresh_token 刷新会话", level="warning", next_action="将尝试使用账号密码重新登录")

    logger.info("refresh_token 刷新失败或不可用, 尝试使用账密重新登录 Steam...")
    try:
        auth_info = client.relogin()
        if auth_info and isinstance(auth_info, dict):
            _save_token_cache(username, auth_info)
            logger.info("Steam 会话账密重新登录成功")
            return True
        raise SteamLoginError("验证重新登录会话", detail="账号密码认证完成后未返回有效会话")
    except Exception as e:
        _log_steam_login_issue(e, "使用账号密码重新登录")
        return False


# ================== 会话与代理设置 ======================


def _setup_client_session(client: SteamClient, config: dict):
    if config["steam_login_ignore_ssl_error"]:
        logger.warning("警告: 已经关闭SSL验证, 请确保你的网络安全")
        client._session.verify = False
        requests.packages.urllib3.disable_warnings()  # type: ignore
    else:
        client._session.verify = True

    if config["steam_local_accelerate"]:
        logger.info("已经启用Steamauto内置加速")
        client._session.auth = accelerator()

    if config.get("use_proxies", False):
        client._session.proxies = config["proxies"]
        logger.info("已经启用Steam代理")


def _check_proxy_availability(config: dict) -> bool:
    if not config.get("use_proxies", False):
        return True
    if not isinstance(config["proxies"], dict):
        logger.error("proxies格式错误，请检查配置文件")
        return False
    logger.info("正在检查代理服务器可用性...")
    try:
        requests.get(
            "https://steamcommunity.com",
            headers={"User-Agent": STEAM_USER_AGENT},
            proxies=config["proxies"],
            timeout=10,
        )
        logger.info("代理服务器可用")
        return True
    except Exception as e:
        _log_steam_login_issue(e, "检查 Steam 代理")
        return False


# ================== 后台刷新线程 ========================


class TokenRefreshThread(threading.Thread):
    """
    后台维护 access_token / refresh_token
    策略:
      - 每次循环检查距离 access_token 过期时间
      - 距离过期 < 3600 秒则尝试刷新 (loginByRefreshToken)
      - 如果 session 失效或刷新失败 -> relogin()
      - 若完全失败 -> 发送通知
    """

    def __init__(self, steam_client: SteamClient, config: dict):
        super().__init__(daemon=True)
        self.steam_client = steam_client
        self.config = config
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.is_set():
            try:
                refresh_succeeded = self._refresh_cycle()
            except Exception as e:
                handle_caught_exception(e, known=True)
                logger.error("后台Token刷新循环出现异常")
                refresh_succeeded = False
            # 计算下一次检查间隔
            wait_seconds = self._compute_wait_interval(refresh_failed=not refresh_succeeded)
            self.stop_event.wait(wait_seconds)

    def _compute_wait_interval(self, refresh_failed: bool = False) -> int:
        """
        基于缓存中 access_token 过期时间决定下一次检查:
          - 距离过期 > 6h: 3h 后检查
          - 距离过期 1h~6h: 1h 后检查
          - 距离过期 < 1h: 10 分钟后检查
          - 没有过期信息: 默认 6 小时
          - 上次刷新失败: 5 分钟后重试
        """
        if refresh_failed:
            return 300
        try:
            cache = _load_token_cache(self.steam_client.username)
            exp = cache.get("access_token_exp_timestamp", 0)
            if not exp:
                return 6 * 3600
            now = int(time.time())
            remain = exp - now
            if remain <= 0:
                return 300  # 已过期, 5分钟后再试(避免频密)
            if remain > 6 * 3600:
                return 3 * 3600
            if remain > 3600:
                return 3600
            return 600
        except Exception:
            return 6 * 3600

    def _refresh_cycle(self) -> bool:
        try:
            with steam_client_mutex.get(self.steam_client.username):
                # 如果会话还活着且 access_token 也未临期则直接返回
                cache = _load_token_cache(self.steam_client.username)
                access_exp = cache.get("access_token_exp_timestamp", 0)
                now = int(time.time())
                need_refresh = False
                if access_exp and access_exp - now < 3600:  # 1 小时内过期
                    need_refresh = True

                if not self.steam_client.is_session_alive():
                    logger.info("检测到会话已失效, 尝试刷新会话...")
                    if _refresh_steam_session(self.steam_client):
                        return True
                    else:
                        send_notification(self.steam_client, "Steam 会话刷新失败", "会话失效后 refresh_token 与重登录均失败，请检查账号或网络")
                        return False

                if need_refresh:
                    if _refresh_steam_session(self.steam_client):
                        return True
                    send_notification(self.steam_client, "Steam 会话维持失败", "自动刷新与重登录均失败，请检查账号或网络")
                    return False
                return True
        except requests.exceptions.RequestException:
            logger.error("无法检查Steam会话状态，请检查网络连接或代理设置")
            return False
        except Exception as e:
            handle_caught_exception(e, known=False)
            return False

    def stop(self):
        self.stop_event.set()


# ================== 登录主流程 ==========================


def login_to_steam(config: dict):
    """
    登录策略 (优先级):
    1) 缓存的 access_token (未过期)
    2) refresh_token 登录
    3) 账密登录
    """
    global token_refresh_thread

    # 读取并验证 Steam 账号信息
    try:
        with open(STEAM_ACCOUNT_INFO_FILE_PATH, "r", encoding=get_encoding(STEAM_ACCOUNT_INFO_FILE_PATH)) as f:
            try:
                steam_account_info = json5.loads(f.read())
            except Exception as e:
                logger.debug("解析 Steam 账号配置失败", exc_info=True)
                detail = str(e).replace("<string>", STEAM_ACCOUNT_INFO_FILE_PATH)
                raise SteamAccountConfigError(f"JSON5 语法错误：{detail}") from e
        steam_account_info = _validate_steam_account_info(steam_account_info)
    except FileNotFoundError:
        logger.error("未检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + ", 请添加后再进行操作!")
        pause()
        return None
    except SteamAccountConfigError as e:
        logger.error(_format_steam_account_config_error(e))
        pause()
        return None

    username = steam_account_info["steam_username"]
    password = steam_account_info["steam_password"]
    if steam_client_mutex.get(username) is None:
        steam_client_mutex[username] = threading.Lock()

    config["use_proxies"] = config.get("use_proxies", False)
    if not _check_proxy_availability(config):
        pause()
        return None

    token_cache = _load_token_cache(username)
    now = int(time.time())

    # 1. 尝试使用缓存 access_token
    access_token = token_cache.get("access_token")
    access_exp = token_cache.get("access_token_exp_timestamp", 0)
    steamid_cache = token_cache.get("steamid")
    if access_token and steamid_cache and access_exp and access_exp - now > 60:
        logger.info("检测到缓存的未过期 access_token, 尝试直接恢复会话...")
        try:
            if config.get("use_proxies", False):
                client = SteamClient(api_key="", proxies=config["proxies"])
            else:
                client = SteamClient(api_key="")
            _setup_client_session(client, config)
            if client.set_and_verify_access_token(steamid_cache, access_token, steam_account_info):
                logger.info("使用缓存 access_token 登录成功")
                _bind_client_credentials(client, username, password)
                # 启动刷新线程
                _start_token_refresh_thread(client, config)
                return client
            else:
                logger.warning("缓存 access_token 已失效，进入 refresh_token 流程")
        except Exception as e:
            _log_steam_login_issue(e, "使用缓存 access_token 恢复会话", level="warning", next_action="将尝试使用 refresh_token 或账号密码登录")

    # 2. 尝试 refresh_token 登录
    refresh_token = token_cache.get("refresh_token")
    refresh_exp = token_cache.get("refresh_token_exp_timestamp", 0)
    if refresh_token and steamid_cache:
        if refresh_exp and refresh_exp <= now:
            logger.warning("refresh_token 已过期，将回退到账密登录")
        else:
            remaining = refresh_exp - now if refresh_exp else None
            if remaining:
                hours = remaining // 3600
                if hours > 0:
                    logger.info(f"refresh_token 预计还有 ~{hours} 小时过期")
            logger.info("尝试使用 refresh_token 登录...")
            try:
                if config.get("use_proxies", False):
                    client = SteamClient(api_key="", proxies=config["proxies"])
                else:
                    client = SteamClient(api_key="")
                _setup_client_session(client, config)
                auth_info = client.loginByRefreshToken(refresh_token, steamid_cache, steam_account_info)
                if auth_info and client.is_session_alive():
                    logger.info("使用 refresh_token 登录成功")
                    _save_token_cache(username, auth_info)
                    _bind_client_credentials(client, username, password)
                    _start_token_refresh_thread(client, config)
                    return client
                else:
                    _log_steam_login_issue(
                        SteamLoginError("验证 refresh_token 会话", detail="refresh_token 未能恢复有效的 Steam 会话"),
                        "验证 refresh_token 会话",
                        level="warning",
                        next_action="将回退到账密登录",
                    )
            except Exception as e:
                _log_steam_login_issue(e, "使用 refresh_token 登录", level="warning", next_action="将回退到账密登录")

    # 3. 账密登录
    logger.info("正在使用账密登录Steam...")
    try:
        if config.get("use_proxies", False):
            client = SteamClient(api_key="", proxies=config["proxies"])
        else:
            client = SteamClient(api_key="")
        _setup_client_session(client, config)
        if config["use_proxies"] and config["steam_local_accelerate"]:
            logger.warning("检测到你已经同时开启内置加速和代理功能！正常情况下不推荐通过这种方式使用软件")
        logger.info("正在登录...")
        auth_info = client.login(username, password, steam_account_info)
        if client.is_session_alive():
            logger.info("账密登录成功")
            _bind_client_credentials(client, username, password)
            if auth_info and isinstance(auth_info, dict):
                _save_token_cache(username, auth_info)
            _start_token_refresh_thread(client, config)
            return client
        else:
            _log_steam_login_issue(
                SteamLoginError("验证 Steam 社区会话", detail="账号认证流程已完成，但社区会话验证未通过"),
                "验证 Steam 社区会话",
            )
            return None
    except Exception as e:
        _log_steam_login_issue(e, "使用账号密码登录")
        pause()
        return None


def _start_token_refresh_thread(steam_client: SteamClient, config: dict):
    global token_refresh_thread
    try:
        for t in token_refresh_thread:
            if t.steam_client.username == steam_client.username and t.is_alive():
                logger.info("检测到已有TokenRefreshThread在运行，跳过启动新线程")
                return
        thread = TokenRefreshThread(steam_client, config)
        token_refresh_thread.append(thread)
        thread.start()
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error("启动 TokenRefreshThread 失败")


# 用于外部报价处理器交互
# 使用前请自行确保配置文件中 external_offer_handler 配置项正确
# 具体使用方法请直接查看代码，不提供额外文档
def external_handler(tradeOfferId, desc) -> bool:
    """
    与外部报价处理器交互（与 plugins/ExternalAutoAcceptOffer.py 保持一致）：
    1. 先查询 /getToAcceptOffers 列表，如果报价号已经在待接受列表中则先调用 /deleteOffer 删除该报价，然后返回 True（避免重复提交）
    2. 否则将报价提交到 /submit，由外部处理器决定是否处理（根据返回的 deliver 字段）
    """
    if not isinstance(config, dict):
        return True
    external_handler = config.get("external_offer_handler", "").strip()
    if not external_handler:
        return True

    base_url = external_handler.rstrip("/")

    # 先检查外部处理器的待接受列表，若已存在则删除并直接返回 True
    try:
        get_url = f"{base_url}/getToAcceptOffers"
        logger.info(f"正在检查外部报价处理器的待接受列表 {get_url}，是否包含报价号 {tradeOfferId} ...")
        resp = requests.get(get_url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        offers = payload.get("data", []) if isinstance(payload, dict) else []

        # offers 期望为字典列表，每项包含 "offerId"
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            offer_id = offer.get("offerId")
            if offer_id is None:
                continue
            if str(offer_id) == str(tradeOfferId):
                logger.info(f"报价号 {tradeOfferId} 已存在于外部处理器的待接受列表，尝试删除后直接接受")
                try:
                    delete_url = f"{base_url}/deleteOffer"
                    del_resp = requests.post(delete_url, json={"offerId": offer_id}, timeout=10)
                    del_resp.raise_for_status()
                    del_result = del_resp.json()
                    if isinstance(del_result, dict) and del_result.get("status") == "ok":
                        logger.info(f"已从外部处理器删除报价: {tradeOfferId}")
                    else:
                        logger.error(f"从外部处理器删除报价失败: {tradeOfferId} -> {del_result}")
                except Exception as e:
                    logger.error(f"向外部处理器请求删除报价时出错: {e}")
                return True
    except Exception:
        # 无法获取待接受列表时忽略此步，继续走提交逻辑
        logger.debug("无法检查外部处理器的待接受列表，继续提交 /submit")

    # 提交到 /submit，由外部处理器决定是否处理
    external_handler_url = base_url + "/submit"
    try:
        data = {"offerId": tradeOfferId, "description": desc}
        logger.info(f"正在将报价号 {tradeOfferId} 发送到外部报价处理器 {external_handler_url} ...")
        response = requests.post(external_handler_url, json=data, timeout=15)
        try:
            result = response.json()
        except Exception:
            logger.error(f"无法解析外部处理器 {external_handler_url} 的响应为 JSON，已跳过该报价")
            return False

        if isinstance(result, dict) and result.get("deliver"):
            logger.info(f"外部报价处理器接受处理报价号 {tradeOfferId}")
            return True
        else:
            logger.info(f"外部报价处理器拒绝报价号 {tradeOfferId}，已跳过")
            return False
    except Exception:
        logger.error("无法连接到外部报价处理器，已跳过该报价")
        return False


def accept_trade_offer(client: SteamClient, mutex, tradeOfferId, retry=False, desc="", network_retry_count=0, reportToExternal=True, session_retry=False):
    max_network_retries = 3
    network_retry_delay = 5

    if reportToExternal:
        if not external_handler(tradeOfferId, desc):
            return True

    try:
        with mutex:
            client.accept_trade_offer(str(tradeOfferId))
        send_notification(client, f"报价号：{tradeOfferId}\n{desc}", title="接受报价成功")
        return True
    except Exception as e:
        if retry:
            logger.error(f"接受报价号{tradeOfferId}失败！")
            return False

        # 网络错误重试
        if isinstance(e, RequestException):
            if network_retry_count < max_network_retries:
                logger.warning(f"接受报价号{tradeOfferId}遇到网络错误，正在重试 ({network_retry_count + 1}/{max_network_retries})...")
                handle_caught_exception(e, "SteamClient", known=True)
                time.sleep(network_retry_delay)
                return accept_trade_offer(
                    client,
                    mutex,
                    tradeOfferId,
                    retry=False,
                    desc=desc,
                    network_retry_count=network_retry_count + 1,
                    reportToExternal=False,
                    session_retry=session_retry,
                )
            else:
                logger.error(f"接受报价号{tradeOfferId}网络错误重试次数已达到上限({max_network_retries})，操作失败")
                handle_caught_exception(e, "SteamClient", known=True)
                send_notification(client, f"报价号：{tradeOfferId}\n{desc}", title="接受报价失败(网络错误)")
                return False

        if isinstance(e, ValueError):
            if "Accepted" in str(e):
                logger.warning(f"报价号 {tradeOfferId} 已经处理过，无需再次处理")
                handle_caught_exception(e, "SteamClient", known=True)
                return True
        missing_login_cookie = isinstance(e, ApiException) and "steamLoginSecure" in str(e)
        if isinstance(e, steampy.exceptions.InvalidCredentials) or missing_login_cookie:
            should_refresh = missing_login_cookie or "Invalid API key" in str(e)
            if not should_refresh:
                try:
                    should_refresh = not client.is_session_alive()
                except Exception:
                    should_refresh = True

            if should_refresh and not session_retry:
                logger.warning(f"接受报价号{tradeOfferId}时检测到Steam会话失效，正在刷新会话后重试...")
                handle_caught_exception(e, "SteamClient", known=True)
                with mutex:
                    refreshed = _refresh_steam_session(client)
                if refreshed:
                    logger.info(f"Steam会话刷新成功，正在重试接受报价号{tradeOfferId}")
                    return accept_trade_offer(
                        client,
                        mutex,
                        tradeOfferId,
                        retry=False,
                        desc=desc,
                        network_retry_count=network_retry_count,
                        reportToExternal=False,
                        session_retry=True,
                    )
                logger.error(f"接受报价号{tradeOfferId}失败：Steam会话刷新失败，放弃本次处理")
                send_notification(client, f"报价号：{tradeOfferId}\n{desc}", title="接受报价失败(会话刷新失败)")
                return False

            logger.error(f"接受报价号{tradeOfferId}失败：会话或凭据无效，放弃本次处理")
            handle_caught_exception(e, "SteamClient", known=True)
            send_notification(client, f"报价号：{tradeOfferId}\n{desc}", title="接受报价失败(会话无效)")
            return False

        if isinstance(e, steampy.exceptions.ConfirmationExpected):
            logger.error(f"接受报价号{tradeOfferId}失败：会话或凭据无效，放弃本次处理")
            handle_caught_exception(e, "SteamClient", known=True)
            send_notification(client, f"报价号：{tradeOfferId}\n{desc}", title="接受报价失败(会话无效)")
            return False
        if isinstance(e, KeyError):
            logger.error(f"接受报价号{tradeOfferId}失败！未找到报价号或报价号已过期")
            return False
        
        if "substring not found" in str(e):
            logger.error(f"由于网络被Steam风控，报价号 {tradeOfferId} 处理失败，请检查服务器IP/代理软件或稍后再试。")
            handle_caught_exception(e, "SteamClient", known=True)
            return False
        # 其它错误统一处理
        handle_caught_exception(e, "SteamClient")
        logger.error(f"接受报价号{tradeOfferId}失败！")
        send_notification(client, f"报价号：{tradeOfferId}\n{desc}", title="接受报价失败")
        return False


def get_cs2_inventory(client: SteamClient, mutex):
    inventory = None
    try:
        with mutex:
            inventory = client.get_my_inventory(game=GameOptions.CS)  # type: ignore
            logger.log(5, "获取到的Steam库存:" + json.dumps(inventory, ensure_ascii=False))
    except Exception as e:
        handle_caught_exception(e, "SteamClient", known=True)
        send_notification(client, "获取库存失败，请检查服务器网络", title="获取库存失败")
    return inventory
