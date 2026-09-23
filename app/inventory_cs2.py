import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from config import get_steam
from steam.inventory import CS2_APP_ID, fetch_cs2_inventory
from steam.session import create_market_session
def _safe_iso(ts: float) -> Optional[str]:
    if not ts:
        return None
    try:
        return datetime.utcfromtimestamp(ts).isoformat() + "Z"
    except (OSError, OverflowError, ValueError):
        return None
def _parse_cooldown(owner_descriptions: List[dict]) -> Tuple[str, float]:
    text = ""
    ts = 0.0
    for d in owner_descriptions or []:
        val = d.get("value") or ""
        if "trade-protected" not in val:
            continue
        text = val
        date_match = re.search(r"\[date\](\d+)\[/date\]", val)
        if date_match:
            ts = float(date_match.group(1))
            break
        m = re.search(r"until (.+?) GMT", val)
        if not m:
            break
        raw = m.group(1)
        raw = raw.replace(" (", " ").replace(")", "")
        try:
            dt = datetime.strptime(raw, "%b %d, %Y %H:%M:%S").replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
        except Exception:
            ts = 0.0
        break
    return text, ts
def scan_cs2_inventory() -> Tuple[bool, List[Dict[str, Any]], str]:
    cred = get_steam()
    steam_id = cred.get("steam_id")
    cookies = cred.get("cookies")
    if not steam_id or not cookies:
        return False, [], "未配置 Steam steam_id 或 cookies"
    session = create_market_session(cookies, steam_id)
    data = fetch_cs2_inventory(session, steam_id)
    if not data:
        return False, [], "获取 CS2 库存失败"
    if isinstance(data, dict) and data.get("auth_expired"):
        return False, [], "登录已过期，请重新登录"
    items: List[Dict[str, Any]] = []
    now = time.time()
    desc_map: Dict[tuple, Dict[str, Any]] = {}
    for d in data.get("descriptions") or []:
        cid = d.get("classid")
        if cid:
            iid = d.get("instanceid") or "0"
            desc_map[(str(cid), str(iid))] = d
    for asset in data.get("assets") or []:
        cid = str(asset.get("classid", ""))
        iid = str(asset.get("instanceid") or "0")
        desc = desc_map.get((cid, iid)) or desc_map.get((cid, "0"))
        if not desc:
            continue
        name = desc.get("name") or ""
        market_hash_name = desc.get("market_hash_name") or desc.get("market_name") or name
        marketable = int(desc.get("marketable", 0))
        tradable = int(desc.get("tradable", 0))
        owner_desc = desc.get("owner_descriptions") or []
        cd_text, cd_ts = _parse_cooldown(owner_desc)
        can_trade = tradable == 1 and (not cd_ts or now >= cd_ts)
        can_sell = marketable == 1 and can_trade
        items.append(
            {
                "name": name,
                "market_hash_name": market_hash_name,
                "assetid": str(asset.get("assetid", "")),
                "appid": int(asset.get("appid", CS2_APP_ID)),
                "contextid": str(asset.get("contextid", "")),
                "marketable": marketable,
                "tradable": tradable,
                "cooldown_text": cd_text,
                "cooldown_at": cd_ts or None,
                "cooldown_at_iso": _safe_iso(cd_ts),
                "can_sell": can_sell,
                "can_trade": can_trade,
            }
        )
    items.sort(key=lambda x: (not x["can_sell"], not x["can_trade"], x["name"]))
    return True, items, ""
