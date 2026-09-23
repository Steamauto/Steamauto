import copy
import threading
import time as _time
from app.config_schema import DEFAULTS, _validate_ranges, merge, validate_and_fill
from config import (
    get_app_config_path,
    get_buff,
    get_steam,
    load_app_config,
    save_app_config,
    update_buff_credentials,
    update_steam_credentials,
)
_config_cache: dict = {}
_config_cache_ts: float = 0.0
_config_cache_revision = None
_CONFIG_CACHE_TTL = 5.0  
_CONFIG_SNAPSHOT_RETRIES = 3
_config_cache_lock = threading.Lock()
_config_update_lock = threading.RLock()
def _config_file_revision():
    try:
        stat = get_app_config_path().stat()
        return stat.st_mtime_ns, stat.st_size, stat.st_ino
    except OSError:
        return None
def _load_stable_app_config():
    raw = {}
    revision = None
    for _ in range(_CONFIG_SNAPSHOT_RETRIES):
        before = _config_file_revision()
        raw = load_app_config()
        revision = _config_file_revision()
        if before == revision:
            return raw, revision, True
    return raw, revision, False
def _invalidate_config_cache() -> None:
    global _config_cache, _config_cache_ts, _config_cache_revision
    with _config_cache_lock:
        _config_cache = {}
        _config_cache_ts = 0.0
        _config_cache_revision = None
def get_steam_credentials() -> dict:
    return get_steam()
def get_buff_credentials() -> dict:
    return get_buff()
def update_steam_creds(cookies: str, session_id: str, steam_id: str = None) -> None:
    update_steam_credentials(cookies, session_id, steam_id)
def update_buff_creds(
    cookies: str,
    user_agent: str = None,
    source: str = None,
) -> None:
    update_buff_credentials(cookies, user_agent=user_agent, source=source)
def get_buff_credentials_generation() -> int:
    """Return the credential revision used by long-lived BUFF clients."""
    try:
        return int((get_buff_credentials() or {}).get("generation", 0))
    except (TypeError, ValueError):
        return 0
def load_app_config_validated() -> dict:
    global _config_cache, _config_cache_ts, _config_cache_revision
    now = _time.monotonic()
    with _config_cache_lock:
        revision = _config_file_revision()
        if (
            _config_cache
            and revision == _config_cache_revision
            and (now - _config_cache_ts) < _CONFIG_CACHE_TTL
        ):
            return copy.deepcopy(_config_cache)
        raw, revision, stable = _load_stable_app_config()
        result = _validate_ranges(validate_and_fill(merge(DEFAULTS, raw)))
        if stable:
            _config_cache = result
            _config_cache_ts = now
            _config_cache_revision = revision
        else:
            # Never associate content from one file generation with the
            # revision of another. The next call will retry the disk read.
            _config_cache = {}
            _config_cache_ts = 0.0
            _config_cache_revision = None
        return copy.deepcopy(result)
def save_app_config_validated(data: dict) -> None:
    with _config_update_lock:
        filled = _validate_ranges(validate_and_fill(merge(DEFAULTS, data)))
        save_app_config(filled)
        _invalidate_config_cache()


def update_app_config_validated(patch: dict) -> dict:
    """Atomically merge a partial update into the latest app configuration."""
    with _config_update_lock:
        current = load_app_config_validated()
        updated = merge(current, patch if isinstance(patch, dict) else {})
        save_app_config_validated(updated)
        return load_app_config_validated()
