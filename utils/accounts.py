"""交易平台账号层：登录 / 登出 / 状态查询。

四个平台的「登录」语义并不相同，这是本模块存在的核心理由：

| 平台 | 凭据形态 | 存放位置 | 登录动作 |
|------|----------|----------|----------|
| BUFF | 会话串   | `config/buff_cookies_{user}.txt` | 手机扫码 |
| UU   | token    | `config/uu_token_{user}.txt`     | 手机号 + 短信验证码 |
| C5   | 无会话   | 配置项 `c5_auto_accept_offer.app_key` | 校验 AppKey |
| ECO  | 无会话   | 配置项 `ecosteam.partnerId` + `config/rsakey.txt` 私钥 | 签名调接口校验 |

因此 C5/ECO 的「登录」本质是「校验凭据是否可用」，「登出」是清掉配置项。

**本模块只做前台短命进程能做的事**：交互式登录、写凭据、查状态、清凭据。
让运行中的守护进程「接上」新登录的平台，由调用方通过控制通道发指令完成
（见 Steamauto.py 的 plugin.retry）。

本模块刻意**不导入** utils.logger（避免短命的 CLI 进程平白生成日志文件），
输出交给调用方决定。
"""

import json
import os
import sys

from utils import config_writer, control, daemon, static

# ------------------------------------------------------------------ 平台定义

#: 平台别名 -> 规范名（大小写不敏感）
_ALIASES = {
    "buff": "buff",
    "buffapi": "buff",
    "网易buff": "buff",
    "uu": "uu",
    "uuyoupin": "uu",
    "悠悠": "uu",
    "悠悠有品": "uu",
    "c5": "c5",
    "c5game": "c5",
    "eco": "eco",
    "ecosteam": "eco",
    "ecos": "eco",
}

#: 平台显示名
DISPLAY = {
    "buff": "BUFF（网易BUFF）",
    "uu": "UU（悠悠有品）",
    "c5": "C5（C5Game）",
    "eco": "ECO（ECOsteam）",
}

#: 平台 -> 对应的 Steamauto 插件配置键（用于让守护进程重启该插件）
PLUGIN_KEY = {
    "buff": "buff_auto_accept_offer",
    "uu": "uu",
    "c5": "c5_auto_accept_offer",
    "eco": "ecosteam",
}

#: 平台 -> 配置里「是否启用该平台插件」的键
ENABLE_KEY = PLUGIN_KEY

#: 探测/登录时的网络超时（秒）
NET_TIMEOUT = 20

#: 交互式扫码登录的总超时（秒）。二维码轮询本身是无限循环，必须加超时，
#: 否则在无人扫码（或没有终端）时会永久挂住。
QRCODE_TIMEOUT = 180


def resolve(name):
    """把用户输入的平台名解析为规范名；无法识别返回 None。"""
    if not name:
        return None
    return _ALIASES.get(str(name).strip().lower().replace(" ", ""))


def platforms():
    """返回全部规范平台名。"""
    return ("buff", "uu", "c5", "eco")


def parse_platforms(raw, default_all=True):
    """解析逗号分隔的平台列表。返回 (规范名列表, 无法识别的原始词列表)。"""
    if not raw:
        return (list(platforms()) if default_all else [], [])
    ok, bad = [], []
    for part in str(raw).replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        name = resolve(part)
        if name is None:
            bad.append(part)
        elif name not in ok:
            ok.append(name)
    return ok, bad


# ------------------------------------------------------------------ 公共辅助

def stdin_is_interactive():
    """当前 stdin 是否是「真正可交互的终端」。

    **不能用 `os.isatty(0)`**：Windows 上 NUL 设备（`subprocess.DEVNULL`、
    `> NUL` 重定向）会被 MSVCRT 判定为字符设备从而返回 True。结果是登录流程在
    根本没有终端时照样去 `input()`（→ EOFError）或进入扫码轮询（→ 无限等待）。

    这里在 Windows 上追加 `GetConsoleMode` 判定：真实控制台句柄能拿到模式，
    而 NUL / 管道 / 文件句柄会失败（ERROR_INVALID_HANDLE），可准确区分。
    """
    if os.environ.get("STEAMAUTO_DAEMON") == "1" or os.environ.get("STEAMAUTO_NO_PAUSE") == "1":
        return False
    try:
        if not (sys.stdin and sys.stdin.isatty()):
            return False
    except Exception:
        return False

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
            if handle in (0, -1, None):
                return False
            mode = wintypes.DWORD()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
        except Exception:
            return False
    return True


