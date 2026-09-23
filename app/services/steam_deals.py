"""
Steam 折扣游戏数据抓取服务。
通过 CheapShark API 获取打折游戏列表，然后并发查询 Steam API
获取 16 个区域的价格和评论数据，存入 SQLite 数据库。
"""
import re
import random
import threading
import time
import requests
import urllib3
import json
from pathlib import Path
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from typing import List, Optional
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ROOT = Path(__file__).resolve().parent.parent.parent
CHECKPOINT_FILE = ROOT / "config" / "steam_deals_checkpoint.json"
CHECKPOINT_VERSION = 1
REGIONS = {
    '中国': 'cn', '俄罗斯': 'ru', '哈萨克斯坦': 'kz', '乌克兰': 'ua',
    '南亚': 'pk', '土耳其': 'tr', '阿根廷': 'ar', '阿塞拜疆': 'az',
    '越南': 'vn', '印尼': 'id', '印度': 'in', '巴西': 'br',
    '智利': 'cl', '日本': 'jp', '中国香港': 'hk', '菲律宾': 'ph'
}
REGION_NAMES = {v: k for k, v in REGIONS.items()}
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/122.0.0.0 Safari/537.36",
]
_fetch_lock = threading.Lock()
_pause_event = threading.Event()
_fetch_state = {
    "running": False,
    "progress": 0,        
    "total": 0,           
    "failed": 0,          
    "message": "",
    "pause_requested": False,
}
def get_fetch_state() -> dict:
    with _fetch_lock:
        return dict(_fetch_state)
def _update_state(**kwargs):
    with _fetch_lock:
        _fetch_state.update(kwargs)
def request_pause() -> bool:
    """Ask the running fetch worker to stop after the current safe checkpoint."""
    with _fetch_lock:
        if not _fetch_state.get("running"):
            return False
        _fetch_state["pause_requested"] = True
        _fetch_state["message"] = "⏸️ 正在暂停，等待当前请求完成..."
    _pause_event.set()
    return True
def _pause_requested() -> bool:
    return _pause_event.is_set()
def _clear_pause_request() -> None:
    _pause_event.clear()
    with _fetch_lock:
        _fetch_state["pause_requested"] = False
def _load_checkpoint() -> Optional[dict]:
    """Load persisted scan progress, if available."""
    try:
        if not CHECKPOINT_FILE.exists():
            return None
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != CHECKPOINT_VERSION:
            return None
        appids = data.get("appids", [])
        phase = data.get("phase")
        if not isinstance(appids, list):
            return None
        if not appids and phase != "discovering":
            return None
        data["appids"] = [str(appid) for appid in appids if appid]
        data["done"] = [str(appid) for appid in data.get("done", []) if appid]
        failed = data.get("failed", {})
        if isinstance(failed, list):
            failed = {str(appid): "" for appid in failed if appid}
        elif isinstance(failed, dict):
            failed = {str(k): str(v) for k, v in failed.items() if k}
        else:
            failed = {}
        data["failed"] = failed
        return data
    except Exception:
        return None
def _save_checkpoint(data: dict) -> None:
    """Atomically persist scan progress so an interrupted run can resume."""
    try:
        CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["version"] = CHECKPOINT_VERSION
        payload["updated_at"] = time.time()
        tmp_path = CHECKPOINT_FILE.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp_path.replace(CHECKPOINT_FILE)
    except Exception:
        pass
def _clear_checkpoint() -> None:
    try:
        CHECKPOINT_FILE.unlink(missing_ok=True)
    except Exception:
        pass
def get_resume_state() -> dict:
    """Return persisted checkpoint status for the UI."""
    cp = _load_checkpoint()
    if not cp:
        return {
            "has_checkpoint": False,
            "progress": 0,
            "total": 0,
            "failed": 0,
            "updated_at": None,
            "started_at": None,
            "status": None,
            "phase": None,
        }
    done = set(cp.get("done", []))
    failed = cp.get("failed", {}) or {}
    appids = cp.get("appids", [])
    phase = cp.get("phase")
    progress = len(appids) if phase == "discovering" else len(done)
    total = cp.get("total_count") or len(appids)
    return {
        "has_checkpoint": True,
        "progress": progress,
        "total": total,
        "failed": len(failed),
        "updated_at": cp.get("updated_at"),
        "started_at": cp.get("started_at"),
        "status": cp.get("status"),
        "phase": phase,
    }
