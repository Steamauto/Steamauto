"""Steam 折扣游戏 API 路由."""
import json
import math
import re
import threading
import time
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Query
from app.config_loader import load_app_config_validated
from app.database import (
    db_get_steam_deals,
    db_get_steam_deals_count,
    db_get_steam_deals_last_update,
    db_get_steam_deals_price_snapshot,
    db_get_steam_deals_by_app_ids,
    db_get_steam_deals_review_snapshot,
)
router = APIRouter()
_REGION_CURRENCY = {
    "cn": "CNY", "ru": "RUB", "kz": "KZT", "ua": "UAH",
    "pk": "PKR", "tr": "TRY", "ar": "ARS", "az": "AZN",
    "vn": "VND", "id": "IDR", "in": "INR", "br": "BRL",
    "cl": "CLP", "jp": "JPY", "hk": "HKD", "ph": "PHP",
}
_FX_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "exchange_rate.json"
_REGION_CODES = [
    "cn", "ru", "kz", "ua", "pk", "tr", "ar", "az",
    "vn", "id", "in", "br", "cl", "jp", "hk", "ph",
]
_rates_cache: dict = {}
_rates_cache_ts: float = 0.0
_rates_lock = threading.Lock()
_RATES_TTL = 300  
def _load_exchange_rates() -> dict:
    """Load exchange rates from file. Returns dict of {currency: rate_to_cny}."""
    try:
        if _FX_FILE.exists():
            with open(_FX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("rates", {})
    except Exception:
        pass
    return {}
def _get_rates() -> dict:
    """Return cached exchange rates; refresh from disk at most every 5 minutes."""
    global _rates_cache, _rates_cache_ts
    with _rates_lock:
        if _rates_cache and (time.time() - _rates_cache_ts) < _RATES_TTL:
            return _rates_cache
        fresh = _load_exchange_rates()
        _rates_cache = fresh
        _rates_cache_ts = time.time()
        return fresh
_sort_lock = threading.Lock()
_sort_cache: dict = {}   
_SORT_TTL = 900          
def _invalidate_sort_cache() -> None:
    with _sort_lock:
        _sort_cache.clear()
def _build_sort_index(
    sort_by: str,
    sort_dir: str,
    compare_region: str,
    rates: dict,
) -> List[str]:
    """Build and cache a list of app_ids sorted by price_diff, discount_abs,
    or region_value.
    Uses the lightweight price snapshot (price columns only) to minimise data
    transfer. Result is cached for _SORT_TTL seconds so subsequent page
    requests just slice the list.
    """
    cache_key = f"{sort_by}|{sort_dir}|{compare_region}"
    with _sort_lock:
        entry = _sort_cache.get(cache_key)
        if entry and (time.time() - entry[0]) < _SORT_TTL:
            return entry[1]
    snap = db_get_steam_deals_price_snapshot()
    # For region_value we need review counts — fetch them once and build a lookup
    review_map: dict = {}
    if sort_by == "region_value":
        for item in db_get_steam_deals_review_snapshot():
            review_map[item["app_id"]] = item["total_reviews"]
    scored: List = []
    for item in snap:
        app_id = item["app_id"]
        if sort_by == "discount_abs":
            orig = _parse_price(item.get("original_cn"))
            curr = _parse_price(item.get("price_cn"))
            if orig is not None and curr is not None and orig > curr:
                key = orig - curr
            else:
                key = -1.0
        elif sort_by == "region_value":
            # Filter: must have >= 2000 reviews
            if review_map.get(app_id, 0) < 2000:
                continue
            cn_cny = _to_cny(item.get("price_cn"), "cn", rates)
            reg_cny = _to_cny(item.get(f"price_{compare_region}"), compare_region, rates)
            # ratio = (cn_price - region_price) / region_price
            # Higher ratio → better cross-region deal relative to the region's own price
            if cn_cny and reg_cny and reg_cny > 0 and reg_cny < cn_cny:
                key = (cn_cny - reg_cny) / reg_cny
            else:
                key = -1.0
        else:  # price_diff
            cn_cny = _to_cny(item.get("price_cn"), "cn", rates)
            if compare_region and compare_region not in ("", "all", "cn"):
                reg_cny = _to_cny(item.get(f"price_{compare_region}"), compare_region, rates)
                key = (cn_cny - reg_cny) if (cn_cny and reg_cny and reg_cny < cn_cny) else -1.0
            else:
                min_cny: Optional[float] = None
                for rc in _REGION_CODES:
                    if rc == "cn":
                        continue
                    v = _to_cny(item.get(f"price_{rc}"), rc, rates)
                    if v is not None and (min_cny is None or v < min_cny):
                        min_cny = v
                key = (cn_cny - min_cny) if (cn_cny and min_cny and min_cny < cn_cny) else -1.0
        scored.append((app_id, key))
    reverse = True
    if sort_by == "discount_abs" and sort_dir == "asc":
        reverse = False
    scored.sort(key=lambda x: x[1], reverse=reverse)
    sorted_ids = [x[0] for x in scored]
    with _sort_lock:
        _sort_cache[cache_key] = (time.time(), sorted_ids)
    return sorted_ids
def _parse_price(price_str: str) -> float:
    """
    Parse an international price string to a numeric value in local currency.
    Handles: spaces as thousands (VND/IDR), comma-as-decimal (RUB/BRL),
    dot-as-thousands (CLP), and mixed formats.
    """
    if not price_str or price_str in ("无", "锁区", "N/A", "免费", "Free", "Free to Play"):
        return None
    cleaned = re.sub(r'[^\d,.\s]', '', price_str).strip()
    cleaned = cleaned.strip(',. ')
    if not cleaned:
        return None
    cleaned = re.sub(r'\s+', '', cleaned)
    has_comma = ',' in cleaned
    has_dot = '.' in cleaned
    if has_comma and has_dot:
        last_comma = cleaned.rfind(',')
        last_dot = cleaned.rfind('.')
        if last_comma > last_dot:
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    elif has_comma:
        parts = cleaned.split(',')
        if len(parts) == 2 and len(parts[1]) <= 2:
            cleaned = cleaned.replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    elif has_dot:
        parts = cleaned.split('.')
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) >= 1:
            cleaned = cleaned.replace('.', '')
    try:
        return float(cleaned)
    except ValueError:
        return None
