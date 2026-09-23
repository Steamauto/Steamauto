import re
import time
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STEAM_HISTORY_DATE_FMT = "%b %d %Y %H"
STEAM_COOLDOWN_FMT = "%b %d, %Y %H:%M:%S"

_STEAM_HISTORY_RE = re.compile(
    r"^\s*(?P<stamp>[A-Za-z]{3}\s+\d{1,2}\s+\d{4}\s+\d{1,2})"
    r"(?:\s*:\s*(?P<offset>[+-]\d{1,4}))?\s*$"
)


def format_utc_offset(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "-"
    absolute = abs(minutes)
    return f"UTC{sign}{absolute // 60:02d}:{absolute % 60:02d}"


def resolve_configured_timezone(
    system_config: Optional[dict],
) -> Tuple[Optional[tzinfo], str]:
    """Resolve an application clock without relying on the host/container TZ."""

    system_config = system_config or {}
    timezone_name = str(system_config.get("timezone") or "").strip()[:128]
    if timezone_name:
        try:
            return ZoneInfo(timezone_name), timezone_name
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass

    raw_offset = system_config.get("timezone_offset_minutes")
    if raw_offset is not None and not isinstance(raw_offset, bool):
        try:
            numeric_offset = float(raw_offset)
            if numeric_offset.is_integer() and -840 <= numeric_offset <= 840:
                offset_minutes = int(numeric_offset)
                return (
                    timezone(timedelta(minutes=offset_minutes)),
                    format_utc_offset(offset_minutes),
                )
        except (TypeError, ValueError, OverflowError):
            pass
    return None, "server-local"


def now_in_configured_timezone(configured_timezone: Optional[tzinfo]) -> datetime:
    if configured_timezone is None:
        return datetime.now().astimezone()
    return datetime.now(configured_timezone)


def timestamp_in_configured_timezone(
    timestamp: float,
    configured_timezone: Optional[tzinfo],
) -> datetime:
    if configured_timezone is None:
        return datetime.fromtimestamp(timestamp).astimezone()
    return datetime.fromtimestamp(timestamp, configured_timezone)


def _parse_utc_offset(raw: Optional[str]) -> Optional[timezone]:
    if not raw:
        return timezone.utc
    sign = 1 if raw[0] == "+" else -1
    digits = raw[1:]
    try:
        if len(digits) <= 2:
            hours = int(digits)
            minutes = 0
        elif len(digits) in {3, 4}:
            hours = int(digits[:-2])
            minutes = int(digits[-2:])
        else:
            return None
    except ValueError:
        return None
    if hours > 14 or minutes > 59 or (hours == 14 and minutes):
        return None
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def parse_steam_history_date(date_str: str) -> Optional[datetime]:
    try:
        match = _STEAM_HISTORY_RE.fullmatch(str(date_str))
        if match is None:
            return None
        parsed_timezone = _parse_utc_offset(match.group("offset"))
        if parsed_timezone is None:
            return None
        parsed = datetime.strptime(match.group("stamp"), STEAM_HISTORY_DATE_FMT)
        return parsed.replace(tzinfo=parsed_timezone).astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


def parse_steam_cooldown(raw: str) -> Optional[datetime]:
    try:
        normalized = raw.replace(" (", " ").replace(")", "").strip()
        normalized = re.sub(r"\s+GMT$", "", normalized, flags=re.IGNORECASE)
        return datetime.strptime(
            normalized,
            STEAM_COOLDOWN_FMT,
        ).replace(tzinfo=timezone.utc)
    except (AttributeError, ValueError, TypeError):
        return None


def cutoff_days_ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def utc_timestamp() -> float:
    return time.time()