def steam_username():
    """从 Steam 账号信息文件读取用户名（用于定位 per-user 凭据文件）。

    BUFF/UU 的凭据文件名带用户名，因此没配 Steam 用户名时无法定位。
    """
    path = static.STEAM_ACCOUNT_INFO_FILE_PATH
    if not os.path.exists(path):
        return ""
    try:
        import json5

        with open(path, "r", encoding="utf-8") as f:
            data = json5.load(f)
        return str(data.get("steam_username", "") or "")
    except Exception:
        return ""


def load_config():
    """读取主配置文件（容忍注释与尾逗号）。"""
    return config_writer.load_config(static.CONFIG_FILE_PATH)


def credential_path(platform):
    """返回该平台凭据文件的绝对路径；无文件凭据的平台返回 None。"""
    user = steam_username()
    if platform == "buff":
        return static.BUFF_COOKIES_FILE_PATH.format(steam_username=user)
    if platform == "uu":
        return static.UU_TOKEN_FILE_PATH.format(steam_username=user)
    return None


def _proxies(cfg, enable_key):
    """按插件配置决定是否使用全局代理；返回 requests 可用的 proxies 或 None。"""
    section = cfg.get(enable_key)
    if not isinstance(section, dict) or not section.get("use_proxies"):
        return None
    proxies = cfg.get("proxies")
    return proxies if isinstance(proxies, dict) else None


def _read_text(path):
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ------------------------------------------------------------------ 状态查询

def _buff_state(cfg, live):
    """BUFF：读 cookie 文件，必要时联网校验权限并取昵称。"""
    user = steam_username()
    path = credential_path("buff")
    info = _blank_state("buff")
    info["credential_file"] = path
    if not user:
        info["error"] = "未配置 Steam 用户名（config/steam_account_info.json5），无法定位 BUFF 凭据文件"
        return info

    raw = _read_text(path)
    session = raw.replace("session=", "").strip()
    if not session:
        info["error"] = "尚无 BUFF 登录凭据"
        return info
    info["configured"] = True
    if not live:
        info["logged_in"] = True  # 未联网校验，只能确认「有凭据」
        return info

    try:
        from utils import buff_helper

        proxies = _proxies(cfg, "buff_auto_accept_offer")
        if not buff_helper.is_session_has_enough_permission("session=" + session, proxies):
            info["error"] = "凭据已失效（权限不足），请重新登录"
            return info
        info["logged_in"] = True
        info["connected"] = True
        info["account"] = buff_helper.get_buff_username("session=" + session) or None
        # 可用余额（可提现）
        try:
            from api.BuffApi import BuffAccount

            info["balance"] = (BuffAccount("session=" + session, proxies=proxies).get_user_brief_assest() or {}).get("total_able_withdraw_amount")
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001 - 探测失败不抛出
        info["error"] = "校验失败：%s" % (e,)
    return info


def _uu_state(cfg, live):
    """UU：读 token 文件，必要时联网校验并取昵称。"""
    user = steam_username()
    path = credential_path("uu")
    info = _blank_state("uu")
    info["credential_file"] = path
    if not user:
        info["error"] = "未配置 Steam 用户名，无法定位 UU 凭据文件"
        return info

    token = _read_text(path)
    if not token:
        info["error"] = "尚无 UU 登录凭据"
        return info
    info["configured"] = True
    if not live:
        info["logged_in"] = True
        return info

    try:
        import api.uuyoupinapi as uuyoupinapi

        proxies = _proxies(cfg, "uu_auto_accept_offer")
        account = uuyoupinapi.UUAccount(token, proxy=proxies)
        nickname = account.get_user_nickname()
        if not nickname:
            info["error"] = "Token 已失效，请重新登录"
            return info
        info["logged_in"] = True
        info["connected"] = True
        info["account"] = nickname
        # 可用余额
        try:
            info["balance"] = account.get_balance().get("available")
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        info["error"] = "校验失败：%s" % (e,)
    return info


