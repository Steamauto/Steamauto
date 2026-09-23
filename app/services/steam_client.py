import hashlib
import threading
import time
from typing import Any, Dict, List, Optional, Union
from app.config_loader import get_steam_credentials, load_app_config_validated
from steam.client import (
    SteamHistoryError,
    fetch_history as _fetch_history,
    market_hash_name_from_listing_url as _market_hash_name_from_listing_url,
)
from steam.request_policy import MarketCooldown
steam_timeout = 15
steam_retry_attempts = 2
_history_cache: Dict[tuple, tuple] = {}
_history_cache_ttl = 300
_history_cache_max = 200
_history_failure_ttl = 30
_history_lock = threading.Lock()
_history_cooldown = MarketCooldown()
class SteamClient:
    def __init__(
        self,
        timeout_sec: int = steam_timeout,
        cache_ttl: int = _history_cache_ttl,
        cache_max: int = _history_cache_max,
    ) -> None:
        self._timeout = timeout_sec
        self._cache_ttl = cache_ttl
        self._cache_max = cache_max
    def fetch_history(
        self,
        market_hash_name: str,
        app_id: int = 730,
        return_currency: bool = False,
    ) -> Union[Optional[List], Optional[Dict]]:
        cred = get_steam_credentials()
        cookies = (cred.get("cookies") or "").strip() or None
        identity = f"{cred.get('steam_id') or ''}\0{cookies or ''}"
        account_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        key = (account_key, market_hash_name, app_id, return_currency)
        # Serialize cache misses so concurrent buy/sell tasks cannot duplicate a
        # failed request or race past a newly established endpoint cooldown.
        with _history_lock:
            now = time.monotonic()
            if key in _history_cache:
                data, stored_at = _history_cache[key]
                ttl = self._cache_ttl if data is not None else _history_failure_ttl
                if now - stored_at < ttl:
                    return data
                del _history_cache[key]
            remaining = _history_cooldown.remaining()
            if remaining > 0:
                from app.state import log
                log(f"[SteamClient] 历史接口冷却中，约 {remaining:.0f}s 后可重试，本次未发送请求", "debug", category="steam")
                return None
            result = self._fetch_history_impl(market_hash_name, app_id, return_currency, cookies)
            if result is None and _history_cooldown.remaining() > 0:
                return None
            if len(_history_cache) >= max(1, self._cache_max):
                oldest = min(_history_cache.items(), key=lambda x: x[1][1])
                del _history_cache[oldest[0]]
            _history_cache[key] = (result, time.monotonic())
            return result
    def _fetch_history_impl(
        self,
        market_hash_name: str,
        app_id: int,
        return_currency: bool,
        cookies: Optional[str],
    ) -> Union[Optional[List], Optional[Dict]]:
        cfg = load_app_config_validated()
        from utils.proxy_manager import get_proxy_manager
        pm = get_proxy_manager()
        for attempt in range(steam_retry_attempts):
            failed = (attempt > 0)
            proxies = pm.get_proxies_for_request(failed=failed)
            if proxies is None and not pm.is_proxy_enabled():
                proxy_url = cfg.get("steam", {}).get("proxy")
                proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
            try:
                return _fetch_history(
                    market_hash_name,
                    app_id=app_id,
                    timeout=self._timeout,
                    return_currency=return_currency,
                    cookies=cookies,
                    proxies=proxies,
                    raise_on_error=True,
                )
            except SteamHistoryError as exc:
                from app.state import log
                log(f"[SteamClient] 历史数据请求失败 (attempt={attempt+1}/{steam_retry_attempts}): {exc}", "warn", category="steam")
                if exc.status_code == 429:
                    remaining = _history_cooldown.defer(exc.retry_after)
                    log(f"[SteamClient] 历史接口 HTTP 429，暂停请求约 {remaining:.0f}s，不切换代理重试", "warn", category="steam")
                    return None
                if not exc.retryable:
                    return None
            if attempt < steam_retry_attempts - 1:
                from utils.delay import jittered_sleep
                jittered_sleep(1.0)
        return None
    @staticmethod
    def market_hash_name_from_listing_url(url: str) -> Optional[str]:
        return _market_hash_name_from_listing_url(url)
def create_steam_client(config: Optional[dict] = None) -> SteamClient:
    timeout = 15
    if config:
        timeout = int(config.get("steam", {}).get("timeout", steam_timeout))
    return SteamClient(timeout_sec=timeout)
