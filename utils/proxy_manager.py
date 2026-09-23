"""
代理池管理器 — 供 pipeline / steam 请求模块调用。
策略说明:
  1 = 本机优先：本机请求失败/超时后切换代理重试
  2 = 完全走代理池
  3 = 关闭代理（只走本机）
"""
import random
import threading
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
def _load_proxy_pool_cfg() -> dict:
    try:
        from app.config_loader import load_app_config_validated
        cfg = load_app_config_validated()
        return cfg.get("proxy_pool", {})
    except Exception:
        return {}
def _build_proxy_url(p: dict) -> str:
    host = p.get("host", "")
    port = p.get("port", 0)
    user = p.get("username", "")
    pwd = p.get("password", "")
    if user and pwd:
        return f"http://{user}:{pwd}@{host}:{port}/"
    return f"http://{host}:{port}/"
def _pm_log(msg: str) -> None:
    try:
        from app.state import log as app_log
        app_log(msg, "debug", category="proxy")
    except Exception:
        pass
def test_one_proxy(proxy_cfg: dict, test_url: str, timeout: int) -> dict:
    proxy_url = _build_proxy_url(proxy_cfg)
    proxies = {"http": proxy_url, "https": proxy_url}
    result = {
        "host": proxy_cfg.get("host", ""),
        "port": proxy_cfg.get("port", 0),
        "status": "failed",
        "ip_detected": None,
        "latency_ms": 0,
        "error": None,
    }
    start = time.time()
    try:
        resp = requests.get(test_url, proxies=proxies, timeout=timeout)
        latency = (time.time() - start) * 1000
        if resp.status_code == 200:
            result["status"] = "ok"
            result["ip_detected"] = resp.text.strip()
            result["latency_ms"] = round(latency, 1)
        else:
            result["error"] = f"HTTP {resp.status_code}"
    except requests.exceptions.ProxyError:
        result["error"] = "代理验证失败或节点拒绝连接"
    except requests.exceptions.Timeout:
        result["error"] = "请求超时"
    except Exception as e:
        result["error"] = str(e)
    return result
class ProxyManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._proxy_configs: list = []
        self._proxy_weights: list = []
        self._proxies: list = []
        self._warming_up: bool = False
        self._last_disabled_proxy_log_key = None
        self._last_disabled_proxy_log_at = 0.0
        self._reload()
        _pm_log(
            f"[ProxyManager] 初始化完成: "
            f"代理数={len(self._proxies)} "
            f"已启用={self.is_proxy_enabled()} "
            f"策略={self.get_strategy()}"
        )
    def _reload(self):
        cfg = _load_proxy_pool_cfg()
        raw = cfg.get("proxies", [])
        # A configured node is eligible before the asynchronous warmup has
        # completed. Warmup will promote healthy nodes or set failed ones to 0.
        self._proxies = [{"config": p, "score": 1} for p in raw if p.get("host")]
        self._cached_strategy = int(cfg.get("strategy", 1))
        self._cached_enabled = bool(cfg.get("enabled", False))
        self._sync_cycle()
    def _sync_cycle(self):
        self._proxies.sort(key=lambda x: x["score"], reverse=True)
        # 按评分构建权重列表，score=0的节点权重为0，永远不被选中
        configs = [p["config"] for p in self._proxies]
        weights = [max(0, p["score"]) for p in self._proxies]
        self._proxy_configs = configs
        self._proxy_weights = weights
    def reload(self):
        with self._lock:
            self._reload()
        _pm_log(
            f"[ProxyManager] reload 完成: "
            f"代理数={len(self._proxies)} "
            f"已启用={self.is_proxy_enabled()} "
            f"策略={self.get_strategy()}"
        )
        if self.is_proxy_enabled() and self._proxies:
            threading.Thread(target=self.warmup, daemon=True).start()
        else:
            _pm_log("[ProxyManager] 代理池未启用或无可用配置，跳过预热")
    def warmup(self):
        with self._lock:
            if getattr(self, "_warming_up", False):
                return
            enabled = self.is_proxy_enabled()
            strategy = self.get_strategy()
            proxy_count = len(self._proxies)
            if not enabled:
                _pm_log(
                    f"[ProxyManager] 代理池未启用(enabled={enabled}, strategy={strategy}, 代理数={proxy_count})，跳过预热"
                )
                return
            self._warming_up = True
            proxies_snapshot = list(self._proxies)
        if not proxies_snapshot:
            with self._lock:
                self._warming_up = False
            return
        _pm_log(f"[ProxyManager] 开始预热和检测代理池，共 {len(proxies_snapshot)} 个代理...")
        cfg = _load_proxy_pool_cfg()
        test_url = cfg.get("test_url", "https://ipv4.webshare.io/")
        timeout = int(cfg.get("timeout_seconds", 10))
        results_map = {}
        max_workers = min(len(proxies_snapshot), 20)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_proxy = {
                executor.submit(test_one_proxy, p["config"], test_url, timeout): p["config"]
                for p in proxies_snapshot
            }
            for future in as_completed(future_to_proxy):
                config = future_to_proxy[future]
                key = (config.get("host"), config.get("port"))
                try:
                    res = future.result()
                    if res["status"] == "ok":
                        score = max(1, 100000 - res["latency_ms"])
                    else:
                        score = 0
                except Exception:
                    score = 0
                results_map[key] = score
        with self._lock:
            for p in self._proxies:
                key = (p["config"].get("host"), p["config"].get("port"))
                if key in results_map:
                    p["score"] = results_map[key]
            self._sync_cycle()
            self._warming_up = False
        _pm_log(f"[ProxyManager] 预热和检测代理池完成，已按评分降序重载代理循环队列。")
    def get_strategy(self) -> int:
        return self._cached_strategy
    def is_proxy_enabled(self) -> bool:
        return self._cached_enabled and self._cached_strategy != 3
    def get_next_proxy_dict(self):
        with self._lock:
            configs = self._proxy_configs
            weights = self._proxy_weights
            if not configs or all(w == 0 for w in weights):
                _pm_log("[ProxyManager] get_next_proxy_dict → 代理池为空或全部失败，返回 None")
                return None
            # 按评分加权随机选：延迟低（高分）的代理被选中概率更高
            p = random.choices(configs, weights=weights, k=1)[0]
            url = _build_proxy_url(p)
            result = {"http": url, "https": url}
            _pm_log(f"[ProxyManager] 加权随机选到代理: {p.get('host')}:{p.get('port')}")
            return result
    def should_use_proxy_on_failure(self) -> bool:
        """策略1：请求失败后才用代理。"""
        return self.is_proxy_enabled() and self.get_strategy() == 1
    def should_always_use_proxy(self) -> bool:
        """策略2：一直走代理。"""
        return self.is_proxy_enabled() and self.get_strategy() == 2
    def get_proxies_for_request(self, failed: bool = False) -> Optional[dict]:
        """
        根据策略返回适当的 proxies dict：
          - 策略3 / 未启用 → None（走本机）
          - 策略2 → 始终返回代理
          - 策略1 → 仅在 failed=True 时返回代理
        """
        enabled = self.is_proxy_enabled()
        strategy = self.get_strategy()
        proxy_count = len(self._proxies)
        if not enabled:
            now = time.time()
            key = (enabled, strategy, proxy_count)
            if key != self._last_disabled_proxy_log_key or (now - self._last_disabled_proxy_log_at) >= 60:
                _pm_log(
                    f"[ProxyManager] get_proxies_for_request: "
                    f"代理未启用(enabled={enabled}, strategy={strategy}, 代理数={proxy_count}) → 走本机"
                )
                self._last_disabled_proxy_log_key = key
                self._last_disabled_proxy_log_at = now
            return None
        if strategy == 3:
            _pm_log(f"[ProxyManager] get_proxies_for_request(failed={failed}): 策略3=关闭 → 走本机")
            return None
        if strategy == 2:
            proxy = self.get_next_proxy_dict()
            _pm_log(
                f"[ProxyManager] get_proxies_for_request(failed={failed}): "
                f"策略2=始终代理 → {proxy.get('http') if proxy else 'None(池空)'}"
            )
            return proxy
        if strategy == 1:
            if failed:
                proxy = self.get_next_proxy_dict()
                _pm_log(
                    f"[ProxyManager] get_proxies_for_request(failed=True): "
                    f"策略1=失败切代理 → {proxy.get('http') if proxy else 'None(池空)'}"
                )
                return proxy
            else:
                return None
        return None
_manager: Optional[ProxyManager] = None
_manager_lock = threading.Lock()
def get_proxy_manager() -> ProxyManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = ProxyManager()
    return _manager
