"""共享 Steam 市场价查询工具.
统一的批量价格查询逻辑，供库存管理和持有饥品两个模块共用，
避免同一物品被重复查询两次。
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional, Set
from app.config_loader import get_steam_credentials, load_app_config_validated
_BATCH_MAX_WORKERS = 4
def get_steam_smart_price_cny(session, market_hash_name: str, app_id: int = 730) -> Optional[float]:
    """获取单个物品的 Steam 市场智能报价（CNY）.
    同时被 inventory.py 和 transactions.py 使用的核心函数。
    """
    from steam.market_orders import get_sell_orders_cny, compute_smart_list_price
    cfg = load_app_config_validated().get("pipeline", {})
    wall_volume = int(cfg.get("sell_price_wall_volume", 20))
    max_ignore = int(cfg.get("sell_price_max_ignore_volume", 4))
    result = get_sell_orders_cny(session, market_hash_name, app_id=app_id, request_delay=1.0)
    if not result or not result.get("sell_orders"):
        return None
    price, _ = compute_smart_list_price(
        result["sell_orders"],
        wall_volume_threshold=wall_volume,
        max_ignore_volume=max_ignore,
        min_step=0,
        offset=0,
    )
    return price
def batch_fetch_prices(names: Set[str], app_id: int = 730) -> Dict[str, float]:
    """批量查询一组物品名称的市场价，返回 {name: price_cny}.
    - PERF-01: 使用 ThreadPoolExecutor 并发查询（最多 4 线程），速度远快于串行
    - 每个线程使用独立的 session，避免 requests.Session 线程安全问题
    - 返回字典中只包含成功取到价格的条目
    """
    from steam.session import create_market_session
    if not names:
        return {}
    cred = get_steam_credentials()
    cookies = cred.get("cookies", "")
    steam_id = cred.get("steam_id", "")
    if not cookies or not steam_id:
        return {}
    def _fetch_one(name: str) -> tuple:
        """Each thread creates its own session to ensure thread safety."""
        try:
            session = create_market_session(cookies, steam_id)
            price = get_steam_smart_price_cny(session, name, app_id=app_id)
            return name, price
        except Exception:
            return name, None
    prices: Dict[str, float] = {}
    valid_names = [n for n in names if n]
    with ThreadPoolExecutor(max_workers=_BATCH_MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, name): name for name in valid_names}
        for future in as_completed(futures):
            try:
                name, price = future.result()
                if price is not None and price > 0:
                    prices[name] = round(float(price), 2)
            except Exception:
                pass
    return prices
