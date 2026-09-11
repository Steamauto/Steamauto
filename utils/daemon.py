"""后台运行支撑：状态文件、PID 存活判定、启动/停止/状态查询（D2.A）。

跨平台策略：
- **Windows**：用 `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` 让子进程脱离控制台；
  判定进程存活用 `OpenProcess` + `GetExitCodeProcess`（**不能用** `os.kill(pid, 0)`，
  Windows 上 os.kill 会真的去 TerminateProcess）。
- **Linux/macOS**：`start_new_session=True` 创建新会话（等价 setsid）。

运行时会写两个文件（都在 `run/` 目录）：
- `steamauto.pid`      纯数字 PID，便于外部工具与本文件互操作
- `steamauto.state.json` 结构化状态：pid / port / 版本 / 启动时间 / 日志路径 / 模式
"""

import datetime
import json
import os
import signal
import subprocess
import sys
import time

from utils import control, static

# 本模块会被短命的 CLI 进程导入，因此**不导入 utils.logger**
# （logger 在 import 时会创建日志文件，会让 status/config 之类命令白白留下日志）。
# 对外输出统一走 print。
_out = print

# 等待子进程写出 PID 文件的上限
STARTUP_TIMEOUT = 25.0
# 停止时等待进程优雅退出的上限
STOP_TIMEOUT = 25.0


# ---------------------------------------------------------------- 状态文件

