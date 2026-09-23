from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from buff import BuffAuthExpired, BuffRequestBlocked
from utils.delay import jittered_sleep
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass
STEAM_ACCEPT_REFERER = "https://steamcommunity.com/tradeoffer/{trade_offer_id}/"
STEAM_ACCEPT_URL = "https://steamcommunity.com/tradeoffer/{trade_offer_id}/accept"
INVENTORY_SETTLE_ATTEMPTS = 5
INVENTORY_SETTLE_DELAY_SECONDS = 2
def _cookies_str_to_dict(cookie_str: str) -> Dict[str, str]:
    out = {}
    for part in (cookie_str or "").split(";"):
        s = part.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out
def fetch_buff_steam_trade(buff_client: Any) -> Tuple[bool, List[Dict[str, Any]], str]:
    """Fetch pending BUFF trades through the shared authenticated client.

    BUFF must not use the Steam proxy pool or a second hard-coded browser
    identity.  The supplied client owns the stable Session, UA, account lock,
    request pacing and circuit breaker used by the purchase pipeline.
    """

    try:
        if buff_client is None or not callable(getattr(buff_client, "get_steam_trades", None)):
            return False, [], "Buff 统一会话客户端不可用"
        from app.services.buff_auth import get_buff_auth_lock
        from app.services.buff_checkout_guard import (
            buff_activity_guard,
            get_unresolved_checkout,
        )
        from app.pipeline import is_shutdown_pending
        from app.state import get_status

        # Re-check while holding the same activity slot used by pipeline
        # acknowledgement/start.  This closes the old check-then-GET race.
        with get_buff_auth_lock():
            with buff_activity_guard():
                status = get_status() or {}
                if (
                    is_shutdown_pending()
                    or get_unresolved_checkout() is not None
                    or status.get("status") == "running"
                    or status.get("buff_auth_expired")
                    or status.get("buff_verification_required")
                ):
                    return False, [], "Buff 流水线或对账门禁已启用"
                raw = buff_client.get_steam_trades()
        if raw is None:
            return False, [], "Buff 待收货接口请求失败"
        if not isinstance(raw, list):
            return False, [], "数据格式异常"
        pending = []
        for x in raw:
            if x.get("state") != 1 or not x.get("tradeofferid"):
                continue
            created_at = int(x.get("created_at", 0)) if x.get("created_at") is not None else 0
            goods_list = x.get("items_to_trade") or []
            if not goods_list:
                continue
            items_in_trade = []
            for g in goods_list:
                asset_id = str(g.get("assetid", ""))
                gid = str(g.get("goods_id", ""))
                goods_id_buff = None
                if gid and gid != "0":
                    try:
                        goods_id_buff = int(gid)
                    except (ValueError, TypeError):
                        pass
                info = (x.get("goods_infos") or {}).get(gid) or {}
                if isinstance(info, dict):
                    item_name = info.get("name", "未知物品") or "未知物品"
                    market_hash_name = (info.get("market_hash_name") or "").strip()
                else:
                    item_name = "未知物品"
                    market_hash_name = ""
                items_in_trade.append({
                    "assetid": asset_id,
                    "name": item_name,
                    "market_hash_name": market_hash_name,
                    "goods_id": goods_id_buff,
                })
            pending.append({
                "tradeofferid": x.get("tradeofferid"),
                "created_at": created_at,
                "items": items_in_trade,
            })
        return True, pending, ""
    except (BuffAuthExpired, BuffRequestBlocked):
        raise
    except Exception as e:
        return False, [], str(e)[:120]
def accept_steam_trade_offer(
    trade_offer_id: str,
    steam_cookies: Dict[str, str],
) -> Optional[bool]:
    """Accept one Steam offer.

    ``None`` means the POST may have reached Steam but no definitive response
    was received.  Callers must reconcile inventory instead of resending the
    non-idempotent POST immediately.
    """
    from utils.proxy_manager import get_proxy_manager
    try:
        pm = get_proxy_manager()
        url = STEAM_ACCEPT_URL.format(trade_offer_id=trade_offer_id)
        referer = STEAM_ACCEPT_REFERER.format(trade_offer_id=trade_offer_id)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            "Origin": "https://steamcommunity.com",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": referer,
        }
        session_id = steam_cookies.get("sessionid", "").strip()
        data = {
            "sessionid": session_id,
            "serverid": "1",
            "tradeofferid": str(trade_offer_id),
            "partner": "",
            "captcha": "",
        }
        proxies = pm.get_proxies_for_request(failed=False)
        r = requests.post(
            url,
            headers=headers,
            cookies=steam_cookies,
            proxies=proxies,
            data=data,
            verify=False,
            timeout=15,
        )
        if r.status_code != 200:
            return None
        raw_text = (r.text or "").strip()
        if not raw_text:
            return None
        try:
            body = r.json()
        except Exception:
            return None
        if not isinstance(body, dict):
            return None
        if body.get("tradeid"):
            return True
        if body.get("strError"):
            return False
        if "tradeid" in body or body.get("success") == 1:
            return True
        return None
    except Exception:
        return None
