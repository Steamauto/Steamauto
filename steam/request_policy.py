"""Process-local cooldowns for Steam market endpoints, without credential state."""

import math
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Optional


DEFAULT_RATE_LIMIT_SECONDS = 60.0


def parse_retry_after(value: object, *, now: Optional[float] = None) -> float:
    """Accept delta seconds or an HTTP date; unknown limits get a short cooldown."""
    try:
        delay = float(value)
    except (TypeError, ValueError, OverflowError):
        try:
            date = parsedate_to_datetime(str(value))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            delay = max(0.0, date.timestamp() - (time.time() if now is None else now))
        except (TypeError, ValueError, OverflowError, OSError):
            return DEFAULT_RATE_LIMIT_SECONDS
    if not math.isfinite(delay) or delay < 0:
        return DEFAULT_RATE_LIMIT_SECONDS
    return max(1.0, delay)


class MarketCooldown:
    """Share an endpoint's 429 cooldown across tasks, without blocking a thread."""

    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self._clock = clock or time.monotonic
        self._until = 0.0
        self._lock = threading.Lock()

    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self._until - self._clock())

    def defer(self, retry_after: object = None) -> float:
        delay = parse_retry_after(retry_after)
        with self._lock:
            now = self._clock()
            self._until = max(self._until, now + delay)
            return self._until - now