def read_state() -> dict:
    """读取运行时状态文件，不存在或损坏时返回 {}。"""
    try:
        with open(static.STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(**fields) -> dict:
    """合并写入运行时状态文件。"""
    os.makedirs(static.RUN_FOLDER, exist_ok=True)
    data = read_state()
    data.update(fields)
    with open(static.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def clear_state():
    """删除状态文件与 PID 文件。"""
    for path in (static.STATE_FILE, static.PID_FILE):
        try:
            os.remove(path)
        except OSError:
            pass


def read_pid():
    """读取 PID：优先状态文件，回退纯 PID 文件。返回 int 或 None。"""
    state = read_state()
    pid = state.get("pid")
    if isinstance(pid, int):
        return pid
    try:
        with open(static.PID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- 进程存活

def pid_alive(pid) -> bool:
    """判定 PID 是否对应一个存活进程（不发送任何会终止进程的信号）。"""
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但无权限发信号
    except OSError:
        return False


def _pid_alive_windows(pid) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def is_running():
    """返回 (running: bool, state: dict)。

    以「PID 存活」为准；若进程存活但控制通道不可达，会在 state 里补
    `control_ok=False` 供调用方提示（例如老版本进程或控制通道被关闭）。
    """
    state = read_state()
    pid = read_pid()
    if not pid or not pid_alive(pid):
        return False, state
    state = dict(state)
    state["pid"] = pid
    port = state.get("port")
    if port:
        ok, _ = control.ping(port)
        state["control_ok"] = bool(ok)
    else:
        state["control_ok"] = False
    return True, state


# ---------------------------------------------------------------- 日志路径

def console_log_path() -> str:
    """后台进程 stdout/stderr 的落盘路径（D4②）。"""
    os.makedirs(static.LOGS_FOLDER, exist_ok=True)
    name = "console-%s.log" % datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    return os.path.join(static.LOGS_FOLDER, name)


def latest_log_file(include_console=True):
    """返回最近修改的日志文件路径（None 表示还没有日志）。"""
    folder = static.LOGS_FOLDER
    if not os.path.isdir(folder):
        return None
    candidates = []
    for name in os.listdir(folder):
        if not name.endswith(".log"):
            continue
        if not include_console and name.startswith("console-"):
            continue
        path = os.path.join(folder, name)
        try:
            candidates.append((os.path.getmtime(path), path))
        except OSError:
            continue
    if not candidates:
        return None
    return max(candidates)[1]


def tail(path, lines=50):
    """读取文件末尾若干行。"""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.readlines()
    except OSError:
        return []
    return [line.rstrip("\r\n") for line in data[-max(1, int(lines)):]]


def follow(path, poll=0.5, out=None):
    """跟随文件新增内容输出（Ctrl+C 结束）。"""
    out = out or sys.stdout
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)
        try:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(poll)
                    continue
                out.write(line)
                out.flush()
        except KeyboardInterrupt:
            return


# ---------------------------------------------------------------- 启动 / 停止

def _run_command():
    """构造「以 run 模式启动本程序」的命令行。

    注意用 PROJECT_ROOT（代码位置）而不是 _BASE_DIR（数据目录，可能被
    STEAMAUTO_BASE_DIR 覆盖），否则数据目录与代码目录分离时会找不到脚本。
    """
    if hasattr(sys, "_MEIPASS"):
        return [sys.executable, "run"]
    script = os.path.join(static.PROJECT_ROOT, "Steamauto.py")
    return [sys.executable, script, "run"]


def _popen_detached(cmd, out_handle, env):
    kwargs = {
        "cwd": static.PROJECT_ROOT,
        "stdin": subprocess.DEVNULL,
        "stdout": out_handle,
        "stderr": out_handle,
        "env": env,
    }
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def spawn_background(port=None, extra_args=None, control_enable=True):
    """以脱离终端的方式启动后台进程。

    :return: (ok: bool, message: str)
    """
    running, state = is_running()
    if running:
        return False, "Steamauto 已在运行（PID %s）" % state.get("pid")

    clear_state()  # 清掉上次残留，避免误判
    console_log = console_log_path()
    env = os.environ.copy()
    # 后台无交互终端：复用既有钩子，跳过 pause 与交互式登录
    env["STEAMAUTO_NO_PAUSE"] = "1"
    env["STEAMAUTO_DAEMON"] = "1"
    # 让子进程在状态文件里记录自己的控制台日志路径
    env["STEAMAUTO_CONSOLE_LOG"] = console_log
    if port:
        env["STEAMAUTO_CONTROL_PORT"] = str(port)
    cmd = _run_command() + list(extra_args or [])

    with open(console_log, "ab") as out:
        try:
            proc = _popen_detached(cmd, out, env)
        except OSError as e:
            return False, "启动失败：%s" % (e,)

    # 等待子进程写状态文件（登录可能较慢，但 PID 文件在启动初期就会写）
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if not pid_alive(proc.pid):
            break
        if read_pid() == proc.pid:
            _out("Steamauto 已在后台启动（PID %s）" % proc.pid)
            _out("  日志文件：%s" % (read_state().get("log_file") or latest_log_file() or "启动中…"))
            _out("  控制台输出：%s" % console_log)
            if control_enable:
                _out("  可用命令：python Steamauto.py status / stop / config list")
            return True, ""
        time.sleep(0.25)

    # 启动失败：把控制台输出尾部回显给用户，便于定位
    if not pid_alive(proc.pid):
        lines = tail(console_log, 20)
        detail = "\n".join(lines) if lines else "（无输出）"
        return False, "后台进程启动后立即退出，输出如下：\n%s" % detail
    return False, "后台进程已启动（PID %s）但未写出状态文件，请检查 %s" % (proc.pid, console_log)


def stop_running(timeout=STOP_TIMEOUT, force_hint=True):
    """优雅停止运行中的进程。返回 (ok: bool, message: str)。"""
    running, state = is_running()
    if not running:
        clear_state()
        return True, "Steamauto 未在运行"

    pid = state.get("pid")
    port = state.get("port")

    if port and state.get("control_ok"):
        ok, resp = control.request("shutdown", port=port)
        if not ok:
            return False, "发送停止指令失败：%s" % resp
    else:
        # 控制通道不可用：退化为发送信号（非优雅，日志可能未 flush）
        if force_hint:
            _out("控制通道不可用，退化为发送终止信号（可能不优雅）")
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True, check=False)
            else:
                os.kill(pid, signal.SIGTERM)
        except OSError as e:
            return False, "发送终止信号失败：%s" % (e,)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            clear_state()
            return True, "Steamauto 已停止（PID %s）" % pid
        time.sleep(0.25)
    return False, "等待进程 %s 退出超时（%s 秒）。可用 --force 强制结束。" % (pid, int(timeout))


def kill_running():
    """强制结束进程（不优雅）。返回 (ok, message)。"""
    running, state = is_running()
    if not running:
        clear_state()
        return True, "Steamauto 未在运行"
    pid = state.get("pid")
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError as e:
        return False, "强制结束失败：%s" % (e,)
    time.sleep(0.5)
    if pid_alive(pid):
        return False, "强制结束后进程 %s 仍然存活" % pid
    clear_state()
    return True, "已强制结束 Steamauto（PID %s）" % pid


def _fmt_duration(seconds):
    seconds = int(max(0, seconds))
    parts = []
    for unit, size in (("天", 86400), ("小时", 3600), ("分钟", 60)):
        if seconds >= size:
            parts.append("%d%s" % (seconds // size, unit))
            seconds %= size
    parts.append("%d秒" % seconds)
    return "".join(parts)


def describe_status():
    """生成状态描述（多行文本）与结构化数据。"""
    running, state = is_running()
    if not running:
        return [
            "状态：未运行",
            "配置目录：%s" % static.CONFIG_FOLDER,
            "日志目录：%s" % static.LOGS_FOLDER,
        ], {"running": False}

    pid = state.get("pid")
    lines = ["状态：运行中（PID %s）" % pid]
    started = state.get("started_at")
    if started:
        try:
            lines.append("启动时间：%s（已运行 %s）" % (
                datetime.datetime.fromtimestamp(float(started)).strftime("%Y-%m-%d %H:%M:%S"),
                _fmt_duration(time.time() - float(started)),
            ))
        except (TypeError, ValueError):
            pass
    if state.get("version"):
        lines.append("版本：%s" % state["version"])
    lines.append("模式：%s" % ("后台运行" if state.get("mode") == "daemon" else "前台运行"))
    if state.get("control_ok"):
        lines.append("控制通道：127.0.0.1:%s（可用）" % state.get("port"))
    elif state.get("port"):
        lines.append("控制通道：127.0.0.1:%s（不可达，stop/config 等命令将退化为信号方式）" % state.get("port"))
    else:
        lines.append("控制通道：未启用")
    if state.get("log_file"):
        lines.append("日志文件：%s" % state["log_file"])
    if state.get("console_log"):
        lines.append("控制台输出：%s" % state["console_log"])

    data = dict(state)
    data["running"] = True
    if state.get("control_ok") and state.get("port"):
        ok, resp = control.request("ping", port=state["port"])
        if ok:
            data["ping"] = resp
            if resp.get("plugins") is not None:
                lines.append("已启用插件：%s" % (", ".join(resp["plugins"]) or "（无）"))
            if resp.get("uptime") is not None:
                lines.append("进程运行时长：%s" % _fmt_duration(resp["uptime"]))
    return lines, data
