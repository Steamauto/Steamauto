import json
from typing import Optional, Union
SELL_ITEM_URL = "https://steamcommunity.com/market/sellitem/"


def parse_sell_response(text: object) -> tuple[bool, Optional[dict]]:
    """Parse a Steam listing response without assuming a JSON object.

    Steam, a CDN, or an intermediary may occasionally return valid JSON such
    as ``null`` or a non-object payload.  Callers use mapping keys like
    ``success`` and ``message``, so only a JSON object is a usable response.
    """
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            ok = data.get("success") is True
            return ok, data
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        pass
    return False, None


# Compatibility for any existing internal imports of the old private helper.
_parse_sell_response = parse_sell_response


def list_item(
    session,
    session_id: str,
    appid: int,
    contextid: Union[str, int],
    assetid: str,
    price: Union[int, float],
    amount: int = 1,
) -> Optional[dict]:
    payload = {
        "sessionid": session_id,
        "appid": str(appid),
        "contextid": str(contextid),
        "assetid": str(assetid),
        "amount": str(amount),
        "price": str(int(price) if isinstance(price, float) and price == int(price) else price),
    }
    try:
        r = session.post(SELL_ITEM_URL, data=payload, timeout=15)
        return {
            "status_code": r.status_code,
            "text": r.text,
            "retry_after": r.headers.get("Retry-After"),
        }
    except Exception:
        return None
def list_item_by_name(
    session,
    steam_id: str,
    session_id: str,
    item_name: str,
    price: Union[int, float],
    *,
    app_id: int = 753,
    context_id: int = 6,
    count: int = 75,
) -> dict:
    from .inventory import CS2_APP_ID, fetch_cs2_inventory, fetch_inventory, find_asset_by_name
    if app_id == CS2_APP_ID:
        inv = fetch_cs2_inventory(session, steam_id, count=count)
    else:
        inv = fetch_inventory(session, steam_id, app_id=app_id, context_id=context_id, count=count)
    asset = find_asset_by_name(inv, item_name)
    if not asset:
        return {"ok": False, "error": "未找到该名称的物品或库存中无此资产"}
    out = list_item(
        session,
        session_id,
        asset["appid"],
        asset["contextid"],
        asset["assetid"],
        price,
    )
    if not out:
        return {"ok": False, "error": "上架请求异常"}
    ok, parsed = parse_sell_response(out["text"])
    ok = ok and out["status_code"] == 200
    result = {
        "ok": ok,
        "status_code": out["status_code"],
        "response_text": out["text"],
        "response": parsed,
        "assetid": asset["assetid"] if ok else None,
    }
    if not ok and parsed and "message" in parsed:
        result["error"] = parsed.get("message", out["text"][:200])
    elif not ok:
        result["error"] = out["text"][:200] if out.get("text") else "请求失败"
    return result
