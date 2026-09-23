"""Transaction routes (purchases, sales, stats, delist, sync)."""
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel
from app.state import (
    append_purchase,
    delete_purchase,
    delete_purchase_by_id,
    delete_sale,
    delete_sale_by_id,
    get_purchases,
    get_sales,
    is_steam_background_allowed,
    log,
    reload_transactions,
    update_purchase,
    update_purchase_by_id,
    update_sale,
)
from app.config_loader import (
    get_steam_credentials,
    load_app_config_validated,
)
from app.shared_market import get_steam_smart_price_cny, batch_fetch_prices
router = APIRouter()
STEAM_FEE_FACTOR = 1.15


class AddPurchaseBody(BaseModel):
    name: str = ""
    price: float = 0
    quantity: int = 1
    goods_id: Optional[int] = None
    steam_link: Optional[str] = None
    assetid: Optional[str] = None
class TransactionUpdateBody(BaseModel):
    type: str = "purchase"
    idx: int = 0
    db_id: Optional[int] = None  
    name: Optional[str] = None
    price: Optional[float] = None
    goods_id: Optional[int] = None
    market_price: Optional[float] = None
    # ``sale_price`` is the historical, buyer-paid (pre-fee) value used by
    # automatic Steam history sync.  The holdings UI asks for the seller's
    # actual proceeds instead, so it sends this explicit field and the backend
    # normalizes it to the legacy ``sale_price`` representation.
    sale_proceeds: Optional[float] = None
    sale_price: Optional[float] = None
    pending_receipt: Optional[bool] = None
    assetid: Optional[str] = None
    listing: Optional[bool] = None
def _name_from_steam_link(steam_link: str) -> Optional[str]:
    from steam.client import resolve_market_hash_name_from_listing_url
    from utils.proxy_manager import get_proxy_manager
    url = (steam_link or "").strip()
    if not url:
        return None
    pm = get_proxy_manager()
    for attempt in range(3):
        proxies = pm.get_proxies_for_request(failed=(attempt > 0))
        name = resolve_market_hash_name_from_listing_url(url, proxies=proxies)
        if name:
            return name
    return None
def _get_steam_smart_price_cny(session, market_hash_name: str, app_id: int = 730) -> Optional[float]:
    return get_steam_smart_price_cny(session, market_hash_name, app_id=app_id)
def _fetch_steam_lowest_cny(market_hash_name: str, app_id: int = 730) -> Optional[float]:
    name = (market_hash_name or "").strip()
    if not name:
        return None
    prices = batch_fetch_prices({name}, app_id=app_id)
    return prices.get(name)
def _enrich_purchases_with_current_prices(transactions: list) -> None:
    """Fill current_market_price on unsold purchase records using shared batch_fetch_prices."""
    purchases = [t for t in transactions if t.get("type") == "purchase"]
    if not purchases:
        return
    names: set = set()
    for t in purchases:
        if t.get("sale_price") is not None:
            continue
        name = (t.get("name") or "").strip()
        if name:
            names.add(name)
    if not names:
        return
    prices = batch_fetch_prices(names)
    for t in purchases:
        name = (t.get("name") or "").strip()
        if name in prices and t.get("sale_price") is None:
            t["current_market_price"] = prices[name]
@router.get("/api/purchases")
def api_purchases():
    return {"purchases": get_purchases()}
@router.post("/api/purchase")
def api_add_purchase(body: AddPurchaseBody):
    name = (body.name or "").strip()
    steam_link = (body.steam_link or "").strip()
    if steam_link:
        extracted = _name_from_steam_link(steam_link)
        if extracted:
            name = extracted
    if not name:
        return {"ok": False, "error": "请填写物品名称或有效的 Steam 市场链接"}
    if body.price <= 0:
        return {"ok": False, "error": "价格须大于 0"}
    qty = max(1, int(body.quantity)) if body.quantity is not None else 1
    goods_id = int(body.goods_id) if body.goods_id is not None else 0
    import time
    now = time.time()
    price = round(float(body.price), 2)
    market_price = _fetch_steam_lowest_cny(name)
    assetid_val = (body.assetid or "").strip() or None
    for _ in range(qty):
        rec = {"name": name, "goods_id": goods_id, "price": price, "at": now}
        if market_price is not None and market_price > 0:
            rec["market_price"] = round(float(market_price), 2)
        if assetid_val is not None:
            rec["assetid"] = assetid_val
        append_purchase(rec)
    return {"ok": True, "added": qty}
