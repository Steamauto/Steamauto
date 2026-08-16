"""Steamauto GUI 的平台登录模块（C 档：登录流程 GUI 化）。

登录在 GUI 进程内完成，结果缓存到 Steamauto 的 session/config 文件，
运行时子进程读到缓存后跳过控制台交互。

实现方式：复用 Steamauto 的登录函数，通过最小 monkeypatch 把
pause / 二维码 / input 交互桥接到浏览器，核心代码零改动。
"""
import builtins
import json5
import re
import sys
import threading

from . import config_editor

# 确保项目根在 sys.path，以便 import Steamauto 核心模块
if config_editor.PROJECT_ROOT not in sys.path:
    sys.path.insert(0, config_editor.PROJECT_ROOT)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _clean_ansi(text):
    return _ANSI_RE.sub("", str(text))


class InteractionBridge:
    """浏览器交互桥接：登录线程请求输入/展示二维码，浏览器轮询并响应。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._request = None
        self._response = None
        self._request_event = threading.Event()
        self._response_event = threading.Event()

    def ask_input(self, prompt):
        """请求浏览器输入，阻塞直到用户响应。返回用户输入字符串。"""
        with self._lock:
            self._request = {"type": "input", "prompt": _clean_ansi(prompt)}
            self._response = None
            self._request_event.set()
            self._response_event.clear()
        self._response_event.wait()
        with self._lock:
            return self._response

    def show_qrcode(self, url):
        """展示二维码（不阻塞，调用方自行轮询登录状态）。"""
        with self._lock:
            self._request = {"type": "qrcode", "url": url}
            self._request_event.set()

    def peek_request(self):
        if not self._request_event.is_set():
            return None
        with self._lock:
            return dict(self._request) if self._request else None

    def respond(self, value):
        with self._lock:
            self._response = value
            self._request = None
            self._request_event.clear()
            self._response_event.set()

    def clear(self):
        with self._lock:
            self._request = None
            self._request_event.clear()


bridge = InteractionBridge()

# 登录状态：idle / running / success / failed
_login_state = {
    "steam": {"status": "idle", "msg": ""},
    "buff": {"status": "idle", "msg": ""},
    "uu": {"status": "idle", "msg": ""},
}
_login_lock = threading.Lock()
_steam_client = None  # Steam 登录成功后保存，供 BUFF / 悠悠有品复用


def _set_state(platform, status, msg):
    with _login_lock:
        _login_state[platform] = {"status": status, "msg": msg}


def get_state():
    with _login_lock:
        return {k: dict(v) for k, v in _login_state.items()}


def get_steam_client():
    return _steam_client


def _read_config():
    try:
        with open(config_editor.CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
            return json5.load(f)
    except Exception:
        return {}


def login_steam():
    """Steam 登录（后台线程调用）。"""
    from utils import steam_client as sc

    _set_state("steam", "running", "正在登录 Steam...")
    original_pause = sc.pause
    sc.pause = lambda *a, **k: None
    try:
        config = _read_config()
        client = sc.login_to_steam(config)
        global _steam_client
        if client is not None:
            _steam_client = client
            _set_state("steam", "success", "Steam 登录成功：" + str(getattr(client, "username", "")))
        else:
            _set_state("steam", "failed", "Steam 登录失败，请检查账号信息与网络")
    except Exception as e:  # noqa: BLE001
        _set_state("steam", "failed", "Steam 登录异常：" + str(e))
    finally:
        sc.pause = original_pause


def login_buff():
    """BUFF 登录（后台线程调用，依赖已登录的 SteamClient）。"""
    from utils import buff_helper
    from utils.logger import PluginLogger

    if _steam_client is None:
        _set_state("buff", "failed", "请先登录 Steam")
        return
    _set_state("buff", "running", "正在登录 BUFF...")
    original_draw = buff_helper.qrcode_terminal.draw
    buff_helper.qrcode_terminal.draw = lambda url: bridge.show_qrcode(url)
    try:
        session = buff_helper.get_valid_session_for_buff(_steam_client, PluginLogger("BuffLoginSolver"))
        if session:
            _set_state("buff", "success", "BUFF 登录成功")
        else:
            _set_state("buff", "failed", "BUFF 登录失败")
    except Exception as e:  # noqa: BLE001
        _set_state("buff", "failed", "BUFF 登录异常：" + str(e))
    finally:
        buff_helper.qrcode_terminal.draw = original_draw
        bridge.clear()


def login_uu():
    """悠悠有品登录（后台线程调用，依赖已登录的 SteamClient）。"""
    from utils import uu_helper

    if _steam_client is None:
        _set_state("uu", "failed", "请先登录 Steam")
        return
    _set_state("uu", "running", "正在登录悠悠有品...")
    original_input = builtins.input
    builtins.input = lambda prompt="": bridge.ask_input(prompt)
    try:
        token = uu_helper.get_valid_token_for_uu(_steam_client)
        if token:
            _set_state("uu", "success", "悠悠有品登录成功")
        else:
            _set_state("uu", "failed", "悠悠有品登录失败")
    except Exception as e:  # noqa: BLE001
        _set_state("uu", "failed", "悠悠有品登录异常：" + str(e))
    finally:
        builtins.input = original_input
        bridge.clear()


def start_login(platform):
    """启动指定平台的登录（后台线程）。"""
    if platform == "steam":
        threading.Thread(target=login_steam, daemon=True).start()
    elif platform == "buff":
        threading.Thread(target=login_buff, daemon=True).start()
    elif platform == "uu":
        threading.Thread(target=login_uu, daemon=True).start()
    else:
        raise ValueError("unknown platform: " + platform)
