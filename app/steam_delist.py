import json
import re
import time
from typing import Any, Callable, Dict, Optional, Tuple
import requests
from config import get_steam
from steam.session import create_market_session
from utils.delay import jittered_sleep
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass
MYLISTINGS_API_URL = "https://steamcommunity.com/market/mylistings/"
REMOVE_LISTING_URL = "https://steamcommunity.com/market/removelisting/"
MARKET_URL = "https://steamcommunity.com/market/"
REMOVELISTING_PATTERN = re.compile(
    r"(?:RemoveMarketListing|CancelMarketListingConfirmation)\('mylisting',\s*'(\d+)',\s*\d+,\s*'[^']*',\s*'(\d+)'\)"
)
SESSIONID_PATTERN = re.compile(r'g_sessionID\s*=\s*"([^"]+)"')
TIMEOUT = 25
def _cookies_to_dict(cookies) -> dict:
    if isinstance(cookies, dict):
        return dict(cookies)
    out = {}
    for part in (cookies or "").split(";"):
        s = part.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out
def _get_mylistings_api(session) -> Optional[Dict[str, Dict[str, Any]]]:
    params = {"norender": "1", "start": "0", "count": "100"}
    try:
        r = session.get(MYLISTINGS_API_URL, params=params, verify=False, timeout=TIMEOUT)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except (json.JSONDecodeError, TypeError):
        return None
    if not data.get("success"):
        return None
    asset_info: Dict[str, Dict[str, Any]] = {}
    def extract_items(container):
        if container is None:
            return []
        if isinstance(container, dict):
            return list(container.values())
        if isinstance(container, list):
            return container
        return []
    outer_assets = data.get("assets") or {}
    all_listings = extract_items(data.get("listings"))
    all_listings.extend(extract_items(data.get("listings_to_confirm")))
    for item in all_listings:
        if not isinstance(item, dict):
            continue
        listing_id = str(item.get("listingid", "")).strip()
        asset = item.get("asset") or {}
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("id", "")).strip()
        appid = str(asset.get("appid", "730"))
        contextid = str(asset.get("contextid", "2"))
        class_id = str(asset.get("classid", "")).strip()
        instance_id = str(asset.get("instanceid", "")).strip() or "0"
        if not listing_id or not asset_id:
            continue
        if not class_id or not instance_id:
            ref = (outer_assets.get(appid) or {}).get(contextid) or {}
            if isinstance(ref, dict):
                ref_asset = ref.get(asset_id) or (ref.get(int(asset_id)) if asset_id.isdigit() else None)
            else:
                ref_asset = None
            if isinstance(ref_asset, dict):
                class_id = str(ref_asset.get("classid", class_id)).strip()
                instance_id = str(ref_asset.get("instanceid", instance_id)).strip() or "0"
        if class_id and instance_id:
            asset_info[asset_id] = {
                "listingid": listing_id,
                "classid": class_id,
                "instanceid": instance_id,
                "appid": appid,
                "contextid": contextid,
            }
    return asset_info
def _extract_js_var(html: str, var_name: str) -> str:
    prefix = f"var {var_name} = "
    i = html.find(prefix)
    if i < 0:
        return ""
    i += len(prefix)
    if i >= len(html) or html[i] != "{":
        return ""
    depth, start, j = 0, i, i
    while j < len(html):
        ch = html[j]
        if ch == "{":
            depth += 1
            j += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start:j + 1]
            j += 1
        elif ch in ('"', "'"):
            q, k = ch, j + 1
            while k < len(html):
                if html[k] == "\\":
                    k += 2
                    continue
                if html[k] == q:
                    k += 1
                    break
                k += 1
            j = k
        else:
            j += 1
    return ""
def _get_asset_class_instance_from_market_page(html: str, assetid: str) -> Optional[Tuple[str, str]]:
    inv_js = _extract_js_var(html, "g_rgInventory")
    if not inv_js:
        return None
    try:
        inventory = json.loads(inv_js)
    except (json.JSONDecodeError, TypeError):
        return None
    for appid, ctx_dict in (inventory if isinstance(inventory, dict) else {}).items():
        if not isinstance(ctx_dict, dict):
            continue
        for ctx, asset_dict in ctx_dict.items():
            if not isinstance(asset_dict, dict):
                continue
            details = asset_dict.get(str(assetid))
            if isinstance(details, dict):
                cid = details.get("classid") or details.get("classId")
                iid = details.get("instanceid") or details.get("instanceId") or "0"
                if cid:
                    return (str(cid), str(iid))
    return None