def _to_cny(price_str: str, region_code: str, rates: dict) -> float:
    """Convert a regional price string to CNY float. Returns None on failure."""
    if region_code == "cn":
        return _parse_price(price_str)
    val = _parse_price(price_str)
    if val is None:
        return None
    if "USD" in price_str.upper():
        currency = "USD"
    else:
        currency = _REGION_CURRENCY.get(region_code)
    if not currency:
        return None
    rate = rates.get(currency)  
    if not rate or rate <= 0:
        return None
    return round(val * rate, 2)
# Wilson Score is implemented in app.database._compute_wilson_score — not needed here.
@router.get("/api/steam-deals")
def api_get_steam_deals(
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    search: str = Query(""),
    sort_by: str = Query("discount_percent"),
    sort_dir: str = Query("asc"),
    compare_region: str = Query(""),
    deal_status_filter: str = Query(""),
):
    """分页查询 Steam 折扣游戏，支持搜索和排序."""
    rates = _get_rates()  
    if sort_by in ("price_diff", "discount_abs", "region_value") and not search and not deal_status_filter:
        total = db_get_steam_deals_count(search=search)
        sorted_ids = _build_sort_index(sort_by, sort_dir, compare_region, rates)
        page_ids = sorted_ids[offset: offset + limit]
        games = db_get_steam_deals_by_app_ids(page_ids)
    else:
        games = db_get_steam_deals(
            offset=offset,
            limit=limit,
            search=search,
            sort_by=sort_by,
            sort_dir=sort_dir,
            compare_region=compare_region,
            deal_status_filter=deal_status_filter,
        )
        total = db_get_steam_deals_count(search=search)
    for g in games:
        cny_prices = {}
        for rc, price_str in g["prices"].items():
            cny_val = _to_cny(price_str, rc, rates)
            cny_prices[rc] = cny_val
        g["cny_prices"] = cny_prices
        cheapest = [
            (rc, cny_val)
            for rc, cny_val in cny_prices.items()
            if rc != "cn" and cny_val is not None
        ]
        cheapest.sort(key=lambda x: x[1])
        g["cheapest_regions"] = [{"region": rc, "cny": v} for rc, v in cheapest[:2]]
        cn_cny = cny_prices.get("cn")
        g["discount_abs_cn"] = None
        orig_cn_str = g.get("original_cn")
        if orig_cn_str and cn_cny is not None:
            orig_val = _parse_price(orig_cn_str)
            if orig_val is not None and orig_val > cn_cny:
                g["discount_abs_cn"] = round(orig_val - cn_cny, 2)
        g["price_diff"] = None
        if compare_region and compare_region not in ("", "all"):
            reg_cny = cny_prices.get(compare_region)
            if cn_cny is not None and reg_cny is not None and reg_cny < cn_cny:
                g["price_diff"] = round(cn_cny - reg_cny, 2)
        else:
            if cn_cny is not None and cheapest:
                min_rc, min_cny = cheapest[0]
                if min_cny < cn_cny:
                    g["price_diff"] = round(cn_cny - min_cny, 2)
    if sort_by in ("price_diff", "discount_abs") and (search or deal_status_filter):
        if sort_by == "price_diff":
            games.sort(
                key=lambda x: x.get("price_diff") if x.get("price_diff") is not None else -1,
                reverse=True,
            )
        else:
            games.sort(
                key=lambda x: x.get("discount_abs_cn") if x.get("discount_abs_cn") is not None else -1,
                reverse=(sort_dir == "desc"),
            )
        games = games[offset: offset + limit]
    return {"games": games, "total": total, "offset": offset, "limit": limit}