def _match_purchase_for_item(
    item: dict,
    pending_purchases: List[dict],
    assigned_db_ids: set,
) -> Optional[dict]:
    """Return the best matching purchase record dict (containing _db_id), or None.
    Matching priority:
      1. goods_id exact match (most reliable)
      2. one unique, exact name match when either goods_id is unavailable
    Among candidates, prefer the oldest (smallest ``at`` timestamp).
    """
    goods_id_buff = item.get("goods_id")
    try:
        normalized_goods_id = (
            int(goods_id_buff) if goods_id_buff is not None else None
        )
    except (ValueError, TypeError):
        normalized_goods_id = None
    name_for_match = (item.get("market_hash_name") or item.get("name") or "").strip()
    exact_candidates: List[dict] = []
    name_candidates: List[dict] = []
    for p in pending_purchases:
        db_id = p.get("_db_id")
        if not db_id or db_id in assigned_db_ids:
            continue
        if p.get("assetid"):  
            continue
        try:
            purchase_goods_id = (
                int(p.get("goods_id"))
                if p.get("goods_id") is not None
                else None
            )
        except (ValueError, TypeError):
            purchase_goods_id = None
        if normalized_goods_id is not None and purchase_goods_id is not None:
            if normalized_goods_id == purchase_goods_id:
                exact_candidates.append(p)
            # Two explicit, different goods IDs must never be mixed merely
            # because BUFF returned a similar display name.
            continue
        pname = (p.get("name") or "").strip()
        if pname and name_for_match and pname == name_for_match:
            name_candidates.append(p)
    if exact_candidates:
        exact_candidates.sort(
            key=lambda x: (x.get("at") or 0, x.get("_db_id") or 0)
        )
        return exact_candidates[0]
    if len(name_candidates) != 1:
        return None
    return name_candidates[0]