def _build_valid_proxies() -> list:
    """Read proxies from app config, test them, return sorted valid ones."""
    try:
        from app.config_loader import load_app_config_validated
        cfg = load_app_config_validated()
        pool_cfg = cfg.get("proxy_pool", {})
        proxies_raw = pool_cfg.get("proxies", [])
        test_url = pool_cfg.get("test_url", "https://ipv4.webshare.io/")
        timeout = int(pool_cfg.get("timeout_seconds", 10))
    except Exception:
        return []
    if not proxies_raw:
        return []
    all_proxies = []
    for p in proxies_raw:
        host = p.get("host", "")
        port = p.get("port", 0)
        user = p.get("username", "")
        pwd = p.get("password", "")
        if not host:
            continue
        if user and pwd:
            url = f"http://{user}:{pwd}@{host}:{port}/"
        else:
            url = f"http://{host}:{port}/"
        all_proxies.append({"http": url, "https": url})
    _update_state(message=f"⏳ 正在测速代理池 ({len(all_proxies)} 个)...")
    def _test(proxy_dict):
        steam_test = "https://store.steampowered.com/api/appdetails?appids=10&cc=us&filters=basic"
        start = time.time()
        try:
            resp = requests.get(steam_test, proxies=proxy_dict, timeout=min(timeout, 8), verify=False)
            if resp.status_code == 200:
                return (proxy_dict, time.time() - start)
        except Exception:
            pass
        return None
    valid = []
    with ThreadPoolExecutor(max_workers=min(len(all_proxies), 30)) as executor:
        futures = [executor.submit(_test, p) for p in all_proxies]
        for f in as_completed(futures):
            res = f.result()
            if res:
                valid.append(res)
    valid.sort(key=lambda x: x[1])
    result = [p[0] for p in valid]
    _update_state(message=f"✅ 代理测速完成，可用 {len(result)} 个")
    return result
def _fetch_with_proxy(url: str, valid_proxies: list, max_retries: int = 5):
    """Fetch JSON from URL using random proxy from pool."""
    headers = {'User-Agent': random.choice(USER_AGENTS)}
    for attempt in range(max_retries):
        proxy = random.choice(valid_proxies) if valid_proxies else None
        try:
            resp = requests.get(url, proxies=proxy, headers=headers, timeout=8, verify=False)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(1)
                continue
        except Exception:
            continue
    return None
