"""多开实例管理：``--instance <name>`` 的数据目录隔离与初始化。

设计（与 ``utils.static.set_base_dir`` 配合）：

- ``default`` 实例 = 现状数据目录（PROJECT_ROOT 或 STEAMAUTO_BASE_DIR），零迁移。
- 具名实例 ``<name>`` = ``PROJECT_ROOT/instances/<name>/``，config/run/logs/session
  全部落在该目录下，实例间零共享。
- CLI 短命命令（status/config/login/--buff 等）在 ``cli.main`` 里 ``activate()``
  热切换 ``static`` 路径；后台服务进程由 ``daemon.spawn_background`` 继承
  ``STEAMAUTO_BASE_DIR`` 环境变量，import 时自然走对目录。
"""

import json
import os
import socket

from utils import static


def normalize(name):
    """规范化实例名；空 / default 归一到 "default"。"""
    name = (name or "").strip()
    if not name or name.lower() == "default":
        return "default"
    if any(c in name for c in "/\\:") or name in (".", ".."):
        raise ValueError("非法实例名：%s（不能含路径分隔符）" % name)
    return name


def base_dir(name):
    """返回实例的数据根目录（不创建）。"""
    name = normalize(name)
    if name == "default":
        return getattr(static, "_BASE_DIR", static.PROJECT_ROOT)
    return os.path.join(static.INSTANCES_DIR, name)


def _configured_ports():
    """收集所有实例（含 default）config 里已配置的 control.port。"""
    import re

    ports = set()
    candidates = [os.path.join(getattr(static, "_BASE_DIR", static.PROJECT_ROOT), "config", "config.json5")]
    if os.path.isdir(static.INSTANCES_DIR):
        for n in os.listdir(static.INSTANCES_DIR):
            candidates.append(os.path.join(static.INSTANCES_DIR, n, "config", "config.json5"))
    for cfg in candidates:
        try:
            with open(cfg, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        m = re.search(r'["\']?port["\']?\s*:\s*(\d+)', text)
        if m:
            ports.add(int(m.group(1)))
    return ports


def allocate_port(start=None):
    """分配一个本机可绑定的端口（从 control.DEFAULT_PORT 起递增探测）。

    同时排除「其它实例 config 里已配置但尚未运行的端口」——否则两个实例会分到
    同一个端口，一旦同时启动就冲突（只用 socket 探测会漏掉这种情况）。
    """
    from utils import control

    first = start or control.DEFAULT_PORT
    used = _configured_ports()
    for port in range(first, first + 200):
        if port in used:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("无法分配空闲端口（%s-%s 均被占用）" % (first, first + 200))


def _config_text_with_port(port):
    """默认配置模板，把 control.port 替换为分配到的端口。"""
    return static.DEFAULT_CONFIG_JSON.replace('"port": 45917', '"port": %d' % port)


def ensure_instance(name):
    """确保具名实例的目录与初始配置就绪。返回 (base_dir, created: bool)。"""
    name = normalize(name)
    bd = base_dir(name)
    if name == "default":
        return bd, False

    cfg_dir = os.path.join(bd, "config")
    cfg_path = os.path.join(cfg_dir, "config.json5")
    acct_path = os.path.join(cfg_dir, "steam_account_info.json5")

    created = False
    os.makedirs(cfg_dir, exist_ok=True)
    if not os.path.exists(cfg_path):
        port = allocate_port()
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(_config_text_with_port(port))
        created = True
    if not os.path.exists(acct_path):
        with open(acct_path, "w", encoding="utf-8") as f:
            f.write(static.DEFAULT_STEAM_ACCOUNT_JSON)
        created = True
    return bd, created


def activate(name, create=True):
    """激活实例：切换 static 路径 + 设置环境变量。返回 (name, base_dir)。"""
    name = normalize(name)
    bd = base_dir(name)
    if create and name != "default":
        ensure_instance(name)
    os.environ["STEAMAUTO_BASE_DIR"] = bd
    static.set_base_dir(bd)
    return name, bd


def current_name():
    """当前实例名：从 STEAMAUTO_BASE_DIR 反推；default 返回 "default"。"""
    env = os.environ.get("STEAMAUTO_BASE_DIR")
    if env:
        env_abs = os.path.abspath(env)
        root = static.INSTANCES_DIR
        if env_abs.startswith(root + os.sep):
            return os.path.relpath(env_abs, root)
    return "default"


def list_instances():
    """列出所有实例及其运行状态。返回 [{"name","base_dir","running","pid"}]。"""
    from utils import daemon

    entries = [("default", getattr(static, "_BASE_DIR", static.PROJECT_ROOT))]
    if os.path.isdir(static.INSTANCES_DIR):
        for n in sorted(os.listdir(static.INSTANCES_DIR)):
            d = os.path.join(static.INSTANCES_DIR, n)
            if os.path.isdir(d):
                entries.append((n, d))

    result = []
    for name, base in entries:
        state_file = os.path.join(base, "run", "steamauto.state.json")
        running, pid = False, None
        try:
            with open(state_file, encoding="utf-8") as f:
                state = json.load(f)
            pid = state.get("pid")
            running = bool(pid) and daemon.pid_alive(int(pid))
        except (OSError, ValueError, TypeError):
            pass
        result.append({"name": name, "base_dir": base, "running": running, "pid": pid})
    return result