@router.get("/api/steam-deals/exchange-rates")
def api_exchange_rates():
    """返回当前汇率数据."""
    rates = _load_exchange_rates()
    return {"rates": rates, "region_currency": _REGION_CURRENCY}
@router.post("/api/steam-deals/fetch")
def api_fetch_steam_deals(force_refresh: bool = Query(False)):
    from app.services.steam_deals import get_fetch_state, run_fetch

    state = get_fetch_state()
    if state["running"]:
        return {"ok": False, "error": "抓取任务已在运行中"}

    cfg = load_app_config_validated()
    deals_cfg = cfg.get("steam_deals", {})
    if not deals_cfg.get("enabled", False):
        return {"ok": False, "error": "Steam 折扣功能未启用，请在设置中开启该功能。"}

    proxy_cfg = cfg.get("proxy_pool", {})
    if not proxy_cfg.get("proxies"):
        return {"ok": False, "error": "必须配置代理池才能使用此功能（请在[代理池]面板添加代理）。"}

    max_games = int(deals_cfg.get("max_game_threads", 5))
    max_regions = int(deals_cfg.get("max_region_threads", 16))

    import threading

    t = threading.Thread(
        target=run_fetch,
        args=(max_games, max_regions, force_refresh),
        daemon=True,
    )
    t.start()
    return {"ok": True, "message": "已开始获取"}
@router.post("/api/steam-deals/pause")
def api_pause_steam_deals():
    """请求暂停当前 Steam 折扣抓取任务."""
    from app.services.steam_deals import request_pause

    if not request_pause():
        return {"ok": False, "error": "当前没有正在运行的抓取任务"}
    return {"ok": True, "message": "已请求暂停，当前请求完成后会保存进度"}
@router.get("/api/steam-deals/status")
def api_steam_deals_status():
    """获取抓取状态和上次更新时间."""
    from app.services.steam_deals import get_fetch_state, get_resume_state
    state = get_fetch_state()
    resume_state = get_resume_state()
    last_update = db_get_steam_deals_last_update()
    total_games = db_get_steam_deals_count()
    cfg = load_app_config_validated()
    auto_refresh_days = int(cfg.get("steam_deals", {}).get("auto_refresh_days", 7))
    return {
        "running": state["running"],
        "progress": state["progress"],
        "total": state["total"],
        "failed": state["failed"],
        "message": state["message"],
        "pause_requested": state.get("pause_requested", False),
        "last_update": last_update,
        "total_games_in_db": total_games,
        "auto_refresh_days": auto_refresh_days,
        "resume": resume_state,
    }

@router.post("/api/steam-deals/generate-card/{app_id}")
def api_generate_deal_card(app_id: str):
    from app.services.deal_cards import generate_card, ROOT
    from app.database import get_engine, SteamDealGame
    from sqlmodel import Session, select, col
    
    try:
        engine = get_engine()
        with Session(engine) as session:
            stmt = select(SteamDealGame).where(col(SteamDealGame.app_id) == app_id)
            game = session.exec(stmt).first()
            if not game:
                return {"ok": False, "error": "未找到该游戏的折扣记录，请先在列表中获取它"}
                
            out_dir = ROOT / "output_cards"
            out_dir.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(c for c in (game.name_en or game.app_id) if c.isalnum() or c in "_ -")[:40]
            out_path = out_dir / f"{game.app_id}_{safe_name}.png"
            
            generate_card(game, str(out_path))
            return {"ok": True, "message": f"卡片已生成至 output_cards 目录！"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
