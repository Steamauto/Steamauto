import base64
import html
import json
import re
import time
import requests
import urllib3
from bs4 import BeautifulSoup
from steam.session import parse_cookies
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
def _build_cookies(cookies_raw: str) -> dict:
    """从完整 cookie 字符串构建 cookies dict，并确保必要字段存在。"""
    cookies = parse_cookies(cookies_raw)
    cookies.setdefault("birthtime", "283993201")
    cookies.setdefault("wants_mature_content", "1")
    return cookies
def get_friend_list(cookies_raw: str, my_steamid: str) -> list:
    url = f"https://steamcommunity.com/profiles/{my_steamid}/friends/"
    cookies = _build_cookies(cookies_raw)
    from utils.proxy_manager import get_proxy_manager
    from app.state import log as _log
    pm = get_proxy_manager()
    last_exc = None
    resp = None
    for attempt in range(2):
        try:
            proxies = pm.get_proxies_for_request(failed=(attempt > 0))
            _log(
                f"[gift_engine] get_friend_list attempt={attempt} proxy={'使用: ' + proxies.get('http') if proxies else '本机'}",
                "debug", category="proxy"
            )
            resp = requests.get(
                url, params={"ajax": "1"}, cookies=cookies,
                headers=_HEADERS, proxies=proxies, verify=False, timeout=15
            )
            resp.raise_for_status()
            break
        except Exception as e:
            last_exc = e
            _log(
                f"[gift_engine] get_friend_list attempt={attempt} "
                f"异常类型={type(e).__name__} 详情={e}",
                "debug", category="proxy"
            )
            if attempt == 0:
                continue
            raise RuntimeError(f"拉取好友列表失败: {last_exc}") from last_exc
    if resp is None:
        raise RuntimeError(f"拉取好友列表失败: {last_exc}")
    soup = BeautifulSoup(resp.text, "html.parser")
    friends = []
    seen = set()
    for block in soup.find_all(attrs={"data-steamid": True}):
        fid = block.get("data-steamid")
        if not fid or fid == my_steamid or fid in seen:
            continue
        seen.add(fid)
        name_tag = block.find(class_="friend_block_content")
        name = " ".join(name_tag.get_text(strip=True).split()) if name_tag else fid
        avatar_tag = block.find("img")
        avatar = avatar_tag["src"] if avatar_tag else ""
        friends.append({"steamid": fid, "name": name, "avatar": avatar})
    return friends
_CURRENCY_MAP = {
    1:  ("USD", "$"),
    2:  ("GBP", "£"),
    3:  ("EUR", "€"),
    5:  ("RUB", "₽"),
    6:  ("PLN", "zł"),
    7:  ("BRL", "R$"),
    8:  ("JPY", "¥"),
    9:  ("NOK", "kr"),
    10: ("IDR", "Rp"),
    11: ("MYR", "RM"),
    12: ("PHP", "₱"),
    13: ("SGD", "S$"),
    14: ("THB", "฿"),
    15: ("VND", "₫"),
    16: ("KRW", "₩"),
    17: ("TRY", "₺"),
    18: ("UAH", "₴"),
    19: ("MXN", "Mex$"),
    20: ("CAD", "C$"),
    21: ("AUD", "A$"),
    22: ("NZD", "NZ$"),
    23: ("CNY", "¥"),    
    24: ("INR", "₹"),
    25: ("CLP", "CLP$"),
    26: ("PEN", "S/."),
    27: ("COP", "COL$"),
    28: ("ZAR", "R"),
    29: ("HKD", "HK$"),
    30: ("TWD", "NT$"),
    31: ("SAR", "SR"),
    32: ("AED", "AED"),
    34: ("ARS", "ARS$"),
    35: ("ILS", "₪"),
    37: ("KZT", "₸"),
    38: ("KWD", "KD"),
    39: ("QAR", "QR"),
}
_NO_DIVIDE_CURRENCIES = {8, 16, 15}

