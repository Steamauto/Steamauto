"""运行时状态与协作原语。

本模块提供四样东西，供「后台运行 + 优雅关停 + 运行时改配置 + 热登录」共用：

1. `shutdown_event`：全局关停信号。长驻循环（各插件 `exec()`）改为
   `while not shutdown_event.is_set()`，停止命令 / Ctrl+C 触发后线程能自然收尾，
   避免依赖 daemon 线程被进程强杀（那样日志不会 flush、PID 不会清理）。
2. `interruptible_sleep()`：可被**关停或唤醒**打断的 sleep。插件轮询里所有
   `time.sleep(n)` 都应改用它，否则关停要等最长一个 interval 才生效。
3. **唤醒机制**：`request_wake()` 让所有正在等待的循环立刻结束等待、提前进入下一轮。
   用于「刚补登录完，希望插件马上重试」的场景（否则要等一个完整 interval）。
   采用**版本号广播**（而非 threading.Event）：Event 会被多个等待者争抢消费，
   谁先醒谁清掉，其余循环收不到——后台的 cloud_service 轮询线程就会抢走通知。
   插件侧的写法是「只有关停才 break」：
       if not runtime.interruptible_sleep(interval) and runtime.is_shutdown_requested():
           break
   这样唤醒时循环继续（重读配置/重试登录），关停时才退出。
4. 热应用注册表：`config.set` 改完配置后，由注册的回调把新值应用到运行中的进程。

注意：本模块不得 import utils.logger / utils.static，以免与 static→logger 的
初始化顺序形成循环依赖。
"""

import threading
import time

__all__ = [
    "shutdown_event",
    "request_shutdown",
    "is_shutdown_requested",
    "clear_shutdown",
    "request_wake",
    "wake_sequence",
    "clear_wake",
    "wait_or_shutdown",
    "interruptible_sleep",
    "register_hot_applier",
    "apply_hot_config",
    "hot_keys",
    "is_hot_key",
]

#: 全局关停事件。置位后所有长驻循环应尽快退出。
shutdown_event = threading.Event()

#: 唤醒「版本号」。每次 request_wake() 递增；等待中的循环比较自己进入等待时的版本号，
#: 发现变化即提前返回。用**版本号而非 Event** 是因为唤醒是「广播」语义：
#: 用 Event 的话，多个等待者会争抢着把事件消费掉（谁先醒谁清掉），其余循环收不到通知
#: —— 实测中 cloud_service 的后台轮询线程就会把唤醒抢走，导致目标插件收不到。
_wake_lock = threading.Lock()
_wake_seq = 0

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


def request_wake():
    """请求唤醒：让所有正在等待的循环提前进入下一轮（幂等、广播语义）。

    与关停不同，唤醒是「有进展了，立刻重试」的正向信号，
    因此调用方不应据此退出循环。
    """
    global _wake_seq
    with _wake_lock:
        _wake_seq += 1


def wake_sequence() -> int:
    """当前的唤醒版本号。用于判断「我等待期间是否发生过唤醒」。"""
    with _wake_lock:
        return _wake_seq


def clear_wake():
    """把唤醒版本号归零。仅测试使用。"""
    global _wake_seq
    with _wake_lock:
        _wake_seq = 0


def wait_or_shutdown(seconds: float) -> bool:
    """等待 `seconds` 秒，或被关停/唤醒打断。

    :return: True 表示等待满时长；False 表示被关停或唤醒提前打断
             （用 `is_shutdown_requested()` 区分两者）。
    """
    if seconds is None or seconds <= 0:
        return not shutdown_event.is_set()
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return not shutdown_event.is_set()
    return _wait_any(total, 0.5)


def interruptible_sleep(seconds: float, step: float = 0.5) -> bool:
    """可被关停或唤醒打断的 sleep。

    内部分片等待，保证关停/唤醒请求能在 `step` 秒内被响应（即便秒数很大）。

    :return: True = 睡满；False = 被关停或唤醒提前打断。
             调用方应写成「只有 `is_shutdown_requested()` 才 break」，
             这样唤醒时循环继续。
    """
    if seconds is None:
        return True
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return True
    if total <= 0:
        return not shutdown_event.is_set()
    return _wait_any(total, step)


def _wait_any(total: float, step: float) -> bool:
    """分片等待；关停置位或唤醒版本号变化时提前返回 False。"""
    start_seq = wake_sequence()
    deadline = time.monotonic() + total
    while True:
        if shutdown_event.is_set():
            return False
        if wake_sequence() != start_seq:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        # 分片等待关停事件；唤醒最迟在下一个分片边界被感知（≤ step 秒）
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