def _c5_state(cfg, live):
    """C5：无会话，只有 AppKey；联网校验其有效性。"""
    info = _blank_state("c5")
    section = cfg.get("c5_auto_accept_offer") or {}
    app_key = str(section.get("app_key", "") or "").strip()
    if not app_key:
        info["error"] = "未配置 AppKey（config set c5_auto_accept_offer.app_key <key>）"
        return info
    info["configured"] = True
    info["credential_file"] = None
    if not live:
        info["logged_in"] = True
        return info
    try:
        from api.PyC5Game import C5Account

        if C5Account(app_key).checkAppKey:
            info["logged_in"] = True
            info["connected"] = True
        else:
            info["error"] = "AppKey 无效"
    except Exception as e:  # noqa: BLE001
        info["error"] = "校验失败：%s" % (e,)
    return info


def _eco_state(cfg, live):
    """ECO：无会话，需要 partnerId + rsakey 私钥；联网校验。"""
    info = _blank_state("eco")
    section = cfg.get("ecosteam") or {}
    partner_id = str(section.get("partnerId", "") or "").strip()
    if not partner_id:
        info["error"] = "未配置 partnerId（config set ecosteam.partnerId <id>）"
        return info
    if not os.path.exists(static.ECOSTEAM_RSAKEY_FILE):
        info["error"] = "缺少私钥文件 %s" % static.ECOSTEAM_RSAKEY_FILE
        return info
    rsa_key = _read_text(static.ECOSTEAM_RSAKEY_FILE)
    if not rsa_key:
        info["error"] = "私钥文件为空：%s" % static.ECOSTEAM_RSAKEY_FILE
        return info
    if "PUBLIC" in rsa_key:
        info["error"] = "私钥文件里放的是公钥，需填入 Private key"
        return info
    info["configured"] = True
    info["credential_file"] = static.ECOSTEAM_RSAKEY_FILE
    if not live:
        info["logged_in"] = True
        return info
    try:
        from api.PyECOsteam import ECOsteamClient

        client = ECOsteamClient(partner_id, rsa_key, qps=section.get("qps", 10))
        data = client.GetTotalMoney().json()
        result = (data or {}).get("ResultData") or {}
        if result.get("UserName"):
            info["logged_in"] = True
            info["connected"] = True
            info["account"] = str(result.get("UserName"))
        else:
            info["error"] = "校验失败，请检查 partnerId 与私钥"
    except Exception as e:  # noqa: BLE001
        info["error"] = "校验失败：%s" % (e,)
    return info


_STATE_PROBES = {
    "buff": _buff_state,
    "uu": _uu_state,
    "c5": _c5_state,
    "eco": _eco_state,
}


def _blank_state(platform):
    return {
        "platform": platform,
        "display": DISPLAY[platform],
        "configured": False,
        "logged_in": False,
        "connected": False,
        "account": None,
        "credential_file": None,
        "error": None,
    }


def account_state(platform, cfg=None, live=True):
    """探测单个平台的账号状态（本地探测，不经过守护进程）。"""
    cfg = load_config() if cfg is None else cfg
    return _STATE_PROBES[platform](cfg, live)


def all_account_states(live=True):
    """探测全部平台，返回 {platform: state}。"""
    cfg = load_config()
    return {p: _STATE_PROBES[p](cfg, live) for p in platforms()}


def steam_state(live=True):
    """Steam 会话状态（影响 BUFF 的「用 Steam 登录」方式是否可用）。

    Steam 登录与否只是可选路径，不影响本工具的主体功能，因此单独列出。

    注意：这里**绝不发起真实登录**（那会弹二维码/要密码）。能用的信息来源只有
    运行中的进程（它有真实会话）与本地会话缓存文件（弱证据）。
    """
    user = steam_username()
    info = {
        "platform": "steam",
        "display": "Steam",
        "configured": bool(user),
        "logged_in": False,
        "connected": False,
        "account": user or None,
        "credential_file": None,
        "source": None,
        "error": None,
    }
    if not user:
        info["error"] = "未配置 Steam 用户名（可选；不影响买卖/上架等免鉴权功能）"
        return info

    cache = os.path.join(static.SESSION_FOLDER, "steam_account_%s.json" % user.lower())
    info["credential_file"] = cache

    # 优先问运行中的进程（D7）
    running, state = daemon.is_running()
    if running and state.get("control_ok"):
        ok, resp = control.request("account.status", {"platform": "steam"}, port=state.get("port"))
        if ok and isinstance(resp, dict) and isinstance(resp.get("steam"), dict):
            merged = dict(info)
            merged.update(resp["steam"])
            merged["source"] = "运行中的进程"
            return merged

    # 退化：只看本地会话缓存文件是否存在（弱证据，不代表会话仍有效）
    info["source"] = "本地文件"
    if os.path.exists(cache):
        info["logged_in"] = True
        if live:
            info["error"] = "凭据缓存存在，但需程序运行时才能确认会话是否仍有效"
    else:
        info["error"] = "无 Steam 会话缓存（未登录 Steam 亦可使用买卖/上架等功能）"
    return info