_COUNTRY_CODE_KEYS = (
    "country_code",
    "country",
    "store_country_code",
    "wallet_country",
    "user_country",
)


def _normalize_country_code(value) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip().upper()
    return value if re.fullmatch(r"[A-Z]{2}", value) else ""


def _find_country_code(value) -> str:
    if isinstance(value, dict):
        for key in _COUNTRY_CODE_KEYS:
            code = _normalize_country_code(value.get(key))
            if code:
                return code
        for item in value.values():
            code = _find_country_code(item)
            if code:
                return code
    elif isinstance(value, list):
        for item in value:
            code = _find_country_code(item)
            if code:
                return code
    return ""


def _extract_store_user_config(page_text: str) -> dict:
    for pattern in (
        r'data-store_user_config="([^"]+)"',
        r"data-store_user_config='([^']+)'",
    ):
        cfg = re.search(pattern, page_text or "")
        if not cfg:
            continue
        try:
            data = json.loads(html.unescape(cfg.group(1)))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _extract_country_code(page_text: str, config_data: dict = None) -> str:
    code = _find_country_code(config_data or {})
    if code:
        return code

    for source in (page_text or "", html.unescape(page_text or "")):
        for key in _COUNTRY_CODE_KEYS:
            match = re.search(rf'"{re.escape(key)}"\s*:\s*"([A-Za-z]{{2}})"', source)
            if match:
                code = _normalize_country_code(match.group(1))
                if code:
                    return code
    return ""


def get_base_auth_status(cookies_raw: str, *, require_country: bool = False):
    url = f"https://store.steampowered.com/cart/?_t={int(time.time() * 1000)}"
    cookies = _build_cookies(cookies_raw)
    from utils.proxy_manager import get_proxy_manager
    pm = get_proxy_manager()
    try:
        proxies = pm.get_proxies_for_request()
        resp = requests.get(url, cookies=cookies, headers=_HEADERS, proxies=proxies, verify=False, timeout=15)
        page_text = resp.text or ""
        jwt_token = ""
        config_data = _extract_store_user_config(page_text)
        if config_data:
            jwt_token = config_data.get("webapi_token", "")
        country_code = _extract_country_code(page_text, config_data)
        if require_country and not country_code:
            raise RuntimeError("无法从 Steam 商店页面解析账号地区")
        if not country_code:
            country_code = "CN"
        return jwt_token, country_code, config_data
    except Exception as e:
        raise RuntimeError(f"获取底层鉴权失败: {e}") from e
