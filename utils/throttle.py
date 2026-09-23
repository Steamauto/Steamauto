import threading
import time
from typing import Dict
from utils.delay import jittered_sleep
class DomainThrottle:
    def __init__(self) -> None:
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()
    def wait(self, domain: str, min_interval: float = 2.0) -> None:
        with self._lock:
            now = time.time()
            last = self._last.get(domain, 0)
            gap = min_interval - (now - last)
        if gap > 0:
            jittered_sleep(gap, jitter_ratio=0.2)
        with self._lock:
            self._last[domain] = time.time()
_global_throttle = DomainThrottle()
def get_throttle() -> DomainThrottle:
    return _global_throttle
