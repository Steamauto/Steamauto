import logging
import time
from functools import wraps
from typing import Callable, Tuple, TypeVar
from utils.delay import jittered_sleep
F = TypeVar("F", bound=Callable)
logger = logging.getLogger(__name__)
def with_retry(
    max_attempts: int = 3,
    backoff_base: float = 1.0,
    delay_after_failure_seconds: float = 5.0,
    fatal_exceptions: Tuple[type, ...] = (),
    jitter_ratio: float = 0.3,
) -> Callable[[F], F]:
    def decorator(f: F) -> F:
        @wraps(f)
        def inner(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return f(*args, **kwargs)
                except fatal_exceptions:
                    raise
                except Exception as e:
                    last_exc = e
                    if attempt + 1 >= max_attempts:
                        logger.warning(
                            "%s failed after %d attempts: %s",
                            f.__name__, max_attempts, e,
                        )
                        raise last_exc
                    wait = max(delay_after_failure_seconds, backoff_base * (2 ** attempt))
                    logger.info(
                        "%s attempt %d/%d failed (%s), retrying in %.1fs",
                        f.__name__, attempt + 1, max_attempts, e, wait,
                    )
                    jittered_sleep(wait, jitter_ratio=jitter_ratio)
            raise last_exc
        return inner
    return decorator