@router.get("/api/transactions")
def api_transactions(enrich_current_price: bool = False):
    purchases = get_purchases()
    sales = get_sales()
    out = []
    for i, p in enumerate(purchases):
        row = {"type": "purchase", "idx": i, "name": p.get("name", ""), "goods_id": p.get("goods_id", ""), "price": float(p.get("price", 0)), "at": p.get("at", 0)}
        if p.get("_db_id"):
            row["db_id"] = p.get("_db_id")  
        mp = p.get("market_price")
        if mp is not None:
            row["market_price"] = round(float(mp), 2)
        sp = p.get("sale_price")
        if sp is not None:
            row["sale_price"] = round(float(sp), 2)
        sa = p.get("sold_at")
        if sa is not None:
            row["sold_at"] = float(sa)
        if p.get("pending_receipt") is not None:
            row["pending_receipt"] = bool(p.get("pending_receipt"))
        if p.get("assetid") is not None:
            row["assetid"] = p.get("assetid") if isinstance(p.get("assetid"), str) else str(p.get("assetid"))
        if p.get("listing") is not None:
            row["listing"] = bool(p.get("listing"))
        if p.get("listing_status") is not None:
            row["listing_status"] = p.get("listing_status")
        for key in (
            "buff_order_id",
            "buff_sell_order_id",
            "batch_id",
            "bill_order_id",
        ):
            if p.get(key) is not None:
                row[key] = str(p.get(key))
        out.append(row)
    for i, s in enumerate(sales):
        row = {"type": "sale", "idx": i, "name": s.get("name", ""), "goods_id": s.get("goods_id", ""), "price": float(s.get("price", 0)), "at": s.get("at", 0), "assetid": s.get("assetid") or ""}
        if s.get("_db_id"):
            row["db_id"] = s.get("_db_id")
        out.append(row)
    out.sort(key=lambda x: x["at"], reverse=True)
    if enrich_current_price and is_steam_background_allowed():
        _enrich_purchases_with_current_prices(out)
    cfg = load_app_config_validated().get("pipeline", {})
    resell_ratio = float(cfg.get("resell_ratio", 0.85))
    if resell_ratio <= 0:
        resell_ratio = 0.85
    return {"transactions": out, "resell_ratio": resell_ratio}
@router.delete("/api/transaction")
def api_delete_transaction(type: str = "purchase", idx: int = 0, db_id: int = 0):
    if type == "purchase":
        ok = delete_purchase_by_id(db_id) if db_id else delete_purchase(idx)
    elif type == "sale":
        ok = delete_sale_by_id(db_id) if db_id else delete_sale(idx)
    else:
        return {"ok": False, "error": "type 须为 purchase 或 sale"}
    return {"ok": ok, "error": None if ok else "记录不存在或索引无效"} if ok else {"ok": False, "error": "记录不存在或索引无效"}