# ------------------------------------------------------------------ 登录

def login(platform, interactive=True):
    """登录指定平台。返回 (ok: bool, message: str, detail: dict)。

    必须在**有交互终端**的前台进程里调用（BUFF 扫码 / UU 短信验证码）。
    """
    cfg = load_config()
    if platform == "buff":
        return _login_buff(cfg)
    if platform == "uu":
        return _login_uu(cfg)
    if platform == "c5":
        return _login_c5(cfg)
    if platform == "eco":
        return _login_eco(cfg)
    return False, "未知平台：%s" % platform, {}


def _login_buff(cfg):
    user = steam_username()
    if not user:
        return False, "未配置 Steam 用户名（config/steam_account_info.json5），无法定位 BUFF 凭据文件", {}
    if not stdin_is_interactive():
        return False, "BUFF 登录需要扫码，请在交互式终端中执行（当前没有可交互的终端）", {}

    from utils import buff_helper
    from utils.steam_client import OfflineSteamClient

    proxies = _proxies(cfg, "buff_auto_accept_offer")
    # 只做扫码：不走 Steam OpenID（那需要真实 Steam 会话），也不需要已登录 Steam
    client = OfflineSteamClient(user)
    try:
        # 必须带超时：二维码轮询是「等扫码」的无限循环，没有超时会在无人扫码时永久挂住
        session = buff_helper.login_to_buff_by_qrcode(client, proxies, timeout=QRCODE_TIMEOUT)
    except EOFError:
        return False, "BUFF 登录需要扫码，但当前没有可交互的终端", {}
    except Exception as e:  # noqa: BLE001
        return False, "BUFF 扫码登录异常：%s" % (e,), {}
    if not session:
        return False, "BUFF 扫码登录失败或超时（%s 秒内未完成扫码）" % QRCODE_TIMEOUT, {}

    session = str(session).replace("session=", "")
    if not buff_helper.is_session_has_enough_permission("session=" + session, proxies):
        return False, "BUFF 登录成功但权限不足（请在 BUFF 端确认账号状态）", {}

    path = credential_path("buff")
    _write_text(path, "session=" + session)
    nickname = buff_helper.get_buff_username("session=" + session) or ""
    return True, "BUFF 登录成功%s" % ("（账号：%s）" % nickname if nickname else ""), {
        "account": nickname or None,
        "credential_file": path,
    }


def _login_uu(cfg):
    user = steam_username()
    if not user:
        return False, "未配置 Steam 用户名，无法定位 UU 凭据文件", {}
    if not stdin_is_interactive():
        return False, "UU 登录需要输入手机号与短信验证码，请在交互式终端中执行（当前没有可交互的终端）", {}

    from utils import uu_helper
    import api.uuyoupinapi as uuyoupinapi

    proxies = _proxies(cfg, "uu_auto_accept_offer")
    try:
        token = uu_helper.get_token_automatically(proxies)
    except EOFError:
        return False, "UU 登录需要输入手机号与短信验证码，但当前没有可交互的终端", {}
    except KeyboardInterrupt:
        return False, "已取消 UU 登录", {}
    except Exception as e:  # noqa: BLE001
        return False, "UU 登录异常：%s" % (e,), {}
    if not token:
        return False, "UU 登录失败（验证码错误或未发送验证短信）", {}

    try:
        nickname = uuyoupinapi.UUAccount(str(token), proxy=proxies).get_user_nickname()
    except Exception as e:  # noqa: BLE001
        return False, "UU 登录后校验失败：%s" % (e,), {}
    if not nickname:
        return False, "UU Token 校验失败", {}

    path = credential_path("uu")
    _write_text(path, str(token))
    return True, "UU 登录成功（账号：%s）" % nickname, {"account": nickname, "credential_file": path}


