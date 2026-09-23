"""Runtime environment detection and launch policy helpers."""
from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from typing import Mapping, Optional


DEFAULT_PORT = 28472
TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


@dataclass(frozen=True)
class RuntimeProfile:
    platform: str
    requested_mode: str
    mode: str
    display_available: bool
    headless: bool
    can_open_gui: bool
    can_launch_headful_browser: bool
    open_browser: bool
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def _clean(value: Optional[str]) -> str:
    return (value or "").strip()


def parse_bool(value: Optional[str], default: Optional[bool] = None) -> Optional[bool]:
    text = _clean(value).lower()
    if not text:
        return default
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return default


def env_bool(name: str, default: Optional[bool] = None, env: Optional[Mapping[str, str]] = None) -> Optional[bool]:
    source = os.environ if env is None else env
    return parse_bool(source.get(name), default)


def _env_value(*names: str, env: Optional[Mapping[str, str]] = None) -> str:
    source = os.environ if env is None else env
    for name in names:
        value = _clean(source.get(name))
        if value:
            return value
    return ""


def has_graphical_display(
    env: Optional[Mapping[str, str]] = None,
    platform_name: Optional[str] = None,
) -> bool:
    source = os.environ if env is None else env
    platform = (platform_name or sys.platform).lower()
    if platform.startswith("win") or platform == "darwin":
        return True
    return any(_clean(source.get(name)) for name in ("DISPLAY", "WAYLAND_DISPLAY", "MIR_SOCKET"))


def get_runtime_profile(
    env: Optional[Mapping[str, str]] = None,
    platform_name: Optional[str] = None,
) -> RuntimeProfile:
    source = os.environ if env is None else env
    platform = (platform_name or sys.platform).lower()
    requested = _env_value("AETHERSWAP_MODE", "AETHERSWAP_RUNTIME", env=source).lower() or "auto"
    if requested in {"headless", "service"}:
        requested = "server"
    elif requested in {"gui", "webview"}:
        requested = "desktop"
    elif requested not in {"auto", "server", "desktop"}:
        requested = "auto"

    display_available = has_graphical_display(source, platform)
    manual_login_only = env_bool("AETHERSWAP_MANUAL_LOGIN_ONLY", False, source) is True
    explicit_headless = env_bool("AETHERSWAP_HEADLESS", None, source)
    server_only = env_bool("AETHERSWAP_SERVER_ONLY", False, source) is True

    if requested == "server" or server_only:
        mode = "server"
        reason = "server mode was requested"
    elif explicit_headless is True:
        mode = "server"
        reason = "headless mode was requested"
    elif requested == "desktop" and display_available:
        mode = "desktop"
        reason = "desktop mode was requested"
    elif requested == "desktop" and not display_available:
        mode = "server"
        reason = "desktop mode was requested but no graphical display was detected"
    elif display_available:
        mode = "desktop"
        reason = "graphical display detected"
    else:
        mode = "server"
        reason = "no graphical display detected"

    headless = mode == "server" or explicit_headless is True
    can_open_gui = mode == "desktop" and display_available and not headless
    can_launch_headful_browser = can_open_gui and not manual_login_only
    open_browser = env_bool("AETHERSWAP_OPEN_BROWSER", can_open_gui, source)

    return RuntimeProfile(
        platform=platform,
        requested_mode=requested,
        mode=mode,
        display_available=display_available,
        headless=headless,
        can_open_gui=can_open_gui,
        can_launch_headful_browser=can_launch_headful_browser,
        open_browser=bool(open_browser),
        reason=reason,
    )


def get_bind_host(profile: Optional[RuntimeProfile] = None, env: Optional[Mapping[str, str]] = None) -> str:
    host = _env_value("AETHERSWAP_HOST", env=env)
    if host:
        return host
    profile = profile or get_runtime_profile(env=env)
    return "0.0.0.0" if profile.mode == "server" else "127.0.0.1"


def get_port(env: Optional[Mapping[str, str]] = None) -> int:
    raw = _env_value("AETHERSWAP_PORT", "PORT", env=env)
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return port if 0 < port < 65536 else DEFAULT_PORT


def get_local_url(host: str, port: int) -> str:
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}"


def get_external_url_hint(host: str, port: int) -> str:
    if host in {"0.0.0.0", "::"}:
        return f"http://<server-ip>:{port}"
    return f"http://{host}:{port}"