def _get_discounted_appids(max_count: int = 20000, valid_proxies: Optional[list] = None, checkpoint: Optional[dict] = None) -> List[str]:
    """Fetch discounted game AppIDs from Steam search API, up to max_count.
    Uses Steam's native search endpoint with specials=1 filter, paginated via
    the `start` offset parameter (max 100 per page). Every request is routed
    through the proxy pool to avoid rate-limiting (HTTP 429).
    """
    appids: List[str] = []
    seen: set = set()
    start = 0
    page_size = 100
    total_count: Optional[int] = None
    consecutive_empty = 0
    if checkpoint and checkpoint.get("phase") == "discovering":
        appids = [str(appid) for appid in checkpoint.get("appids", []) if appid]
        seen = set(appids)
        start = int(checkpoint.get("discovery_start", 0) or 0)
        total_count = checkpoint.get("total_count")
        consecutive_empty = int(checkpoint.get("consecutive_empty", 0) or 0)
        _update_state(
            message=f"🔁 继续获取打折游戏 ID，已获取 {len(appids)} 个...",
            progress=len(appids),
            total=total_count or len(appids),
        )
    else:
        _update_state(message=f"🕵️ 正在从 Steam 获取打折游戏列表 (上限 {max_count})...")
        if checkpoint is not None:
            checkpoint.update({
                "phase": "discovering",
                "status": "running",
                "appids": [],
                "done": [],
                "failed": {},
                "discovery_start": 0,
                "total_count": None,
                "consecutive_empty": 0,
            })
            _save_checkpoint(checkpoint)
    base_url = (
        "https://store.steampowered.com/search/results/"
        "?specials=1"          
        "&category1=998"       
        "&sort_by=Global_Topsellers"  
        "&json=1"
        "&count={count}&start={start}"
    )
    while len(appids) < max_count:
        if _pause_requested():
            if checkpoint is not None:
                checkpoint.update({
                    "phase": "discovering",
                    "status": "paused",
                    "appids": appids,
                    "discovery_start": start,
                    "total_count": total_count,
                    "consecutive_empty": consecutive_empty,
                })
                _save_checkpoint(checkpoint)
            _update_state(message=f"⏸️ 已暂停：打折游戏 ID 已获取 {len(appids)} 个", progress=len(appids), total=total_count or len(appids))
            return appids
        url = base_url.format(count=page_size, start=start)
        try:
            data = _fetch_with_proxy(url, valid_proxies or [], max_retries=5)
            if data is None:
                consecutive_empty += 1
                if checkpoint is not None:
                    checkpoint.update({
                        "phase": "discovering",
                        "status": "running",
                        "appids": appids,
                        "discovery_start": start,
                        "total_count": total_count,
                        "consecutive_empty": consecutive_empty,
                    })
                    _save_checkpoint(checkpoint)
                if consecutive_empty >= 3:
                    break
                time.sleep(2)
                continue
            items = data.get("items", [])
            if total_count is None:
                total_count = data.get("total_count", 0)
            if not items:
                break
            consecutive_empty = 0
            new_count = 0
            for item in items:
                logo = item.get("logo", "")
                m = re.search(r"/apps/(\d+)/", logo)
                if not m:
                    continue
                appid = m.group(1)
                if appid and appid not in seen:
                    seen.add(appid)
                    appids.append(appid)
                    new_count += 1
                    if len(appids) >= max_count:
                        break
            _update_state(
                message=(
                    f"🔄 已获取 {len(appids)}"
                    + (f"/{total_count}" if total_count else "")
                    + " 个打折游戏 ID..."
                ),
                progress=len(appids),
                total=total_count or len(appids),
            )
            start += page_size
            if checkpoint is not None:
                checkpoint.update({
                    "phase": "discovering",
                    "status": "running",
                    "appids": appids,
                    "discovery_start": start,
                    "total_count": total_count,
                    "consecutive_empty": consecutive_empty,
                })
                _save_checkpoint(checkpoint)
            time.sleep(0.5)
        except Exception:
            consecutive_empty += 1
            if checkpoint is not None:
                checkpoint.update({
                    "phase": "discovering",
                    "status": "running",
                    "appids": appids,
                    "discovery_start": start,
                    "total_count": total_count,
                    "consecutive_empty": consecutive_empty,
                })
                _save_checkpoint(checkpoint)
            if consecutive_empty >= 3:
                break
            time.sleep(2)
    if checkpoint is not None:
        checkpoint.update({
            "phase": "processing",
            "status": "running",
            "appids": appids,
            "done": checkpoint.get("done", []),
            "failed": checkpoint.get("failed", {}),
            "discovery_start": start,
            "total_count": total_count or len(appids),
            "consecutive_empty": consecutive_empty,
        })
        _save_checkpoint(checkpoint)
    _update_state(message=f"✅ 获取到 {len(appids)} 个打折游戏 ID", progress=len(appids), total=len(appids))
    return appids
def _fetch_region_data(appid: str, cc_code: str, valid_proxies: list, max_region_retries: int = 3):
    """Fetch price data for a single game in a single region.
    On network failure (data is None) rotates to a different proxy and retries
    up to max_region_retries times so transient blocks don't produce empty cells.
    """
    lang = "schinese" if cc_code == "cn" else "english"
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc={cc_code}&l={lang}&filters=price_overview,basic"
    data = None
    for _attempt in range(max(1, max_region_retries)):
        data = _fetch_with_proxy(url, valid_proxies, max_retries=3)
        if data is not None:
            break  
        time.sleep(0.5)
    price_str = None
    discount_str = "0%"
    original_str = None
    game_name = None
    final_cents = None  
    if data:
        app_info = data.get(str(appid), {})
        if app_info.get('success'):
            game_name = app_info['data'].get('name')
            price_info = app_info['data'].get('price_overview')
            if price_info:
                original_str = price_info.get('initial_formatted', price_info.get('final_formatted', None))
                price_str = price_info.get('final_formatted', None)
                final_cents = price_info.get('final', None)  
                discount = price_info.get('discount_percent', 0)
                discount_str = f"-{discount}%" if discount > 0 else "0%"
        else:
            price_str = "锁区"
    return cc_code, price_str, discount_str, original_str, game_name, final_cents