def _login_c5(cfg):
    section = cfg.get("c5_auto_accept_offer") or {}
    app_key = str(section.get("app_key", "") or "").strip()
    if not app_key:
        return False, "未配置 AppKey。请先执行：config set c5_auto_accept_offer.app_key <你的AppKey>", {}
    try:
        from api.PyC5Game import C5Account

        if C5Account(app_key).checkAppKey:
            return True, "C5 AppKey 校验通过", {"account": None, "credential_file": None}
    except Exception as e:  # noqa: BLE001
        return False, "C5 校验异常：%s" % (e,), {}
    return False, "C5 AppKey 无效，请检查后重试", {}


def _login_eco(cfg):
    section = cfg.get("ecosteam") or {}
    partner_id = str(section.get("partnerId", "") or "").strip()
    if not partner_id:
        return False, "未配置 partnerId。请先执行：config set ecosteam.partnerId <你的partnerId>", {}
    path = static.ECOSTEAM_RSAKEY_FILE
    if not os.path.exists(path):
        return False, "缺少私钥文件 %s，请写入 Private key 后重试" % path, {}
    rsa_key = _read_text(path)
    if not rsa_key:
        return False, "私钥文件为空：%s" % path, {}
    if "PUBLIC" in rsa_key:
        return False, "私钥文件里放的是公钥，请填入 Private key", {}

    try:
        from api.PyECOsteam import ECOsteamClient

        client = ECOsteamClient(partner_id, rsa_key, qps=section.get("qps", 10))
        data = client.GetTotalMoney().json()
        user_name = ((data or {}).get("ResultData") or {}).get("UserName")
        if user_name:
            return True, "ECOsteam 登录成功（账号：%s）" % user_name, {"account": str(user_name), "credential_file": path}
    except Exception as e:  # noqa: BLE001
        return False, "ECOsteam 登录异常：%s" % (e,), {}
    return False, "ECOsteam 登录失败，请检查 partnerId 与私钥", {}


# ------------------------------------------------------------------ 登出

def logout(platform):
    """登出指定平台（一律清凭据 + 清配置项，返回 (ok, message, cleared:list)）。"""
    cleared = []
    try:
        if platform == "buff":
            path = credential_path("buff")
            if path:
                _write_text(path, "session=")
                cleared.append(path)
        elif platform == "uu":
            path = credential_path("uu")
            if path:
                _write_text(path, "")
                cleared.append(path)
        elif platform == "c5":
            cfg_path = static.CONFIG_FILE_PATH
            if os.path.exists(cfg_path):
                changed, _created, _lit = config_writer.set_value(
                    cfg_path, "c5_auto_accept_offer.app_key", None, literal='""'
                )
                if changed:
                    cleared.append("config:c5_auto_accept_offer.app_key")
        elif platform == "eco":
            cfg_path = static.CONFIG_FILE_PATH
            if os.path.exists(cfg_path):
                changed, _created, _lit = config_writer.set_value(
                    cfg_path, "ecosteam.partnerId", None, literal='""'
                )
                if changed:
                    cleared.append("config:ecosteam.partnerId")
            if os.path.exists(static.ECOSTEAM_RSAKEY_FILE):
                _write_text(static.ECOSTEAM_RSAKEY_FILE, "")
                cleared.append(static.ECOSTEAM_RSAKEY_FILE)
        else:
            return False, "未知平台：%s" % platform, []
    except Exception as e:  # noqa: BLE001
        return False, "登出失败：%s" % (e,), cleared
    return True, "已登出 %s" % DISPLAY.get(platform, platform), cleared


# ------------------------------------------------------------------ 生效通知

def notify_runtime(platform, timeout=8.0):
    """让运行中的守护进程重试该平台插件（D1b：动态重启插件线程）。

    :return: (delivered: bool, message: str)
    """
    running, state = daemon.is_running()
    if not running:
        return False, "程序未在运行"
    if not state.get("control_ok"):
        return False, "程序在运行但控制通道不可达"

    plugin_key = PLUGIN_KEY.get(platform)
    ok, resp = control.request(
        "plugin.retry",
        {"platform": platform, "plugin_key": plugin_key, "wake": True},
        port=state.get("port"),
        timeout=timeout,
    )
    if not ok:
        return False, str(resp)
    if isinstance(resp, dict):
        return True, str(resp.get("message") or json.dumps(resp, ensure_ascii=False))
    return True, "已通知运行中的进程"
