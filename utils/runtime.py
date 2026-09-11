"""运行时状态与协作原语。

本模块提供三样东西，供「后台运行 + 优雅关停 + 运行时改配置」共用：

1. `shutdown_event`：全局关停信号。长驻循环（各插件 `exec()`）改为
   `while not shutdown_event.is_set()`，停止命令 / Ctrl+C 触发后线程能自然收尾，
   避免依赖 daemon 线程被进程强杀（那样日志不会 flush、PID 不会清理）。
2. `interruptible_sleep()`：可被关停信号打断的 sleep。插件轮询里所有
   `time.sleep(n)` 都应改用它，否则关停要等最长一个 interval 才生效。
3. 热应用注册表：`config.set` 改完配置后，由注册的回调把新值应用到运行中的进程。

注意：本模块不得 import utils.logger / utils.static，以免与 static→logger 的
初始化顺序形成循环依赖。
"""

import threading
import time

__all__ = [
    "shutdown_event",
    "request_shutdown",
    "is_shutdown_requested",
    "wait_or_shutdown",
    "interruptible_sleep",
    "register_hot_applier",
    "apply_hot_config",
    "hot_keys",
    "is_hot_key",
]

#: 全局关停事件。置位后所有长驻循环应尽快退出。
shutdown_event = threading.Event()

_hot_appliers = {}
_hot_lock = threading.Lock()


def request_shutdown():
    """请求优雅关停（幂等）。"""
    shutdown_event.set()


def is_shutdown_requested() -> bool:
    return shutdown_event.is_set()


def clear_shutdown():
    """清除关停信号。仅测试使用。"""
    shutdown_event.clear()


def wait_or_shutdown(seconds: float) -> bool:
    """等待 `seconds` 秒或被关停打断。

    :return: True 表示等待满时长（可继续下一轮）；False 表示收到关停请求。
    """
    if seconds is None or seconds <= 0:
        return not shutdown_event.is_set()
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return not shutdown_event.is_set()
    return not shutdown_event.wait(seconds)


def interruptible_sleep(seconds: float, step: float = 0.5) -> bool:
    """`wait_or_shutdown` 的别名，语义上更贴近替换 `time.sleep` 的场景。

    内部分片等待，保证关停请求能在 `step` 秒内被响应（即便秒数很大）。
    """
    if seconds is None:
        return True
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return True
    if total <= 0:
        return not shutdown_event.is_set()
    deadline = time.monotonic() + total
    while True:
        if shutdown_event.is_set():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        if shutdown_event.wait(min(step, remaining)):
            return False


def register_hot_applier(key: str, fn):
    """注册「配置键 -> 热应用回调」。fn(value) 接受新值，无返回值。"""
    with _hot_lock:
        _hot_appliers[key] = fn


def is_hot_key(key: str) -> bool:
    with _hot_lock:
        return key in _hot_appliers


def hot_keys():
    with _hot_lock:
        return sorted(_hot_appliers)


def apply_hot_config(key: str, value):
    """把单个配置键的新值应用到运行中的进程。

    :return: (applied: bool, error: str) —— applied=False 表示该键不支持热改。
    """
    with _hot_lock:
        fn = _hot_appliers.get(key)
    if fn is None:
        return False, "该配置项不支持运行时热改，需重启程序后生效"
    try:
        fn(value)
        return True, ""
    except Exception as e:  # noqa: BLE001 - 热应用失败不应影响进程
        return False, "应用配置失败: %s" % (e,)