def _get_assetids_by_class_instance(
    session, steam_id: str, appid: str, contextid: str, classid: str, instanceid: str
) -> Optional[set]:
    result = set()
    url = f"https://steamcommunity.com/inventory/{steam_id}/{appid}/{contextid}"
    last_assetid = None
    try:
        while True:
            params = {"l": "english", "count": 2000, "_": int(time.time() * 1000)}
            if last_assetid:
                params["start_assetid"] = last_assetid
            r = session.get(url, params=params, verify=False, timeout=20)
            if r.status_code != 200:
                return None
            try:
                data = r.json() if r.text else {}
            except (ValueError, TypeError):
                return None
            if data.get("success") != 1:
                return None
            assets = data.get("assets") or []
            for asset in assets:
                cid = str(asset.get("classid", ""))
                iid = str(asset.get("instanceid") or "0")
                if cid == classid and iid == instanceid:
                    aid = str(asset.get("assetid", ""))
                    if aid:
                        result.add(aid)
            if not data.get("more_items"):
                break
            last_assetid = data.get("last_assetid")
            if not last_assetid and assets:
                last_assetid = assets[-1].get("assetid")
            if not last_assetid:
                break
            jittered_sleep(0.5)
    except Exception:
        return None
    return result
def delist_item(assetid: str, name: str, log_fn: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, Optional[str], Optional[str]]:
    try:
        cred = get_steam()
    except Exception as exc:
        return False, None, f"读取 Steam 配置失败: {type(exc).__name__}"
    if not isinstance(cred, dict):
        return False, None, "Steam 配置格式无效，请重新保存账号配置"
    cookies_str = cred.get("cookies", "")
    steam_id = cred.get("steam_id", "")
    if not cookies_str or not steam_id:
        return False, None, "未配置 Steam Cookie 或 steam_id"
    try:
        c = _cookies_to_dict(cookies_str)
    except (AttributeError, TypeError, ValueError):
        return False, None, "Steam Cookie 格式无效，请重新登录 Steam"
    if not c.get("steamLoginSecure"):
        return False, None, "Cookie 中无 steamLoginSecure，请重新登录 Steam"
    sessionid = (c.get("sessionid") or "").strip()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://steamcommunity.com",
        "Referer": "https://steamcommunity.com/market/",
    }
    try:
        session = requests.Session()
        session.headers.update(headers)
        session.cookies.update(c)
        session.verify = False
        asset_info = _get_mylistings_api(session)
        listing_id = None
        classid, instanceid = None, None
        appid, contextid = "730", "2"
        if asset_info and assetid in asset_info:
            info = asset_info[assetid]
            listing_id = info.get("listingid", "")
            classid = info.get("classid", "")
            instanceid = info.get("instanceid", "")
            appid = info.get("appid", "730")
            contextid = info.get("contextid", "2")
        if not sessionid:
            r = session.get(MARKET_URL, timeout=TIMEOUT)
            if r.status_code == 200:
                sessionid_match = SESSIONID_PATTERN.search(r.text or "")
                if sessionid_match:
                    sessionid = sessionid_match.group(1)
            if not sessionid:
                return False, None, "未能在 Cookie 或市场页找到 sessionid"
        if not listing_id:
                r = session.get(MARKET_URL, timeout=TIMEOUT)
                if r.status_code != 200:
                    return False, None, f"获取市场页失败 HTTP {r.status_code}"
                html = r.text or ""
                matches = REMOVELISTING_PATTERN.findall(html)
                aid_to_lid = {str(aid): str(lid) for lid, aid in matches}
                if assetid not in aid_to_lid:
                    return False, None, f"未找到 assetid {assetid} 对应的上架记录，请确认该物品在「我的上架」中"
                listing_id = aid_to_lid[assetid]
        if not classid or not instanceid:
            if asset_info and assetid in asset_info:
                info = asset_info[assetid]
                classid = info.get("classid", "")
                instanceid = info.get("instanceid", "")
                appid = info.get("appid", "730")
                contextid = info.get("contextid", "2")
            if not classid:
                r = session.get(MARKET_URL, timeout=TIMEOUT)
                html = (r.text or "") if r.status_code == 200 else ""
                ci = _get_asset_class_instance_from_market_page(html, assetid) if html else None
                if ci:
                    classid, instanceid = ci
            if not classid and name:
                from steam.inventory import CS2_APP_ID, CS2_CONTEXT_MAIN
                sess = create_market_session(cookies_str, steam_id)
                try:
                    inv_r = sess.get(
                        f"https://steamcommunity.com/inventory/{steam_id}/{CS2_APP_ID}/{CS2_CONTEXT_MAIN}",
                        params={"l": "english", "count": 5000}, timeout=20
                    )
                    if inv_r.status_code == 200:
                        inv = inv_r.json()
                        if inv.get("success") == 1:
                            desc_map = {}
                            for d in inv.get("descriptions") or []:
                                cid = d.get("classid")
                                if cid:
                                    iid = str(d.get("instanceid") or "0")
                                    mhn = (d.get("market_hash_name") or d.get("name") or "").strip()
                                    desc_map[(str(cid), iid)] = mhn
                            name_lower = name.strip().lower()
                            for a in inv.get("assets") or []:
                                cid, iid = str(a.get("classid", "")), str(a.get("instanceid") or "0")
                                mhn = desc_map.get((cid, iid), "")
                                if name_lower and mhn and name_lower in mhn.lower():
                                    classid, instanceid = cid, iid
                                    break
                except Exception:
                    pass
        if not listing_id:
            return False, None, f"未找到 assetid {assetid} 对应的上架记录，请确认该物品在「我的上架」中"
        if not classid:
            return False, None, "无法获取物品的 classid/instanceid，请先打开 Steam 市场「我的上架」页确认该物品在售"
        set_before = _get_assetids_by_class_instance(session, steam_id, appid, contextid, classid, instanceid)
        if set_before is None:
            return False, None, "下架前读取 Steam 库存失败，尚未提交下架请求，请稍后重试"
        if log_fn:
            log_fn("已获取下架前库存 assetid 集合", "info")
        remove_url = f"{REMOVE_LISTING_URL}{listing_id}"
        post_headers = {
            "Origin": "https://steamcommunity.com",
            "Referer": "https://steamcommunity.com/market/",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        resp = session.post(remove_url, data={"sessionid": sessionid}, headers=post_headers, timeout=TIMEOUT)
        if resp.status_code != 200:
            return False, None, f"下架请求失败 HTTP {resp.status_code}"
        response_text = (resp.text or "").strip()
        if response_text:
            try:
                response_data = resp.json()
            except (ValueError, TypeError):
                response_data = None
            if isinstance(response_data, dict) and "success" in response_data:
                success = response_data.get("success")
                if success not in (1, True, "1"):
                    detail = response_data.get("message") or response_data.get("error") or ""
                    suffix = f": {str(detail)[:100]}" if detail else ""
                    return False, None, f"Steam 拒绝了下架请求{suffix}"
            elif response_data in (False, 0, "0", "false", "failure", "failed"):
                return False, None, "Steam 返回下架失败"
            elif "<html" in response_text.lower() and (
                "login" in response_text.lower()
                or "sign in" in response_text.lower()
            ):
                return False, None, "Steam 返回登录页面，Cookie 可能已失效，请重新登录"
            elif response_text.lower() in {"false", "failure", "failed", "error"}:
                return False, None, "Steam 返回下架失败"
        if log_fn:
            log_fn("等待 3 秒让 Steam 后端刷新库存…", "info")
        jittered_sleep(3)
        new_ids = set()
        inventory_checked = False
        for attempt in range(3):
            set_after = _get_assetids_by_class_instance(session, steam_id, appid, contextid, classid, instanceid)
            if set_after is None:
                if attempt < 2:
                    jittered_sleep(2)
                continue
            inventory_checked = True
            new_ids = set_after - set_before
            if len(new_ids) == 1:
                break
            if attempt < 2:
                jittered_sleep(2)
        if not inventory_checked:
            warning = "下架请求已提交，但 Steam 库存暂时无法读取，结果尚未确认；请刷新市场状态，勿立即重复下架"
            if log_fn:
                log_fn(warning, "warn")
            return True, None, warning
        if len(new_ids) == 1:
            new_assetid = next(iter(new_ids))
            if log_fn:
                log_fn(f"下架成功，新 assetid={new_assetid}", "info")
            return True, new_assetid, None
        if len(new_ids) == 0:
            if log_fn:
                log_fn("下架成功但库存中未检测到新 assetid，已清空本地 assetid，请使用「同步售出/持有」补全", "warn")
            return True, None, None
        return False, None, f"下架后新 assetid 数量异常: {len(new_ids)} (期望 1)"
    except Exception as e:
        return False, None, str(e)[:150]
