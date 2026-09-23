"""Shared request pacing and circuit breaking for authenticated BUFF clients.

The policy deliberately stores only a one-way account fingerprint.  Cookies and
other credentials must never be written to the circuit-breaker state file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterator, Mapping, Optional, Union


logger = logging.getLogger(__name__)

DEFAULT_MIN_REQUEST_INTERVAL = 2.0
DEFAULT_RATE_LIMIT_SECONDS = 300.0
DEFAULT_POLICY_STATE_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "buff_request_policy.json"
)


def _retry_fallback(value: object = DEFAULT_RATE_LIMIT_SECONDS) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_RATE_LIMIT_SECONDS
    if not math.isfinite(parsed) or parsed < 0:
        return DEFAULT_RATE_LIMIT_SECONDS
    return parsed


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


class BuffRequestError(Exception):
    """Base class for BUFF transport failures callers may handle explicitly."""


class BuffAuthExpired(BuffRequestError):
    """The authenticated BUFF session is no longer valid."""


class BuffWriteResultUnknown(BuffRequestError):
    """A non-idempotent request may have reached BUFF but has no safe result."""

    def __init__(
        self,
        message: str = "BUFF 写请求结果未知，禁止自动重试",
        *,
        method: str = "",
        url: str = "",
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.method = str(method or "").upper()
        self.url = str(url or "")
        self.status_code = int(status_code) if status_code is not None else None


class BuffRequestBlocked(BuffRequestError):
    """The account request circuit is open and no request was sent."""


class BuffVerificationRequired(BuffRequestBlocked):
    """BUFF requested a browser/security verification for this account."""


class BuffRiskControlTriggered(BuffVerificationRequired):
    """BUFF returned an HTTP response associated with risk control."""

    def __init__(self, message: str = "BUFF 风控已触发", *, status_code: int = 403):
        super().__init__(message)
        self.status_code = int(status_code)


class BuffRateLimited(BuffRequestBlocked):
    """BUFF rate-limited the account, including the remaining cooldown."""

    def __init__(self, retry_after: float, message: str = "BUFF 请求过于频繁"):
        self.retry_after = _retry_fallback(retry_after)
        self.status_code = 429
        super().__init__(f"{message}；请在 {self.retry_after:.1f} 秒后重试")


@dataclass(frozen=True)
class _CircuitState:
    reason: str
    updated_at: float
    blocked_until: Optional[float] = None
    message: str = ""
    status_code: Optional[int] = None

    @classmethod
    def from_json(cls, raw: object) -> Optional["_CircuitState"]:
        if not isinstance(raw, dict):
            return None
        reason = str(raw.get("reason", ""))
        if reason not in {"verification", "risk_control", "rate_limited"}:
            return None
        updated_at_raw = raw.get("updated_at")
        if type(updated_at_raw) not in {int, float}:
            return None
        blocked_until_raw = raw.get("blocked_until")
        if (
            blocked_until_raw is not None
            and type(blocked_until_raw) not in {int, float}
        ):
            return None
        status_code_raw = raw.get("status_code")
        if status_code_raw is not None and type(status_code_raw) is not int:
            return None
        try:
            updated_at = float(updated_at_raw)
            blocked_until = (
                float(blocked_until_raw) if blocked_until_raw is not None else None
            )
            status_code = int(status_code_raw) if status_code_raw is not None else None
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(updated_at) or updated_at <= 0:
            return None
        if blocked_until is not None and not math.isfinite(blocked_until):
            return None
        if reason == "rate_limited":
            if (
                blocked_until is None
                or blocked_until < updated_at
                or status_code != 429
            ):
                return None
        elif blocked_until is not None:
            return None
        if reason == "risk_control" and status_code not in {403, 412}:
            return None
        return cls(
            reason=reason,
            updated_at=updated_at,
            blocked_until=blocked_until,
            message=str(raw.get("message", ""))[:500],
            status_code=status_code,
        )


def account_fingerprint(
    cookies: Mapping[str, str], account_id: Optional[str] = None
) -> str:
    """Return a stable, non-reversible key used for per-account policy state."""

    if account_id:
        source = f"account:{account_id}"
    else:
        # ``remember_me`` is normally more stable than a rotating session token.
        # The fallback still keeps independent browser sessions isolated.
        for name in ("remember_me", "session", "Device-Id", "device_id"):
            value = cookies.get(name)
            if value:
                source = f"{name}:{value}"
                break
        else:
            source = "cookies:" + "\n".join(
                f"{key}={cookies[key]}" for key in sorted(cookies)
            )
    return hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()


def parse_retry_after(
    value: Optional[str],
    *,
    now: Optional[float] = None,
    default: float = DEFAULT_RATE_LIMIT_SECONDS,
) -> float:
    """Parse Retry-After seconds or an RFC-compliant HTTP date."""

    fallback = _retry_fallback(default)
    if value is None:
        return fallback
    raw = str(value).strip()
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        pass
    else:
        if not math.isfinite(seconds) or seconds < 0:
            return fallback
        return seconds
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        current = time.time() if now is None else float(now)
        remaining = parsed.timestamp() - current
        if not math.isfinite(current) or not math.isfinite(remaining):
            return fallback
        return max(0.0, remaining)
    except (TypeError, ValueError, OverflowError):
        return fallback


class BuffRequestPolicy:
    """Serialize and pace requests, and persist per-account open circuits."""

    def __init__(
        self,
        *,
        min_interval: float = DEFAULT_MIN_REQUEST_INTERVAL,
        state_path: Optional[Union[str, Path]] = DEFAULT_POLICY_STATE_PATH,
        persist: bool = True,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        parsed_interval = float(min_interval)
        self.min_interval = (
            parsed_interval
            if math.isfinite(parsed_interval) and parsed_interval >= 0
            else DEFAULT_MIN_REQUEST_INTERVAL
        )
        self.state_path = Path(state_path) if persist and state_path is not None else None
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleeper
        self._guard = threading.RLock()
        self._account_locks: dict[str, threading.RLock] = {}
        self._last_started: dict[str, float] = {}
        self._circuits: dict[str, _CircuitState] = {}
        self._state_invalid = False
        self._load()

    def _account_lock(self, account_key: str) -> threading.RLock:
        with self._guard:
            return self._account_locks.setdefault(account_key, threading.RLock())

    @contextmanager
    def request_slot(self, account_key: str) -> Iterator[None]:
        """Hold the account lock for one complete HTTP request."""

        lock = self._account_lock(account_key)
        with lock:
            self.raise_if_blocked(account_key)
            previous = self._last_started.get(account_key)
            if previous is not None:
                remaining = self.min_interval - (self._clock() - previous)
                if remaining > 0:
                    self._sleep(remaining)
            # Record immediately before yielding so request starts, including failed
            # connection attempts, can never be closer than the hard interval.
            self._last_started[account_key] = self._clock()
            yield

    def raise_if_blocked(self, account_key: str) -> None:
        with self._guard:
            if self._state_invalid:
                raise BuffVerificationRequired(
                    "BUFF 请求策略状态文件损坏或格式异常；需人工验证后才能继续请求"
                )
            state = self._circuits.get(account_key)
            if state is None:
                return
            now = self._wall_clock()
            if state.reason == "rate_limited":
                blocked_until = state.blocked_until or now
                remaining = blocked_until - now
                if remaining <= 0:
                    self._circuits.pop(account_key, None)
                    if not self._save_locked():
                        raise BuffVerificationRequired(
                            "无法持久化 BUFF 冷却状态变更；已继续阻断请求"
                        )
                    return
                raise BuffRateLimited(remaining, state.message or "BUFF 请求过于频繁")
            if state.reason == "risk_control":
                raise BuffRiskControlTriggered(
                    state.message or "BUFF 风控已触发，需要人工验证",
                    status_code=state.status_code or 403,
                )
            raise BuffVerificationRequired(
                state.message or "BUFF 需要人工完成安全验证"
            )

    def trip_verification(self, account_key: str, message: str = "") -> None:
        self._set_circuit(
            account_key,
            _CircuitState(
                reason="verification",
                updated_at=self._wall_clock(),
                message=str(message)[:500],
            ),
        )

    def trip_risk_control(
        self, account_key: str, *, status_code: int, message: str = ""
    ) -> None:
        self._set_circuit(
            account_key,
            _CircuitState(
                reason="risk_control",
                updated_at=self._wall_clock(),
                message=str(message)[:500],
                status_code=int(status_code),
            ),
        )

    def trip_rate_limit(
        self, account_key: str, retry_after: float, message: str = ""
    ) -> None:
        now = self._wall_clock()
        retry_after = _retry_fallback(retry_after)
        self._set_circuit(
            account_key,
            _CircuitState(
                reason="rate_limited",
                updated_at=now,
                blocked_until=now + retry_after,
                message=str(message)[:500],
                status_code=429,
            ),
        )

    def _set_circuit(self, account_key: str, state: _CircuitState) -> None:
        with self._guard:
            current = self._circuits.get(account_key)
            # Never shorten an existing rate-limit window due to a later response.
            if (
                current is not None
                and current.reason == state.reason == "rate_limited"
                and (current.blocked_until or 0.0) > (state.blocked_until or 0.0)
            ):
                return
            self._circuits[account_key] = state
            self._save_locked()

    def reset(self, account_key: Optional[str] = None) -> None:
        """Close one account circuit, or clear all policy state when omitted."""

        with self._guard:
            if account_key is None:
                self._circuits.clear()
                self._last_started.clear()
            else:
                self._circuits.pop(account_key, None)
                self._last_started.pop(account_key, None)
            self._state_invalid = False
            if not self._save_locked():
                raise BuffVerificationRequired(
                    "无法持久化 BUFF 请求策略重置；已继续阻断请求"
                )

    def clear(self, account_key: Optional[str] = None) -> None:
        """Clear circuit state while preserving the last-request pacing clock."""

        with self._guard:
            if account_key is None:
                self._circuits.clear()
            else:
                self._circuits.pop(account_key, None)
            # ``clear`` is only called after an explicit successful browser
            # verification, which is also the recovery path for a damaged
            # policy file.
            self._state_invalid = False
            if not self._save_locked():
                raise BuffVerificationRequired(
                    "无法持久化 BUFF 请求策略清理；已继续阻断请求"
                )

    def note_external_request(self, account_key: str) -> None:
        """Account for a browser/manual probe made outside this policy.

        Successful authentication must not allow the next API request to start
        immediately after the verification request merely because it used a
        different HTTP transport.
        """

        with self._account_lock(account_key):
            self._last_started[account_key] = self._clock()

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            if self.state_path is None:
                return
            # A leftover atomic temp means a prior policy commit did not reach
            # the canonical path. Its contents may contain a newer circuit, so
            # absence of the main file is not permission to send requests.
            if list(
                self.state_path.parent.glob(
                    f".{self.state_path.name}.*.tmp"
                )
            ):
                self._state_invalid = True
            return
        try:
            if list(
                self.state_path.parent.glob(
                    f".{self.state_path.name}.*.tmp"
                )
            ):
                self._state_invalid = True
                return
            raw = json.loads(
                self.state_path.read_text(encoding="utf-8"),
                parse_constant=_reject_non_finite_json,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
            if (
                not isinstance(raw, dict)
                or type(raw.get("version")) is not int
                or raw.get("version") != 1
                or not isinstance(raw.get("accounts"), dict)
            ):
                self._state_invalid = True
                return
            accounts = raw["accounts"]
            now = self._wall_clock()
            for account_key, value in accounts.items():
                state = _CircuitState.from_json(value)
                if state is None:
                    self._circuits.clear()
                    self._state_invalid = True
                    return
                if (
                    state.reason == "rate_limited"
                    and (state.blocked_until or now) <= now
                ):
                    continue
                self._circuits[str(account_key)] = state
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            self._circuits.clear()
            self._state_invalid = True
            logger.warning("无法读取 BUFF 请求策略状态 %s: %s", self.state_path, exc)

    def _save_locked(self) -> bool:
        if self.state_path is None:
            return True
        payload = {
            "version": 1,
            "accounts": {
                account_key: asdict(state)
                for account_key, state in self._circuits.items()
            },
        }
        temp_path: Optional[Path] = None
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{self.state_path.name}.",
                suffix=".tmp",
                dir=str(self.state_path.parent),
            )
            temp_path = Path(temp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    payload,
                    stream,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.state_path)
            for stale_path in self.state_path.parent.glob(
                f".{self.state_path.name}.*.tmp"
            ):
                try:
                    stale_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return True
        except (OSError, TypeError, ValueError) as exc:
            self._state_invalid = True
            logger.warning("无法保存 BUFF 请求策略状态 %s: %s", self.state_path, exc)
            # Keep a completed temp file as a fail-closed restart marker. If
            # even creating the temp failed, the current process still blocks.
            return False


_GLOBAL_POLICY_LOCK = threading.RLock()
_GLOBAL_POLICY: Optional[BuffRequestPolicy] = None


def get_global_policy() -> BuffRequestPolicy:
    global _GLOBAL_POLICY
    with _GLOBAL_POLICY_LOCK:
        if _GLOBAL_POLICY is None:
            _GLOBAL_POLICY = BuffRequestPolicy()
        return _GLOBAL_POLICY


def reset_global_policy(
    policy: Optional[BuffRequestPolicy] = None,
    *,
    min_interval: float = DEFAULT_MIN_REQUEST_INTERVAL,
    state_path: Optional[Union[str, Path]] = DEFAULT_POLICY_STATE_PATH,
    persist: bool = True,
    clear_state: bool = True,
) -> BuffRequestPolicy:
    """Replace the process-global policy; useful after login and in tests."""

    global _GLOBAL_POLICY
    with _GLOBAL_POLICY_LOCK:
        replacement = policy or BuffRequestPolicy(
            min_interval=min_interval, state_path=state_path, persist=persist
        )
        if clear_state:
            replacement.reset()
        _GLOBAL_POLICY = replacement
        return replacement


# More explicit aliases for callers that prefer the full component name.
get_global_request_policy = get_global_policy
reset_global_request_policy = reset_global_policy


def clear_global_policy(account_key: Optional[str] = None) -> None:
    """Clear circuit state without replacing the process-global policy object."""

    get_global_policy().clear(account_key)


__all__ = [
    "BuffAuthExpired",
    "BuffRateLimited",
    "BuffRequestBlocked",
    "BuffRequestError",
    "BuffRequestPolicy",
    "BuffRiskControlTriggered",
    "BuffVerificationRequired",
    "BuffWriteResultUnknown",
    "DEFAULT_MIN_REQUEST_INTERVAL",
    "DEFAULT_POLICY_STATE_PATH",
    "account_fingerprint",
    "clear_global_policy",
    "get_global_policy",
    "get_global_request_policy",
    "parse_retry_after",
    "reset_global_policy",
    "reset_global_request_policy",
]