@router.put("/api/transaction")
def api_update_transaction(body: TransactionUpdateBody):
    data: dict = {}
    if body.name is not None:
        data["name"] = body.name
    if body.price is not None:
        data["price"] = round(float(body.price), 2)
    if body.goods_id is not None:
        data["goods_id"] = int(body.goods_id)
    if body.market_price is not None:
        if float(body.market_price) > 0:
            data["market_price"] = round(float(body.market_price), 2)
        else:
            data["market_price"] = None
    if body.sale_proceeds is not None:
        if float(body.sale_proceeds) > 0:
            data["sale_price"] = round(
                float(body.sale_proceeds) * STEAM_FEE_FACTOR,
                2,
            )
            data["sold_at"] = __import__("time").time()
        else:
            data["sale_price"] = None
            data["sold_at"] = None
    elif body.sale_price is not None:
        if float(body.sale_price) > 0:
            data["sale_price"] = round(float(body.sale_price), 2)
            data["sold_at"] = __import__("time").time()
        else:
            data["sale_price"] = None
            data["sold_at"] = None
    if body.pending_receipt is not None:
        data["pending_receipt"] = bool(body.pending_receipt)
    if body.assetid is not None:
        data["assetid"] = body.assetid if body.assetid else None
    if body.listing is not None:
        data["listing"] = bool(body.listing)
        if not body.listing:
            data["listing_status"] = None
    ok = False
    if body.type == "purchase":
        ok = update_purchase_by_id(body.db_id, data) if body.db_id else update_purchase(body.idx, data)
    elif body.type == "sale":
        ok = update_sale(body.idx, data)  
    else:
        return {"ok": False, "error": "type 须为 purchase 或 sale"}
    return {"ok": ok, "error": None if ok else "更新失败（记录不存在或无效）"} if ok else {"ok": False, "error": "更新失败"}
@router.get("/api/stats")
def api_stats():
    purchases = get_purchases()
    total_purchased = sum(
        float(p.get("price", 0))
        for p in purchases
        if p.get("sale_price") is not None and float(p.get("sale_price", 0) or 0) > 0
    )
    total_sold = sum(
        float(p.get("sale_price", 0))
        for p in purchases
        if p.get("sale_price") is not None and float(p.get("sale_price", 0) or 0) > 0
    )
    ratio_sum = 0.0
    ratio_count = 0
    total_profit = 0.0
    for p in purchases:
        sp = p.get("sale_price")
        if sp is None or float(sp or 0) <= 0:
            continue
        after_tax = float(sp) / 1.15
        cost = float(p.get("price", 0))
        total_profit += after_tax - cost
        if after_tax > 0 and cost > 0:
            ratio_sum += cost / after_tax
            ratio_count += 1
    discount_ratio = (ratio_sum / ratio_count) if ratio_count > 0 else None
    return {
        "total_purchased": round(total_purchased, 2),
        "total_sold": round(total_sold, 2),
        "total_profit": round(total_profit, 2),
        "discount_ratio": round(discount_ratio, 4) if discount_ratio is not None else None,
    }