def get_wallet_balance(cookies_raw: str) -> dict:
    cookies = _build_cookies(cookies_raw)
    from utils.proxy_manager import get_proxy_manager
    pm = get_proxy_manager()
    proxies = pm.get_proxies_for_request()
    unknown_currency_id = None
    try:
        resp = requests.get(
            "https://steamcommunity.com/market/",
            cookies=cookies,
            headers={**_HEADERS, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            proxies=proxies,
            verify=False,
            timeout=15,
        )
        m = re.search(r'g_rgWalletInfo\s*=\s*(\{[^;]+\})', resp.text)
        if m:
            wallet = json.loads(m.group(1))
            if wallet.get("success") == 1 or "wallet_balance" in wallet:
                balance_raw  = int(wallet.get("wallet_balance", 0))
                delayed_raw  = int(wallet.get("wallet_delayed_balance", 0))
                total_raw    = balance_raw + delayed_raw
                currency_id  = int(wallet.get("wallet_currency", 0))
                wallet_country = _normalize_country_code(wallet.get("wallet_country"))
                currency_info = _CURRENCY_MAP.get(currency_id)
                if not currency_info:
                    if currency_id > 0:
                        unknown_currency_id = currency_id
                    raise RuntimeError(f"未知 Steam 钱包币种 ID: {currency_id}")
                code, symbol = currency_info
                display = (
                    f"{symbol}{total_raw:,}"
                    if currency_id in _NO_DIVIDE_CURRENCIES
                    else f"{symbol}{total_raw / 100.0:.2f}"
                )
                return {
                    "balance_raw": total_raw,
                    "balance_display": display,
                    "currency_code": code,
                    "currency_symbol": symbol,
                    "currency_id": currency_id,
                    "wallet_country": wallet_country,
                    "country_code": wallet_country,
                }
    except Exception:
        pass
    try:
        jwt_token, _, _ = get_base_auth_status(cookies_raw)
        if jwt_token:
            api_url = (
                "https://api.steampowered.com/IWalletService/GetWalletDetails/v1"
                f"?access_token={jwt_token}"
            )
            resp = requests.get(api_url, headers=_HEADERS, proxies=proxies, verify=False, timeout=15)
            if resp.status_code == 200:
                data = resp.json().get("response", {})
                balance_raw  = int(data.get("balance", 0))
                delayed_raw  = int(data.get("delayed_balance", 0))
                total_raw    = balance_raw + delayed_raw
                currency_id  = int(data.get("currency", 0))
                wallet_country = _normalize_country_code(
                    data.get("country")
                    or data.get("wallet_country")
                    or data.get("country_code")
                )
                if currency_id > 0:
                    currency_info = _CURRENCY_MAP.get(currency_id)
                    if not currency_info:
                        if currency_id > 0:
                            unknown_currency_id = currency_id
                        raise RuntimeError(f"未知 Steam 钱包币种 ID: {currency_id}")
                    code, symbol = currency_info
                    display = (
                        f"{symbol}{total_raw:,}"
                        if currency_id in _NO_DIVIDE_CURRENCIES
                        else f"{symbol}{total_raw / 100.0:.2f}"
                    )
                    return {
                        "balance_raw": total_raw,
                        "balance_display": display,
                        "currency_code": code,
                        "currency_symbol": symbol,
                        "currency_id": currency_id,
                        "wallet_country": wallet_country,
                        "country_code": wallet_country,
                    }
    except Exception:
        pass
    if unknown_currency_id is not None:
        raise RuntimeError(f"未知 Steam 钱包币种 ID: {unknown_currency_id}")
    raise RuntimeError("无法获取 Steam 钱包余额，请确认 Cookie 有效且账户已设置钱包")
def extract_appid_from_url(url: str) -> str:
    m = re.search(r'/app/(\d+)', url)
    return m.group(1) if m else ""
def get_all_available_editions(app_id: str, cookies_raw: str) -> list:
    url = f"https://store.steampowered.com/app/{app_id}/"
    headers = {**_HEADERS, "Accept-Language": "en-US,en;q=0.9"}
    cookies = _build_cookies(cookies_raw)
    cookies["Steam_Language"] = "english"
    from utils.proxy_manager import get_proxy_manager
    pm = get_proxy_manager()
    try:
        proxies = pm.get_proxies_for_request()
        resp = requests.get(url, headers=headers, cookies=cookies, proxies=proxies, verify=False, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        editions = []
        seen_ids = set()
        for block in soup.find_all("div", class_=re.compile(r"game_area_purchase_(game|bundle)")):
            item_id = block.find("input", {"name": "subid"})
            item_type = "subid"
            if not item_id:
                item_id = block.find("input", {"name": "bundleid"})
                item_type = "bundleid"
            if not item_id:
                continue
            item_id = item_id.get("value")
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            name = f"Unknown ({item_type}: {item_id})"
            heading = block.find(["h1", "h2"])
            if heading:
                name = heading.get_text(separator=" ", strip=True)
            else:
                nd = block.find("div", class_=re.compile(r"game_area_purchase_name"))
                if nd:
                    name = nd.get_text(separator=" ", strip=True)
            name = re.sub(r'(?i)BUNDLE\s*\(\?\)', '', name).strip()
            if name.lower().startswith("buy "):
                name = name[4:].strip()
            price = ""
            original_price = ""
            discount_pct = ""
            final_tag = block.find(class_=re.compile(r"discount_final_price"))
            if final_tag:
                price = final_tag.get_text(strip=True)
            orig_tag = block.find(class_=re.compile(r"discount_original_price"))
            if orig_tag:
                original_price = orig_tag.get_text(strip=True)
            pct_tag = block.find(class_=re.compile(r"discount_pct"))
            if pct_tag:
                discount_pct = pct_tag.get_text(strip=True)
            if not price:
                normal_tag = block.find(class_=re.compile(r"game_purchase_price"))
                if normal_tag:
                    price = normal_tag.get_text(strip=True)
            if not price:
                free_tag = block.find(string=re.compile(r'(?i)(free|免费|Play for Free)'))
                if free_tag:
                    price = "免费"
            editions.append({
                "name": name,
                "id": item_id,
                "type": item_type,
                "price": price,
                "original_price": original_price,
                "discount_pct": discount_pct,
            })
        title_tag = soup.find("div", class_="apphub_AppName") or soup.find("span", itemprop="name")
        game_title = title_tag.get_text(strip=True) if title_tag else f"AppID {app_id}"
        og_image = ""
        og = soup.find("meta", property="og:image")
        if og:
            og_image = og.get("content", "")
        return editions, game_title, og_image
    except Exception as e:
        raise RuntimeError(f"获取商品版本失败: {e}") from e
def _encode_varint(n):
    res = bytearray()
    while n > 127:
        res.append((n & 127) | 128)
        n >>= 7
    res.append(n)
    return res
def _build_addcart_payload(item_id: int, item_type: str) -> str:
    if item_type == "subid":
        item_msg = bytearray([0x08]) + _encode_varint(item_id)
        tail_b64 = "GlIKFnN0b3JlLnN0ZWFtcG93ZXJlZC5jb20SB2RlZmF1bHQaB2RlZmF1bHQiACoWbWFpbi1jbHVzdGVyLXRvcHNlbGxlcjABOgJJTkgAUgBYAWAA"
    else:
        item_msg = bytearray([0x10]) + _encode_varint(item_id) + bytearray([0x5A, 0x02, 0x08, 0x01])
        tail_b64 = "GjoKFnN0b3JlLnN0ZWFtcG93ZXJlZC5jb20SC2FwcGxpY2F0aW9uGgNhcHAiACoAMAA6AklOSABSAFgB"
    payload = bytearray([0x0A, 0x02, 0x49, 0x4E]) + bytearray([0x12]) + _encode_varint(len(item_msg)) + item_msg + base64.b64decode(tail_b64)
    return base64.b64encode(payload).decode('utf-8')
def _build_modify_payload(line_item_id: int, friend_steamid64: str, country_code: str) -> str:
    account_id = int(friend_steamid64) - 76561197960265728
    payload = bytearray([0x08]) + _encode_varint(line_item_id)
    cc_bytes = country_code.encode('utf-8')
    payload += bytearray([0x12]) + _encode_varint(len(cc_bytes)) + cc_bytes
    friend_msg = bytearray([0x08]) + _encode_varint(account_id)
    payload += bytearray([0x52]) + _encode_varint(len(friend_msg)) + friend_msg
    payload += bytearray([0x5A, 0x04, 0x08, 0x01, 0x10, 0x00])
    return base64.b64encode(payload).decode('utf-8')
def _build_remove_payload(line_item_id: int) -> str:
    payload = bytearray([0x08]) + _encode_varint(line_item_id)
    return base64.b64encode(payload).decode('utf-8')
def _grpc_request(jwt_token: str, endpoint: str, payload_b64: str) -> bool:
    url = f"https://api.steampowered.com/IAccountCartService/{endpoint}?access_token={jwt_token}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://store.steampowered.com",
        "Referer": "https://store.steampowered.com/",
    }
    from utils.proxy_manager import get_proxy_manager
    from app.state import log as _log
    pm = get_proxy_manager()
    for attempt in range(2):
        try:
            proxies = pm.get_proxies_for_request(failed=(attempt > 0))
            _log(
                f"[gift_engine] gRPC {endpoint} attempt={attempt} proxy={'使用: ' + proxies.get('http') if proxies else '本机'}",
                "debug", category="proxy"
            )
            resp = requests.post(url, headers=headers, proxies=proxies, files={"input_protobuf_encoded": (None, payload_b64)}, verify=False, timeout=15)
            result = resp.headers.get('X-Eresult') == '1'
            _log(f"gRPC {endpoint} → X-Eresult={resp.headers.get('X-Eresult')} ok={result}", "debug", category="proxy")
            return result
        except Exception as e:
            _log(
                f"[gift_engine] gRPC {endpoint} attempt={attempt} "
                f"异常类型={type(e).__name__} 详情={e}",
                "debug", category="proxy"
            )
            if attempt == 0:
                continue
    return False
def _get_cart_items(jwt_token: str) -> list:
    url = f"https://api.steampowered.com/IAccountCartService/GetCart/v1?access_token={jwt_token}"
    from utils.proxy_manager import get_proxy_manager
    from app.state import log as _log
    pm = get_proxy_manager()
    for attempt in range(2):
        try:
            proxies = pm.get_proxies_for_request(failed=(attempt > 0))
            _log(
                f"[gift_engine] GetCart attempt={attempt} proxy={'使用: ' + proxies.get('http') if proxies else '本机'}",
                "debug", category="proxy"
            )
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Origin": "https://store.steampowered.com"}, proxies=proxies, verify=False, timeout=15)
            if resp.status_code == 200:
                data = resp.json().get("response", resp.json())
                items = [str(item.get("line_item_id")) for item in data.get("cart", {}).get("line_items", [])]
                _log(f"[gift_engine] GetCart → {len(items)} 个订单行", "debug", category="proxy")
                return items
        except Exception as e:
            _log(
                f"[gift_engine] GetCart attempt={attempt} "
                f"异常类型={type(e).__name__} 详情={e}",
                "debug", category="proxy"
            )
            if attempt == 0:
                continue
    return []
def _steamid64_to_accountid(steamid64: str) -> int:
    """将 SteamID64 转换为 AccountID（用于结账礼物参数）。"""
    try:
        return int(steamid64) - 76561197960265728
    except (ValueError, TypeError):
        return 0
def run_gift_flow(
    cookies_raw: str,
    friend_steamid: str,
    item_id: str,
    item_type: str,
):
    """
    生成器函数，逐步执行赠礼流程，yield dict:
      {"step": int, "total": 5, "msg": str, "ok": bool, "done": bool}
    """
    total = 5
    def _step(n, msg):
        return {"step": n, "total": total, "msg": msg, "ok": True, "done": False}
    def _fail(n, msg):
        return {"step": n, "total": total, "msg": msg, "ok": False, "done": True}
    def _success(msg):
        return {"step": total, "total": total, "msg": msg, "ok": True, "done": True}
    yield _step(0, "Init Auth Token...")
    try:
        jwt_token, country_code, _ = get_base_auth_status(cookies_raw)
    except Exception as e:
        yield _fail(0, f"获取鉴权失败: {e}")
        return
    if not jwt_token:
        yield _fail(0, "未能获取 JWT token，请检查 Steam Cookie 是否有效")
        return
    yield _step(1, "Clearing Cart...")
    existing = _get_cart_items(jwt_token)
    for iid in existing:
        _grpc_request(jwt_token, "RemoveItemFromCart/v1", _build_remove_payload(int(iid)))
        time.sleep(0.3)
    after_clear = _get_cart_items(jwt_token)
    if after_clear:
        yield _fail(1, f"购物车清空失败，仍有 {len(after_clear)} 个残留订单行，请手动在 Steam 清空购物车后重试")
        return
    yield _step(2, "Adding Item to Cart...")
    ok = _grpc_request(jwt_token, "AddItemsToCart/v1", _build_addcart_payload(int(item_id), item_type))
    if not ok:
        yield _fail(2, "加购失败，Steam 拒绝了请求（商品可能不支持礼物或已拥有）")
        return
    time.sleep(1)
    yield _step(3, "Fetching Order LineItem...")
    cart_items = _get_cart_items(jwt_token)
    if not cart_items:
        yield _fail(3, "购物车为空，流水号获取失败")
        return
    if len(cart_items) != 1:
        yield _fail(3, f"购物车订单行数量异常 ({len(cart_items)} != 1)，可能卷入了旧商品，请手动清空购物车后重试")
        return
    line_item_id = cart_items[0]
    yield _step(4, "Modifying LineItem & Binding Giftee...")
    mod_ok = _grpc_request(jwt_token, "ModifyLineItem/v1", _build_modify_payload(int(line_item_id), friend_steamid, country_code))
    if not mod_ok:
        yield _fail(4, "订单属性修改失败，该商品可能不支持赠礼")
        return
    time.sleep(1.5)
    yield _step(5, "Checkout Finalization...")
    giftee_account_id = _steamid64_to_accountid(friend_steamid)
    if not giftee_account_id:
        yield _fail(5, f"无法解析受赠人 SteamID: {friend_steamid}")
        return
    try:
        checkout_ok = _do_checkout(cookies_raw, country_code, giftee_account_id=giftee_account_id)
    except Exception as e:
        yield _fail(5, f"结账异常: {e}")
        return
    if checkout_ok:
        yield _success("Success: Gift sent.")
    else:
        yield _fail(5, "结账失败，可能余额不足或被风控拦截")
def _do_checkout(cookies_raw: str, country_code: str, giftee_account_id: int = 0) -> bool:
    cookies = _build_cookies(cookies_raw)
    session_id = cookies.get("sessionid", "")
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://checkout.steampowered.com",
        "Referer": "https://checkout.steampowered.com/checkout/?accountcart=1",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    is_gift = giftee_account_id != 0
    init_payload = {
        "gidShoppingCart": "-1", "gidReplayOfTransID": "-1", "bUseAccountCart": "1",
        "PaymentMethod": "steamaccount", "abortPendingTransactions": "0", "bHasCardInfo": "0",
        "Country": country_code,
        "bIsGift": "1" if is_gift else "0",
        "GifteeAccountID": str(giftee_account_id) if is_gift else "0",
        "bSaveBillingAddress": "1", "bUseRemainingSteamAccount": "1", "bPreAuthOnly": "0",
        "sessionid": session_id,
    }
    from utils.proxy_manager import get_proxy_manager
    from app.state import log as _log
    pm = get_proxy_manager()
    for attempt in range(2):
        try:
            proxies = pm.get_proxies_for_request(failed=(attempt > 0))
            _log(
                f"[gift_engine] checkout inittransaction attempt={attempt} proxy={'使用: ' + proxies.get('http') if proxies else '本机'}",
                "debug", category="proxy"
            )
            init_res = requests.post(
                "https://checkout.steampowered.com/checkout/inittransaction/",
                data=init_payload, headers=headers, cookies=cookies, proxies=proxies, verify=False, timeout=15
            )
            init_data = init_res.json()
            _log(f"inittransaction → success={init_data.get('success')} transid={init_data.get('transid')}", "debug", category="proxy")
            if init_data.get("success") != 1:
                if attempt == 0:
                    continue  
                return False
            transid = init_data.get("transid")
            fin_res = requests.post(
                "https://checkout.steampowered.com/checkout/finalizetransaction/",
                data={
                    "transid": transid, "CardCVV2": "",
                    "browserInfo": '{"language":"zh-CN","javaEnabled":"false","colorDepth":24,"screenHeight":1440,"screenWidth":2560}',
                },
                headers=headers, cookies=cookies, proxies=proxies, verify=False, timeout=20
            )
            fin_data = fin_res.json()
            _log(f"finalizetransaction → success={fin_data.get('success')}", "debug", category="proxy")
            return fin_data.get("success") == 1
        except Exception as e:
            _log(
                f"[gift_engine] checkout attempt={attempt} "
                f"异常类型={type(e).__name__} 详情={e}",
                "debug", category="proxy"
            )
            if attempt == 0:
                continue
    return False