def try_receive_once(
    get_purchases: Callable[[], List[dict]],
    update_purchase: Callable[[int, dict], bool],
    get_buff_client: Callable[[], Any],
    get_steam_credentials: Callable[[], dict],
    scan_inventory: Optional[Callable[[], Tuple[bool, List[dict], str]]] = None,
    update_purchase_by_id: Optional[Callable[[int, dict], bool]] = None,
) -> int:
    """Accept pending Buff→Steam trade offers and update purchase records.
    Uses ``update_purchase_by_id`` (O(1), keyed on SQLite primary key) when
    available to avoid the race condition where positional indices shift
    between the time they are read and when the update is applied.
    Falls back to positional ``update_purchase`` only if``update_purchase_by_id``
    is not supplied (backward-compatibility).
    """
    purchases = get_purchases()
    pending_records: List[dict] = [
        p for p in purchases
        if p.get("pending_receipt") and not p.get("assetid") and p.get("_db_id")
    ]
    if not pending_records:
        return 0
    buff_client = get_buff_client()
    steam_cred = get_steam_credentials()
    steam_cookies_str = steam_cred.get("cookies") or ""
    steam_cookies = _cookies_str_to_dict(steam_cookies_str)
    session_id = (steam_cred.get("session_id") or "").strip()
    if session_id:
        steam_cookies["sessionid"] = session_id
    if not steam_cookies.get("sessionid") or not steam_cookies.get("steamLoginSecure"):
        return 0
    ok, pending_tasks, err = fetch_buff_steam_trade(buff_client)
    if not ok or not pending_tasks:
        return 0
    pending_tasks = sorted(pending_tasks, key=lambda t: (t.get("created_at") or 0, t.get("tradeofferid") or ""))
    received = 0
    def _do_update(db_id: int, positional_idx: int, data: dict) -> bool:
        """Update a purchase record, preferring _db_id-based O(1) update."""
        if update_purchase_by_id and db_id:
            return update_purchase_by_id(db_id, data)
        return update_purchase(positional_idx, data)
    for task in pending_tasks:
        offer_id = task.get("tradeofferid")
        if not offer_id:
            continue

        # Validate the complete local mapping before accepting an irreversible
        # Steam trade offer.  A mixed or incomplete offer must stay pending
        # instead of disappearing before we know which rows it belongs to.
        purchases = get_purchases()
        pending_records = [
            p for p in purchases
            if p.get("pending_receipt") and not p.get("assetid") and p.get("_db_id")
        ]
        assigned_db_ids: set = set()
        pairs: List[Tuple[dict, dict]] = []
        task_items = task.get("items") or []
        for it in task_items:
            matched = _match_purchase_for_item(
                it,
                pending_records,
                assigned_db_ids,
            )
            if matched is not None:
                assigned_db_ids.add(matched["_db_id"])
                pairs.append((matched, it))
        if not task_items or len(pairs) != len(task_items):
            continue
        pairs.sort(
            key=lambda x: (x[0].get("at") or 0, x[0].get("_db_id") or 0)
        )

        # A seller-side asset ID is not proof of the asset ID that Steam will
        # assign in our inventory.  Take a successful pre-accept snapshot so
        # only genuinely new recipient-side assets can complete local rows.
        if not scan_inventory:
            continue
        ok_before, before_items, _ = scan_inventory()
        if not ok_before:
            # Keep the offer pending and retry on the next worker pass.  Once
            # accepted, we cannot safely reconstruct this baseline.
            continue
        baseline_asset_ids = {
            str(inv_item.get("assetid") or "")
            for inv_item in before_items
            if inv_item.get("assetid")
        }

        accept_result = accept_steam_trade_offer(str(offer_id), steam_cookies)
        if accept_result is False:
            continue
        already_used = {str(p.get("assetid")) for p in get_purchases() if p.get("assetid")}
        inv_by_name: Dict[str, List[dict]] = {}
        required_by_name = Counter(
            (it.get("market_hash_name") or "").strip()
            for _, it in pairs
            if (it.get("market_hash_name") or "").strip()
        )
        discovered_by_asset_id: Dict[str, dict] = {}

        # Steam inventory propagation is eventually consistent.  Poll for the
        # whole accepted offer instead of treating a single partial snapshot as
        # complete.  Keep any still-missing row pending for manual/retry repair.
        for _attempt in range(INVENTORY_SETTLE_ATTEMPTS):
            jittered_sleep(INVENTORY_SETTLE_DELAY_SECONDS)
            ok_inv, inv_list, _ = scan_inventory()
            if ok_inv:
                for inv_item in inv_list or []:
                    aid = str(inv_item.get("assetid") or "")
                    if (
                        not aid
                        or aid in baseline_asset_ids
                        or aid in already_used
                    ):
                        continue
                    mhn = (inv_item.get("market_hash_name") or "").strip()
                    if mhn:
                        discovered_by_asset_id[aid] = inv_item

                inv_by_name = {}
                for aid, inv_item in discovered_by_asset_id.items():
                    mhn = (inv_item.get("market_hash_name") or "").strip()
                    inv_by_name.setdefault(mhn, []).append(inv_item)
                for mhn in inv_by_name:
                    inv_by_name[mhn].sort(
                        key=lambda x: str(x.get("assetid") or "")
                    )

                if required_by_name and all(
                    len(inv_by_name.get(mhn, [])) >= required
                    for mhn, required in required_by_name.items()
                ):
                    break

        for purchase_rec, it in pairs:
            mhn = (it.get("market_hash_name") or "").strip()
            our_assetid = None
            if mhn and inv_by_name.get(mhn):
                for inv_item in inv_by_name[mhn][:]:
                    aid = str(inv_item.get("assetid") or "")
                    if aid in already_used:
                        continue
                    our_assetid = aid
                    already_used.add(aid)
                    inv_by_name[mhn].remove(inv_item)
                    break
            if our_assetid:
                db_id = purchase_rec.get("_db_id") or 0
                pos_idx = next(
                    (i for i, p in enumerate(purchases) if p.get("_db_id") == db_id),
                    -1,
                )
                updated = _do_update(
                    db_id,
                    pos_idx,
                    {"assetid": our_assetid, "pending_receipt": False},
                )
                if updated:
                    received += 1
                already_used.add(our_assetid)
        jittered_sleep(1)
    return received