def _fetch_historical_low(appid: str, valid_proxies: list) -> Optional[dict]:
    """Fetch historical low info (price in USD and timestamp) from CheapShark."""
    lookup_url = f"https://www.cheapshark.com/api/1.0/games?steamAppID={appid}"
    data = _fetch_with_proxy(lookup_url, valid_proxies, max_retries=2)
    if data and isinstance(data, list) and len(data) > 0:
        cs_game_id = data.get("gameID") if isinstance(data, dict) else data[0].get("gameID")
        if cs_game_id:
            details_url = f"https://www.cheapshark.com/api/1.0/games?id={cs_game_id}"
            details = _fetch_with_proxy(details_url, valid_proxies, max_retries=2)
            if details:
                cheapest_info = details.get("cheapestPriceEver", {})
                price = cheapest_info.get("price")
                date_ts = cheapest_info.get("date")
                if price is not None and date_ts is not None:
                    return {"price": float(price), "date": int(date_ts)}
    return None
def _get_deal_status(current_price_usd: float, lowest_usd: float, lowest_ts: int) -> str:
    """Determine the deal status based on 24 hour threshold."""
    now_ts = time.time()
    diff_seconds = now_ts - lowest_ts
    diff_price = current_price_usd - lowest_usd
    if diff_price < -0.05:
        return "新史低"
    elif abs(diff_price) <= 0.05:
        if diff_seconds <= (24 * 3600):
            return "新史低"
        else:
            return "平史低"
    else:
        return "普通打折"
_MIN_VALID_REGIONS = 8
def _process_single_game(appid: str, valid_proxies: list, max_region_threads: int = 16, rates: dict = None) -> Optional[dict]:
    """Process a single game: fetch reviews + 16 region prices + cheapshark.
    Returns None if the record fails the quality gate (game name still unknown
    or fewer than _MIN_VALID_REGIONS regions returned a real price), so the
    caller can discard it without writing garbage to the database.
    """
    game_data = {
        "app_id": str(appid),
        "name": "Unknown",
        "name_en": "Unknown",
        "banner_url": f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg",
        "positive_rate": None,
        "total_reviews": 0,
        "discount_percent": 0,
        "deal_status": None,
        "fetched_at": time.time(),
    }
    fetched_name_cn = None
    fetched_name_en = None
    review_url = f"https://store.steampowered.com/appreviews/{appid}?json=1&language=all"
    review_resp = _fetch_with_proxy(review_url, valid_proxies, max_retries=3)
    if review_resp:
        summary = review_resp.get('query_summary', {})
        total_rev = summary.get('total_reviews', 0)
        total_pos = summary.get('total_positive', 0)
        if total_rev > 0:
            game_data["positive_rate"] = round((total_pos / total_rev) * 100, 1)
            game_data["total_reviews"] = total_rev
    with ThreadPoolExecutor(max_workers=max_region_threads) as executor:
        regions_to_fetch = list(REGIONS.values())
        if "us" not in regions_to_fetch:
            regions_to_fetch.append("us")
        futures = [
            executor.submit(_fetch_region_data, appid, code, valid_proxies)
            for code in regions_to_fetch
        ]
        for future in as_completed(futures):
            try:
                cc, price, discount, original, name, cents = future.result()
                if cc != "us":
                    game_data[f"price_{cc}"] = price
                    game_data[f"discount_{cc}"] = discount
                if cents is not None:
                    game_data[f"price_cents_{cc}"] = cents
                if cc == "cn" and original:
                    game_data["original_cn"] = original
                if name:
                    if cc == "cn":
                        fetched_name_cn = name
                    else:
                        if not fetched_name_en:
                            fetched_name_en = name
                if cc == "cn" and discount and discount != "0%":
                    try:
                        game_data["discount_percent"] = int(discount.replace("%", "").replace("+", ""))
                    except ValueError:
                        pass
            except Exception:
                pass
    final_name = fetched_name_cn if fetched_name_cn else fetched_name_en
    final_name_en = fetched_name_en if fetched_name_en else fetched_name_cn
    game_data["name"] = final_name or "Unknown"
    game_data["name_en"] = final_name_en or "Unknown"
    cs_his_low = _fetch_historical_low(appid, valid_proxies)
    if cs_his_low and game_data.get("price_cents_us") is not None:
        current_us_price = game_data["price_cents_us"] / 100.0
        game_data["deal_status"] = _get_deal_status(
            current_us_price, 
            cs_his_low["price"], 
            cs_his_low["date"]
        )
    elif game_data.get("price_cents_cn") is not None:
        game_data["deal_status"] = "普通打折"
    if game_data["name"] == "Unknown":
        return None
    valid_price_count = sum(
        1 for cc in REGIONS.values()
        if game_data.get(f"price_{cc}") not in (None, "锁区")
    )
    if valid_price_count < _MIN_VALID_REGIONS:
        return None
    return game_data
