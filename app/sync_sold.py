import time
from typing import Any, Dict, List, Tuple
def _fill_assetid_from_inventory(purchases: List[dict], inv_items: List[dict]) -> int:
    used_assetids = {str(p.get("assetid")) for p in purchases if p.get("assetid")}
    filled = 0
    for p in purchases:
        if p.get("assetid"):
            continue
        pname = (p.get("name") or "").strip()
        if not pname:
            continue
        for it in inv_items:
            aid = str(it.get("assetid") or "")
            if not aid or aid in used_assetids:
                continue
            mhn = (it.get("market_hash_name") or "").strip()
            iname = (it.get("name") or "").strip()
            if mhn == pname or iname == pname:
                p["assetid"] = aid
                p["listing"] = False
                p["listing_status"] = None
                p["pending_receipt"] = False
                used_assetids.add(aid)
                filled += 1
                break
    return filled
def run_sync_sold_from_history(log_fn=None) -> Tuple[bool, Dict[str, Any]]:
    from app.config_loader import get_steam_credentials
    from app.state import get_state
    cred = get_steam_credentials()
    cookies = cred.get("cookies") or ""
    if not cookies:
        return False, {"error": "未配置 Steam Cookie"}
    _state = get_state()
    purchases = _state.get_purchases()
    sales = _state.get_sales()
    filled = 0
    try:
        from app.inventory_cs2 import scan_cs2_inventory
        if log_fn:
            log_fn("正在拉取 CS2 库存…", "info")
        ok, inv_items, err = scan_cs2_inventory()
        if ok and inv_items:
            filled = _fill_assetid_from_inventory(purchases, inv_items)
            if log_fn and filled:
                log_fn(f"库存匹配填充 assetid {filled} 条", "info")
        elif not ok and log_fn:
            log_fn(f"拉取库存失败: {err}", "warn")
    except Exception as e:
        if log_fn:
            log_fn(f"拉取/匹配库存异常: {e}", "warn")
    from app.steam_listings import fetch_my_history_sold
    c = cookies if isinstance(cookies, dict) else {}
    if not isinstance(cookies, dict):
        for part in (cookies or "").split(";"):
            s = part.strip()
            if "=" in s:
                k, _, v = s.partition("=")
                c[k.strip()] = v.strip()
    if not c.get("steamLoginSecure"):
        return False, {"error": "Cookie 中无 steamLoginSecure，请重新登录 Steam"}
    if log_fn:
        log_fn("正在拉取 Steam 市场历史 Sold 记录…", "info")
    ok, sold_map, err_msg = fetch_my_history_sold(c, debug_fn=None)
    if not ok:
        return False, {"error": err_msg or "拉取市场历史失败"}
    if log_fn:
        log_fn(f"解析到售出 {len(sold_map)} 条", "info")
    updated = 0
    sold_at = time.time()
    for i, p in enumerate(purchases):
        aid = str(p.get("assetid") or "").strip()
        if not aid or aid not in sold_map:
            continue
        if p.get("sale_price") is not None and float(p.get("sale_price") or 0) > 0:
            continue
        sale_price = sold_map[aid]
        purchases[i] = {**p, "sale_price": sale_price, "sold_at": sold_at, "listing": False, "listing_status": None}
        updated += 1
    if updated or filled:
        _state.replace_transactions(purchases, sales)
    return True, {"updated": updated, "filled": filled, "sold_count": len(sold_map)}