@router.post("/api/purchase/{idx}/delist")
def api_delist_purchase(idx: int, db_id: int = 0):
    from app.steam_delist import delist_item

    try:
        purchases = get_purchases()
    except Exception as exc:
        error = f"读取本地交易记录失败: {type(exc).__name__}"
        log(error, "error", category="delist")
        return {"ok": False, "error": error}

    record_idx = idx
    if db_id:
        match = next(
            (
                (pos, purchase)
                for pos, purchase in enumerate(purchases)
                if int(purchase.get("_db_id") or 0) == db_id
            ),
            None,
        )
        if match is None:
            return {"ok": False, "error": "记录已变化或不存在，请刷新后重试"}
        record_idx, p = match
    else:
        if idx < 0 or idx >= len(purchases):
            return {"ok": False, "error": "索引无效"}
        p = purchases[idx]

    if not p.get("listing"):
        return {"ok": False, "error": "该记录非出售中状态"}
    assetid = str(p.get("assetid") or "").strip()
    if not assetid:
        return {"ok": False, "error": "无 assetid"}
    name = (p.get("name") or "").strip()
    stable_db_id = int(p.get("_db_id") or 0)

    def log_fn(msg: str, level: str = "info"):
        log(msg, level, category="delist")

    try:
        ok, new_assetid, err = delist_item(assetid, name, log_fn=log_fn)
    except Exception as exc:
        err = f"下架请求异常: {type(exc).__name__}: {str(exc)[:120]}"
        log_fn(err, "error")
        return {"ok": False, "error": err}
    if not ok:
        error = err or "下架失败"
        log_fn(error, "error")
        return {"ok": False, "error": error}

    update_data = {
        "assetid": new_assetid,
        "listing": False,
        "listing_status": None,
    }
    try:
        record_updated = (
            update_purchase_by_id(stable_db_id, update_data)
            if stable_db_id
            else update_purchase(record_idx, update_data)
        )
    except Exception as exc:
        record_updated = False
        log_fn(
            f"Steam 已下架，但本地记录写入异常: {type(exc).__name__}: {str(exc)[:120]}",
            "error",
        )

    out = {
        "ok": True,
        "assetid": new_assetid,
        "record_updated": bool(record_updated),
    }
    if err:
        out["warning"] = err
    if not record_updated:
        local_warning = "Steam 已完成下架，但本地记录更新失败；请刷新后使用“同步售出/持有”修复，勿重复下架"
        out["warning"] = "；".join(
            part for part in (out.get("warning"), local_warning) if part
        )

    if new_assetid is None:
        out["message"] = "下架成功，但未检测到新 assetid，正自动尝试同步补全..."
        log_fn("未检测到新 assetid，开始自动同步售出/持有", "info")
        try:
            from app.sync_sold import run_sync_sold_from_history

            ok_s, res_s = run_sync_sold_from_history(log_fn=log_fn)
            if ok_s:
                reload_transactions()
                pur = get_purchases()
                current = next(
                    (
                        purchase
                        for purchase in pur
                        if stable_db_id
                        and int(purchase.get("_db_id") or 0) == stable_db_id
                    ),
                    None,
                )
                if current is None:
                    identity_matches = [
                        purchase
                        for purchase in pur
                        if purchase.get("name") == p.get("name")
                        and purchase.get("at") == p.get("at")
                    ]
                    current = identity_matches[0] if len(identity_matches) == 1 else None
                auto_assetid = current.get("assetid") if current else None
                if auto_assetid:
                    out["assetid"] = auto_assetid
                    out["record_updated"] = True
                    out.pop("warning", None)
                    out["message"] = f"自动同步成功，已补全 assetid: {auto_assetid}"
            else:
                sync_error = (res_s or {}).get("error", "未知错误")
                sync_warning = f"Steam 已完成下架，但自动同步失败: {sync_error}"
                out["warning"] = "；".join(
                    part for part in (out.get("warning"), sync_warning) if part
                )
        except Exception as e:
            log_fn(f"自动同步失败: {e}", "error")
            sync_warning = "Steam 已完成下架，但自动同步异常；请稍后手动同步，勿重复下架"
            out["warning"] = "；".join(
                part for part in (out.get("warning"), sync_warning) if part
            )
    return out
@router.post("/api/sync_sold_from_history")
def api_sync_sold_from_history():
    from app.sync_sold import run_sync_sold_from_history
    def log_fn(msg: str, level: str = "info"):
        log(msg, level, category="sync_sold")
    try:
        ok, result = run_sync_sold_from_history(log_fn=log_fn)
        if not ok:
            return {"ok": False, "error": result.get("error", "同步失败")}
        reload_transactions()
        return {
            "ok": True,
            "updated": result.get("updated", 0),
            "filled": result.get("filled", 0),
            "sold_count": result.get("sold_count", 0),
        }
    except Exception as e:
        log(str(e), "error", category="sync_sold")
        return {"ok": False, "error": str(e)[:200]}
@router.post("/api/repair_error_records")
def api_repair_error_records():
    from app.repair_error_records import run as run_repair
    def log_fn(msg: str, level: str = "info"):
        log(msg, level, category="repair")
    try:
        ok, result = run_repair(log_fn=log_fn)
        if not ok:
            return {"ok": False, "error": result.get("error", "紧急修复失败")}
        reload_transactions()
        return {
            "ok": True,
            "filled": result.get("filled", 0),
            "missing": result.get("missing", 0),
            "total": result.get("total", 0),
            "preserved_sold": result.get("preserved_sold", 0),
            "changed": result.get("changed", 0),
        }
    except Exception as e:
        log(str(e), "error", category="repair")
        return {"ok": False, "error": str(e)[:200]}