def run_fetch(max_game_threads: int = 5, max_region_threads: int = 16, force_refresh: bool = False):
    """
    Main entry: build proxy pool, fetch appids, process games concurrently.
    Progress is saved to config/steam_deals_checkpoint.json so interrupted
    scans can continue without losing already-written rows.
    Designed to be called in a background thread.
    """
    from app.database import db_upsert_steam_deal, db_clear_steam_deals, db_delete_steam_deals_except
    _clear_pause_request()
    with _fetch_lock:
        if _fetch_state["running"]:
            return
        _fetch_state.update({
            "running": True,
            "progress": 0,
            "total": 0,
            "failed": 0,
            "pause_requested": False,
            "message": "🚀 开始获取...",
        })
    try:
        if force_refresh:
            _update_state(message="🧹 正在重置旧进度和旧数据...")
            _clear_checkpoint()
            db_clear_steam_deals()
        valid_proxies = _build_valid_proxies()
        if not valid_proxies:
            _update_state(message="⚠️ 无可用代理，尝试直连...")
        checkpoint = None if force_refresh else _load_checkpoint()
        resumed = bool(checkpoint)
        if not checkpoint:
            checkpoint = {
                "version": CHECKPOINT_VERSION,
                "status": "running",
                "phase": "discovering",
                "started_at": time.time(),
                "appids": [],
                "done": [],
                "failed": {},
                "force_refresh": bool(force_refresh),
            }
            _save_checkpoint(checkpoint)
        if resumed and checkpoint.get("phase") != "discovering":
            appids = checkpoint.get("appids", [])
            done_set = set(checkpoint.get("done", []))
            failed_map = dict(checkpoint.get("failed", {}) or {})
            _update_state(
                total=len(appids),
                progress=len(done_set),
                failed=len(failed_map),
                message=f"🔁 检测到未完成任务，继续扫描 {len(appids) - len(done_set)} 款游戏...",
            )
        else:
            appids = _get_discounted_appids(max_count=20000, valid_proxies=valid_proxies, checkpoint=checkpoint)
            done_set = set()
            failed_map = {}
            if _pause_requested():
                _update_state(
                    running=False,
                    pause_requested=False,
                    progress=len(appids),
                    total=checkpoint.get("total_count") or len(appids),
                    message=f"⏸️ 已暂停：打折游戏 ID 已获取 {len(appids)} 个",
                )
                return
        if not appids:
            _clear_checkpoint()
            _update_state(running=False, pause_requested=False, message="❌ 未获取到任何游戏 ID")
            return
        remaining_appids = [appid for appid in appids if str(appid) not in done_set]
        if not remaining_appids:
            deleted = db_delete_steam_deals_except(appids)
            _clear_checkpoint()
            _update_state(
                running=False,
                pause_requested=False,
                progress=len(appids),
                total=len(appids),
                failed=0,
                message=f"🎉 已全部完成！清理过期记录 {deleted} 款",
            )
            return
        _update_state(
            total=len(appids),
            progress=len(done_set),
            message=f"⛏️ 开始处理 {len(remaining_appids)}/{len(appids)} 款游戏 ({max_game_threads}x{max_region_threads} 并发)...",
        )
        order_index = {str(appid): i for i, appid in enumerate(appids)}
        def _ordered_done() -> List[str]:
            return sorted(done_set, key=lambda x: order_index.get(str(x), len(order_index)))
        fx_file = ROOT / "config" / "exchange_rate.json"
        rates = {}
        try:
            if fx_file.exists():
                with open(fx_file, "r", encoding="utf-8") as f:
                    rates = json.load(f).get("rates", {})
        except Exception:
            pass
        count = len(done_set)
        appid_iter = iter(remaining_appids)
        future_map = {}
        pause_after_batch = False

        def _save_processing_checkpoint(status: str = "running") -> None:
            if checkpoint is not None:
                checkpoint.update({
                    "phase": "processing",
                    "status": status,
                    "appids": appids,
                    "done": _ordered_done(),
                    "failed": failed_map,
                    "last_progress": count,
                })
                _save_checkpoint(checkpoint)

        def _submit_more(executor: ThreadPoolExecutor) -> None:
            while len(future_map) < max_game_threads and not _pause_requested():
                try:
                    next_appid = next(appid_iter)
                except StopIteration:
                    break
                future = executor.submit(_process_single_game, next_appid, valid_proxies, max_region_threads, rates)
                future_map[future] = str(next_appid)

        with ThreadPoolExecutor(max_workers=max_game_threads) as executor:
            _submit_more(executor)
            if _pause_requested() and not future_map:
                pause_after_batch = True
            while future_map:
                done_futures, _pending = wait(future_map.keys(), return_when=FIRST_COMPLETED)
                for future in done_futures:
                    appid = str(future_map.pop(future))
                    count += 1
                    try:
                        game = future.result()
                        if game is None:
                            done_set.add(appid)
                            failed_map.pop(appid, None)
                            _update_state(
                                progress=count,
                                failed=len(failed_map),
                                message=f"⏭️ [{count}/{len(appids)}] 质量不达标，跳过",
                            )
                        else:
                            db_upsert_steam_deal(game)
                            done_set.add(appid)
                            failed_map.pop(appid, None)
                            _update_state(
                                progress=count,
                                failed=len(failed_map),
                                message=f"✅ [{count}/{len(appids)}] {game['name']}",
                            )
                    except Exception as e:
                        failed_map[appid] = str(e)[:120]
                        _update_state(
                            progress=count,
                            failed=len(failed_map),
                            message=f"⚠️ [{count}/{len(appids)}] 处理失败: {str(e)[:60]}",
                        )
                    _save_processing_checkpoint("running")
                if _pause_requested():
                    pause_after_batch = True
                    _update_state(message=f"⏸️ 正在暂停：等待当前 {len(future_map)} 个游戏请求完成...")
                if not pause_after_batch:
                    _submit_more(executor)
        if pause_after_batch:
            _save_processing_checkpoint("paused")
            _update_state(
                running=False,
                pause_requested=False,
                progress=count,
                failed=len(failed_map),
                message=f"⏸️ 已暂停：已处理 {count}/{len(appids)} 款游戏",
            )
            return
        if failed_map:
            if checkpoint is not None:
                checkpoint.update({
                    "phase": "processing",
                    "status": "incomplete",
                    "done": _ordered_done(),
                    "failed": failed_map,
                    "completed_at": time.time(),
                })
                _save_checkpoint(checkpoint)
            _update_state(
                running=False,
                pause_requested=False,
                progress=count,
                failed=len(failed_map),
                message=f"⚠️ 本轮完成，仍有 {len(failed_map)} 款失败；再次点击可继续补扫",
            )
            return
        deleted = db_delete_steam_deals_except(appids)
        _clear_checkpoint()
        _update_state(
            running=False,
            pause_requested=False,
            failed=0,
            message=f"🎉 完成！共处理 {count} 款游戏，清理过期记录 {deleted} 款",
        )
    except Exception as e:
        checkpoint = _load_checkpoint()
        if checkpoint:
            checkpoint["status"] = "interrupted"
            checkpoint["last_error"] = str(e)[:200]
            _save_checkpoint(checkpoint)
        _update_state(running=False, pause_requested=False, message=f"❌ 抓取出错: {str(e)[:100]}")
