import threading
import time
from typing import Callable, Optional
import requests
FAILURE_THRESHOLD: int = 3
PING_FAILURE_THRESHOLD: int = 3
PING_TIMEOUT: float = 5.0
RECONNECT_POLL_INTERVAL: int = 30
PING_URLS: list = [
    "https://www.baidu.com",
    "http://www.baidu.com",
]
def _ping_baidu(timeout: float = PING_TIMEOUT) -> bool:
    session = requests.Session()
    session.max_redirects = 3
    for url in PING_URLS:
        try:
            resp = session.get(url, timeout=timeout, allow_redirects=True)
            if resp.status_code < 600:
                return True
        except Exception:
            continue
    return False
class NetworkChecker:
    def __init__(
        self,
        failure_threshold: int = FAILURE_THRESHOLD,
        ping_failure_threshold: int = PING_FAILURE_THRESHOLD,
        ping_timeout: float = PING_TIMEOUT,
        reconnect_poll_interval: int = RECONNECT_POLL_INTERVAL,
    ) -> None:
        self._lock = threading.Lock()
        self._failure_count: int = 0
        self._is_offline: bool = False
        self._failure_threshold = failure_threshold
        self._ping_failure_threshold = ping_failure_threshold
        self._ping_timeout = ping_timeout
        self._reconnect_poll_interval = reconnect_poll_interval
    def report_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._is_offline = False
    def report_failure(
        self,
        log_fn: Optional[Callable[[str, str], None]] = None,
    ) -> bool:
        with self._lock:
            if self._is_offline:
                return True
            self._failure_count += 1
            count = self._failure_count
        if count < self._failure_threshold:
            return False
        self._log(log_fn, f"请求已连续失败 {count} 次，正在 ping 百度确认网络状态…", "warn")
        ping_failures = 0
        for i in range(self._ping_failure_threshold):
            ok = _ping_baidu(self._ping_timeout)
            if ok:
                self._log(log_fn, f"百度 ping 成功（第 {i+1} 次），网络正常，重置失败计数", "info")
                with self._lock:
                    self._failure_count = 0
                return False
            ping_failures += 1
            self._log(log_fn, f"百度 ping 失败（{ping_failures}/{self._ping_failure_threshold}）", "warn")
            if i < self._ping_failure_threshold - 1:
                time.sleep(2)
        with self._lock:
            self._is_offline = True
            self._failure_count = 0
        self._log(log_fn, "已确认断网，进入断网等待模式", "error")
        return True
    def wait_until_online(
        self,
        is_stop_fn: Optional[Callable[[], bool]] = None,
        log_fn: Optional[Callable[[str, str], None]] = None,
    ) -> bool:
        interval = self._reconnect_poll_interval
        self._log(log_fn, f"断网等待模式：每 {interval} 秒检测一次网络…", "warn")
        while True:
            if is_stop_fn and is_stop_fn():
                self._log(log_fn, "断网等待期间检测到停止请求，退出等待", "warn")
                return False
            ok = _ping_baidu(self._ping_timeout)
            if ok:
                with self._lock:
                    self._is_offline = False
                    self._failure_count = 0
                self._log(log_fn, "网络已恢复，继续正常运行", "info")
                return True
            self._log(log_fn, f"仍处于断网状态，{interval} 秒后再次检测…", "warn")
            for _ in range(interval):
                if is_stop_fn and is_stop_fn():
                    self._log(log_fn, "断网等待期间检测到停止请求，退出等待", "warn")
                    return False
                time.sleep(1)
    @property
    def is_offline(self) -> bool:
        with self._lock:
            return self._is_offline
    @staticmethod
    def _log(
        log_fn: Optional[Callable[[str, str], None]],
        msg: str,
        level: str = "info",
    ) -> None:
        if log_fn is not None:
            try:
                log_fn(msg, level)
            except Exception:
                pass
_checker_instance: Optional[NetworkChecker] = None
_checker_lock = threading.Lock()
def get_network_checker() -> NetworkChecker:
    """返回全局 NetworkChecker 单例。"""
    global _checker_instance
    with _checker_lock:
        if _checker_instance is None:
            _checker_instance = NetworkChecker()
        return _checker_instance
