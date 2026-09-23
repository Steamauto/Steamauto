import re
import threading
import time
import logging
import math
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from steam.client import build_listing_url
from utils.delay import jittered_sleep
from utils.proxy_manager import get_proxy_manager
from utils.money import USD_TO_CNY_DEFAULT
from app.database import db_get_item_nameid, db_set_item_nameid

logger = logging.getLogger(__name__)

_ITEM_NAMEID_TTL = 300
_SELL_ORDERS_TTL = 60
_item_nameid_cache: dict = {}
_sell_orders_cache: dict = {}
_item_nameid_cache_lock = threading.Lock()
_sell_orders_cache_lock = threading.Lock()
def clear_caches() -> None:
    with _item_nameid_cache_lock:
        _item_nameid_cache.clear()
    with _sell_orders_cache_lock:
        _sell_orders_cache.clear()
CURRENCY_CNY = 23
STEAM_CURRENCY_USD = 1
STEAM_CURRENCY_CNY = 23
_ROOT = Path(__file__).resolve().parent.parent
_EXCHANGE_RATE_FILE = _ROOT / "config" / "exchange_rate.json"
_STEAM_CURRENCY_CODES = {
    1: "USD",
    2: "GBP",
    3: "EUR",
    4: "CHF",
    5: "RUB",
    6: "PLN",
    7: "BRL",
    8: "JPY",
    9: "NOK",
    10: "IDR",
    11: "MYR",
    12: "PHP",
    13: "SGD",
    14: "THB",
    15: "VND",
    16: "KRW",
    17: "TRY",
    18: "UAH",
    19: "MXN",
    20: "CAD",
    21: "AUD",
    22: "NZD",
    23: "CNY",
    24: "INR",
    25: "CLP",
    26: "PEN",
    27: "COP",
    28: "ZAR",
    29: "HKD",
    30: "TWD",
    31: "SAR",
    32: "AED",
    33: "SEK",
    34: "ARS",
    35: "ILS",
    36: "BYN",
    37: "KZT",
    38: "KWD",
    39: "QAR",
    40: "CRC",
    41: "UYU",
    42: "BGN",
    43: "HRK",
    44: "CZK",
    45: "DKK",
    46: "HUF",
    47: "RON",
}
_exchange_rate_cache: Tuple[float, Dict[str, float]] = (0.0, {})
_EXCHANGE_RATE_TTL = 300
_ITEM_NAMEID_PATTERNS = [
    re.compile(r"Market_LoadOrderSpread\s*\(\s*(\d+)\s*\)", re.I),
    re.compile(r"item_nameid['\"]?\s*[:=]\s*['\"]?(\d+)", re.I),
]
_SSR_RENDER_CONTEXT_RE = re.compile(
    r'window\.SSR\.renderContext=JSON\.parse\("((?:\\.|[^"\\])*)"\);',
    re.S,
)
_CS2_EXTERIOR_FILTER_TAGS = {
    "Factory New": "tag_WearCategory0",
    "Minimal Wear": "tag_WearCategory1",
    "Field-Tested": "tag_WearCategory2",
    "Well-Worn": "tag_WearCategory3",
    "Battle-Scarred": "tag_WearCategory4",
}
_CS2_QUALITY_FILTER_TAGS = {
    "tag_normal",
    "tag_strange",
    "tag_tournament",
    "tag_unusual",
}
def _format_request_error(prefix: str, exc: Exception) -> str:
    detail = str(exc).strip()
    if len(detail) > 120:
        detail = detail[:117] + "..."
    return f"{prefix}: {type(exc).__name__}" + (f" - {detail}" if detail else "")

def _http_error_reason(where: str, status_code: int) -> str:
    if status_code == 429:
        return f"{where} HTTP 429（Steam 限流）"
    if status_code == 403:
        return f"{where} HTTP 403（访问被拒绝，可能是 Cookie 失效、地区或 IP 风控）"
    if status_code in (500, 502, 503, 504):
        return f"{where} HTTP {status_code}（Steam 服务端或网络网关异常）"
    return f"{where} HTTP {status_code}"

def _extract_item_nameid(html: str) -> Optional[str]:
    for pat in _ITEM_NAMEID_PATTERNS:
        m = pat.search(html)
        if m:
            return m.group(1)
    return None

def _load_exchange_rates() -> Dict[str, float]:
    global _exchange_rate_cache
    now = time.time()
    ts, cached = _exchange_rate_cache
    if cached and now - ts < _EXCHANGE_RATE_TTL:
        return cached
    rates: Dict[str, float] = {}
    try:
        if _EXCHANGE_RATE_FILE.exists():
            with open(_EXCHANGE_RATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw_rates = data.get("rates") if isinstance(data, dict) else None
            if isinstance(raw_rates, dict):
                rates = {
                    str(k).upper(): float(v)
                    for k, v in raw_rates.items()
                    if isinstance(v, (int, float)) and float(v) > 0
                }
    except Exception as e:
        logger.debug("读取 exchange_rate.json 失败: %s", type(e).__name__)
    _exchange_rate_cache = (now, rates)
    return rates

def _extract_ssr_render_context(html: str) -> Optional[dict]:
    m = _SSR_RENDER_CONTEXT_RE.search(html or "")
    if not m:
        return None
    try:
        return json.loads(json.loads(f'"{m.group(1)}"'))
    except Exception as e:
        logger.debug("解析 Steam SSR renderContext 失败: %s", type(e).__name__)
        return None

def _normalize_market_hash_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()

def _extract_orderbook_query_name(query_key: Any) -> str:
    if (
        isinstance(query_key, list)
        and len(query_key) >= 4
        and query_key[0] == "market"
        and query_key[1] == "orderbook"
        and isinstance(query_key[3], str)
    ):
        return query_key[3].strip()
    return ""

def _extract_description_query_name(query_key: Any) -> str:
    if (
        isinstance(query_key, list)
        and len(query_key) >= 4
        and query_key[0] == "market"
        and query_key[1] == "description"
        and isinstance(query_key[3], str)
    ):
        return query_key[3].strip()
    return ""

def _extract_ssr_queries(html: str) -> Tuple[Optional[List[dict]], Optional[str]]:
    ctx = _extract_ssr_render_context(html)
    if not ctx:
        return None, "Steam 新版页面未包含 SSR renderContext"
    try:
        query_data = json.loads(ctx.get("queryData") or "{}")
    except Exception as e:
        return None, f"Steam SSR queryData 解析失败: {type(e).__name__}"
    queries = query_data.get("queries") if isinstance(query_data, dict) else None
    if not isinstance(queries, list):
        return None, "Steam SSR queryData 中没有 queries"
    return queries, None

def _strip_html_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()

def _extract_description_tag(description_data: Dict[str, Any], allowed_tags: set) -> str:
    for row in description_data.get("tags") or []:
        if not isinstance(row, dict):
            continue
        internal_name = _normalize_market_hash_name(row.get("internal_name"))
        if internal_name in allowed_tags:
            return internal_name
    return ""

def _extract_ssr_description_data(html: str, market_hash_name: str) -> Optional[Dict[str, Any]]:
    queries, _ = _extract_ssr_queries(html)
    if not queries:
        return None
    target_name = _normalize_market_hash_name(market_hash_name)
    target_folded = target_name.casefold()
    for query in queries:
        if not isinstance(query, dict):
            continue
        query_name = _extract_description_query_name(query.get("queryKey"))
        if not query_name:
            continue
        normalized_query_name = _normalize_market_hash_name(query_name)
        if normalized_query_name != target_name and normalized_query_name.casefold() != target_folded:
            continue
        data = (query.get("state") or {}).get("data")
        if isinstance(data, dict):
            return data
    return None

def _infer_cs2_quality_filter_tag(market_hash_name: str) -> str:
    normalized = _normalize_market_hash_name(market_hash_name)
    if "StatTrak" in normalized:
        return "tag_strange"
    if normalized.startswith("Souvenir "):
        return "tag_tournament"
    if normalized.startswith("★"):
        return "tag_unusual"
    return "tag_normal"

def _extract_exterior_name_from_description(description_data: Dict[str, Any]) -> str:
    market_hash_name = _normalize_market_hash_name(description_data.get("market_hash_name"))
    m = re.search(r"\(([^)]+)\)\s*$", market_hash_name)
    name_exterior = m.group(1).strip() if m else ""
    for row in description_data.get("descriptions") or []:
        if not isinstance(row, dict):
            continue
        if row.get("name") != "exterior_wear":
            continue
        value = _strip_html_text(row.get("value"))
        if ":" in value:
            value = value.split(":", 1)[1].strip()
        return value if value in _CS2_EXTERIOR_FILTER_TAGS else name_exterior
    return name_exterior

def _build_filtered_group_listing_url(
    html: str,
    market_hash_name: str,
    app_id: int,
) -> Optional[str]:
    if app_id != 730:
        return None
    description_data = _extract_ssr_description_data(html, market_hash_name)
    if not description_data:
        return None
    group_id = _normalize_market_hash_name(description_data.get("market_bucket_group_id"))
    if not group_id:
        return None
    params: List[Tuple[str, str]] = []
    exterior_tag = _extract_description_tag(
        description_data,
        set(_CS2_EXTERIOR_FILTER_TAGS.values()),
    )
    if not exterior_tag:
        exterior_name = _extract_exterior_name_from_description(description_data)
        exterior_tag = _CS2_EXTERIOR_FILTER_TAGS.get(exterior_name)
    if exterior_tag:
        params.append(("category_730_Exterior", exterior_tag))
    quality_tag = _extract_description_tag(description_data, _CS2_QUALITY_FILTER_TAGS)
    if not quality_tag:
        quality_tag = _infer_cs2_quality_filter_tag(market_hash_name)
    if quality_tag:
        params.append(("category_730_Quality", quality_tag))
    if not params:
        return None
    return f"{build_listing_url(group_id, app_id)}?{urlencode(params)}"

def _steam_cents_to_cny(
    cents: int,
    currency: int,
    usd_to_cny_rate: float,
    exchange_rates: Optional[Dict[str, float]] = None,
) -> Optional[float]:
    amount = cents / 100.0
    code = _STEAM_CURRENCY_CODES.get(currency)
    if code == "CNY":
        return amount
    if code == "USD":
        rate = (exchange_rates or {}).get("USD") or usd_to_cny_rate
        return amount * rate
    if code:
        rate = (exchange_rates or {}).get(code)
        if rate:
            return amount * rate
    return None

def _parse_compact_orders_cny(
    raw: Any,
    currency: int,
    usd_to_cny_rate: float,
    exchange_rates: Optional[Dict[str, float]] = None,
) -> List[Tuple[float, int]]:
    if not isinstance(raw, list):
        return []
    out: List[Tuple[float, int]] = []
    for i in range(0, len(raw) - 1, 2):
        try:
            price_cents = int(raw[i])
            volume = int(raw[i + 1])
        except (ValueError, TypeError):
            continue
        price = _steam_cents_to_cny(price_cents, currency, usd_to_cny_rate, exchange_rates)
        if price is None or price <= 0 or volume <= 0:
            continue
        out.append((round(price, 2), volume))
    return sorted(out, key=lambda x: x[0])

def _compact_orderbook_data_to_cny(
    data: Any,
    *,
    source: str,
    usd_to_cny_rate: float = USD_TO_CNY_DEFAULT,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(data, dict):
        return None, f"{source}返回格式异常"
    try:
        currency = int(data.get("eCurrency") or 0)
    except (ValueError, TypeError):
        currency = 0
    currency_code = _STEAM_CURRENCY_CODES.get(currency)
    exchange_rates = _load_exchange_rates()
    if not currency_code:
        return None, f"{source}币种暂不支持: eCurrency={currency}"
    if currency_code not in ("CNY", "USD") and currency_code not in exchange_rates:
        return None, f"{source}币种={currency_code}(eCurrency={currency})，但 exchange_rate.json 缺少该币种汇率"
    orders = _parse_compact_orders_cny(
        data.get("rgCompactSellOrders"),
        currency,
        usd_to_cny_rate,
        exchange_rates,
    )
    if not orders:
        return None, f"{source}为空或无法解析卖单"
    lowest_price = orders[0][0]
    raw_lowest = data.get("amtMinSellOrder")
    if raw_lowest is not None:
        try:
            converted = _steam_cents_to_cny(
                int(raw_lowest), currency, usd_to_cny_rate, exchange_rates
            )
            if converted is not None and converted > 0:
                lowest_price = round(converted, 2)
        except (ValueError, TypeError):
            pass
    return {"lowest_price": lowest_price, "sell_orders": orders}, None

def _extract_ssr_orderbook_cny(
    html: str,
    market_hash_name: Optional[str] = None,
    usd_to_cny_rate: float = USD_TO_CNY_DEFAULT,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    queries, error = _extract_ssr_queries(html)
    if not queries:
        return None, error or "Steam SSR queryData 中没有 queries"
    target_name = _normalize_market_hash_name(market_hash_name)
    candidates: List[Tuple[str, Dict[str, Any]]] = []
    for query in queries:
        if not isinstance(query, dict):
            continue
        data = (query.get("state") or {}).get("data")
        if not isinstance(data, dict) or "rgCompactSellOrders" not in data:
            continue
        candidates.append((_extract_orderbook_query_name(query.get("queryKey")), data))
    if not candidates:
        return None, "Steam 新版页面未找到 market/orderbook 数据"
    selected_name = ""
    selected_data: Optional[Dict[str, Any]] = None
    if target_name:
        for query_name, data in candidates:
            if _normalize_market_hash_name(query_name) == target_name:
                selected_name = query_name
                selected_data = data
                break
        if selected_data is None:
            target_folded = target_name.casefold()
            for query_name, data in candidates:
                if _normalize_market_hash_name(query_name).casefold() == target_folded:
                    selected_name = query_name
                    selected_data = data
                    break
    if selected_data is None and target_name:
        candidate_names = [name for name, _ in candidates if name]
        suffix = f"；当前预取订单簿名单: {candidate_names[:5]}" if candidate_names else ""
        return None, f"Steam 新版页面未预取目标变体订单簿: {target_name}{suffix}"
    if selected_data is None:
        selected_name, selected_data = candidates[0]
    return _compact_orderbook_data_to_cny(
        selected_data,
        source="Steam 新版订单簿",
        usd_to_cny_rate=usd_to_cny_rate,
    )

def _fetch_ssr_sell_orders_cny(
    session,
    market_hash_name: str,
    app_id: int,
    *,
    timeout: int = 15,
    usd_to_cny_rate: float = USD_TO_CNY_DEFAULT,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    url = build_listing_url(market_hash_name, app_id)
    pm = get_proxy_manager()
    last_error = ""
    for attempt in range(3):
        proxies = pm.get_proxies_for_request(failed=(attempt > 0))
        try:
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": url,
            }
            r = session.get(url, headers=headers, timeout=timeout, proxies=proxies, allow_redirects=True)
            if r.status_code == 200:
                result, parse_error = _extract_ssr_orderbook_cny(
                    r.text,
                    market_hash_name=market_hash_name,
                    usd_to_cny_rate=usd_to_cny_rate,
                )
                if result:
                    return result, None
                filtered_url = _build_filtered_group_listing_url(r.text, market_hash_name, app_id)
                if filtered_url and filtered_url != url:
                    headers["Referer"] = filtered_url
                    r2 = session.get(
                        filtered_url,
                        headers=headers,
                        timeout=timeout,
                        proxies=proxies,
                        allow_redirects=True,
                    )
                    if r2.status_code == 200:
                        result, parse_error2 = _extract_ssr_orderbook_cny(
                            r2.text,
                            market_hash_name=market_hash_name,
                            usd_to_cny_rate=usd_to_cny_rate,
                        )
                        if result:
                            return result, None
                        parse_error = parse_error2 or parse_error
                    else:
                        parse_error = _http_error_reason("Steam 新版分组筛选页面", r2.status_code)
                last_error = parse_error or "Steam 新版页面订单簿解析失败"
                break
            last_error = _http_error_reason("Steam 新版市场页面", r.status_code)
        except Exception as e:
            last_error = _format_request_error("Steam 新版市场页面请求异常", e)
        if attempt < 2:
            jittered_sleep(1.0)
    return None, last_error or "无法打开 Steam 新版市场页面"

def _fetch_action_orderbook_cny(
    session,
    market_hash_name: str,
    app_id: int,
    *,
    timeout: int = 15,
    usd_to_cny_rate: float = USD_TO_CNY_DEFAULT,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    name = _normalize_market_hash_name(market_hash_name)
    if not name:
        return None, "Steam 新版 orderbook 接口缺少 market_hash_name"
    url = "https://steamcommunity.com/market/orderbook"
    referer = build_listing_url(name, app_id)
    params = {
        "q": "Load",
        "qp": json.dumps([app_id, name], ensure_ascii=False, separators=(",", ":")),
    }
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        "x-valve-request-type": "queryAction",
    }
    pm = get_proxy_manager()
    last_error = ""
    for attempt in range(3):
        proxies = pm.get_proxies_for_request(failed=(attempt > 0))
        try:
            r = session.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
                proxies=proxies,
                allow_redirects=True,
            )
            if r.status_code == 200:
                try:
                    payload = r.json()
                except Exception as e:
                    last_error = f"Steam 新版 orderbook 接口返回非 JSON: {type(e).__name__}"
                    break
                if not isinstance(payload, dict):
                    last_error = "Steam 新版 orderbook 接口返回格式异常"
                    break
                response_payload = payload
                nested_payload = payload.get("data")
                if (
                    "success" not in payload
                    and isinstance(nested_payload, dict)
                    and "success" in nested_payload
                ):
                    response_payload = nested_payload
                orderbook_data = response_payload.get("data")
                has_unwrapped_orderbook = (
                    "success" not in response_payload
                    and isinstance(orderbook_data, dict)
                    and "rgCompactSellOrders" in orderbook_data
                )
                if (
                    not has_unwrapped_orderbook
                    and response_payload.get("success") not in (True, 1, "1")
                ):
                    msg = response_payload.get("message") or response_payload.get("error") or ""
                    last_error = "Steam 新版 orderbook 接口返回失败" + (f": {msg}" if msg else "")
                    break
                result, parse_error = _compact_orderbook_data_to_cny(
                    orderbook_data,
                    source="Steam 新版 orderbook 接口",
                    usd_to_cny_rate=usd_to_cny_rate,
                )
                if result:
                    return result, None
                last_error = parse_error or "Steam 新版 orderbook 接口订单簿解析失败"
                break
            last_error = _http_error_reason("Steam 新版 orderbook 接口", r.status_code)
        except Exception as e:
            last_error = _format_request_error("Steam 新版 orderbook 接口请求异常", e)
        if attempt < 2:
            jittered_sleep(1.0)
    return None, last_error or "无法访问 Steam 新版 orderbook 接口"

def get_item_nameid(
    session,
    market_hash_name: str,
    app_id: int = 730,
    *,
    timeout: int = 15,
    use_cache: bool = True,
    return_error: bool = False,
):
    key = (market_hash_name.strip(), app_id)
    db_nameid = db_get_item_nameid(key[0])
    if db_nameid:
        return (db_nameid, None) if return_error else db_nameid
    if use_cache:
        with _item_nameid_cache_lock:
            entry = _item_nameid_cache.get(key)
        if entry and time.time() < entry[1]:
            return (entry[0], None) if return_error else entry[0]
    url = build_listing_url(market_hash_name, app_id)
    headers = {
        "Accept": "*/*",
        "Referer": url,
    }
    pm = get_proxy_manager()
    last_error = ""
    for attempt in range(3):
        failed = (attempt > 0)
        proxies = pm.get_proxies_for_request(failed=failed)
        try:
            r = session.get(url, headers=headers, timeout=timeout, proxies=proxies)
            if r.status_code == 200:
                nameid = _extract_item_nameid(r.text)
                if nameid:
                    db_set_item_nameid(key[0], nameid)
                    if use_cache:
                        with _item_nameid_cache_lock:
                            _item_nameid_cache[key] = (nameid, time.time() + _ITEM_NAMEID_TTL)
                    return (nameid, None) if return_error else nameid
                last_error = "Steam 市场页面未包含旧版 item_nameid 字段（可能是 Steam 新版页面、物品名不正确或页面被风控）"
                break
            last_error = _http_error_reason("Steam 市场页面", r.status_code)
        except Exception as e:
            last_error = _format_request_error("Steam 市场页面请求异常", e)
            logger.debug("获取item_nameid失败 (attempt=%d/3) proxies=%s, error=%s", attempt+1, proxies, type(e).__name__)
        
        if attempt < 2:
            jittered_sleep(1.0)

    if return_error:
        return None, last_error or "无法打开 Steam 市场页面"
    return None
_cb_lock = threading.Lock()  
_cb_fail_streak = 0          
_cb_open_until = 0.0         
_CB_FAIL_THRESHOLD = 5       
_CB_COOLDOWN_SEC = 300       

def fetch_item_orders_histogram(
    session,
    item_nameid: str,
    *,
    country: str = "CN",
    language: str = "english",
    currency: int = CURRENCY_CNY,
    timeout: int = 15,
    return_error: bool = False,
):
    global _cb_fail_streak, _cb_open_until
    with _cb_lock:
        if time.time() < _cb_open_until:
            remaining = max(1, int(math.ceil(_cb_open_until - time.time())))
            reason = f"Steam 市场请求熔断中，约 {remaining} 秒后重试（之前连续失败）"
            return (None, reason) if return_error else None
    url = "https://steamcommunity.com/market/itemordershistogram"
    params = {
        "country": country,
        "language": language,
        "currency": currency,
        "item_nameid": item_nameid,
        "no_render": "1",
        "two_factor_hash": "",
    }
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://steamcommunity.com/market/",
    }
    pm = get_proxy_manager()
    any_success = False
    last_error = ""
    
    for attempt in range(3):
        if attempt == 0:
            effective_proxies = None                                    
        else:
            effective_proxies = pm.get_proxies_for_request(failed=True) 
            
        try:
            r = session.get(
                url, params=params, headers=headers,
                timeout=timeout, proxies=effective_proxies, verify=False,
            )
            if r.status_code == 200:
                any_success = True
                data = r.json()
                if isinstance(data, dict) and data.get("success") == 1:
                    with _cb_lock:
                        _cb_fail_streak = 0  
                    return (data, None) if return_error else data

                if isinstance(data, dict):
                    msg = data.get("message") or data.get("error") or ""
                    suffix = f"，返回: {msg}" if msg else ""
                    last_error = f"Steam 直方图接口返回 success={data.get('success')}{suffix}"
                else:
                    last_error = "Steam 直方图接口返回非 JSON 对象"
                logger.debug("直方图 success非1 resp=%s", str(data)[:150])
            else:
                last_error = _http_error_reason("Steam 直方图接口", r.status_code)
                logger.debug("直方图 HTTP %s (attempt=%d)", r.status_code, attempt+1)
        except Exception as e:
            last_error = _format_request_error("Steam 直方图请求异常", e)
            logger.debug("直方图失败 (attempt=%d/3) proxy=%s err=%s: %s", attempt+1, effective_proxies is not None, type(e).__name__, str(e)[:60])
            
        if attempt < 2:
            jittered_sleep(1.0)
    with _cb_lock:
        _cb_fail_streak += 1
        if _cb_fail_streak >= _CB_FAIL_THRESHOLD:
            _cb_open_until = time.time() + _CB_COOLDOWN_SEC
            streak_snap = _cb_fail_streak
            _cb_fail_streak = 0
        else:
            streak_snap = None
    if streak_snap is not None:
        last_error = f"Steam 市场连续 {streak_snap} 轮全部失败，已熔断 {_CB_COOLDOWN_SEC // 60} 分钟，请确认加速器/代理是否正常"
        logger.warning(
            "Steam市场连续 %d 轮全部失败，熔断 %d 分钟。请确认加速器/代理是否正常。",
            streak_snap, _CB_COOLDOWN_SEC // 60
        )

    if return_error:
        if not last_error and not any_success:
            last_error = "Steam 直方图接口无响应"
        return None, last_error or "Steam 直方图接口返回空数据"
    return None
def cents_to_yuan(cents: int) -> float:
    return cents / 100.0
def _parse_sell_order_graph(raw: Any) -> List[Tuple[float, int]]:
    if not isinstance(raw, list):
        return []
    out: List[Tuple[float, int]] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            price = float(row[0])
            volume = int(row[1])
            out.append((price, volume))
        except (ValueError, TypeError):
            continue
    return out
def get_sell_orders_cny(
    session,
    market_hash_name: str,
    app_id: int = 730,
    *,
    country: str = "CN",
    language: str = "english",
    request_delay: float = 1.0,
    use_cache: bool = True,
    return_error: bool = False,
    usd_to_cny_rate: float = USD_TO_CNY_DEFAULT,
):
    key = (market_hash_name.strip(), app_id)
    if use_cache:
        with _sell_orders_cache_lock:
            entry = _sell_orders_cache.get(key)
        if entry and time.time() < entry[1]:
            return (entry[0], None) if return_error else entry[0]
    ssr_result, ssr_error = _fetch_ssr_sell_orders_cny(
        session,
        market_hash_name,
        app_id,
        usd_to_cny_rate=usd_to_cny_rate,
    )
    if ssr_result:
        if use_cache:
            with _sell_orders_cache_lock:
                _sell_orders_cache[key] = (ssr_result, time.time() + _SELL_ORDERS_TTL)
        return (ssr_result, None) if return_error else ssr_result
    action_result, action_error = _fetch_action_orderbook_cny(
        session,
        market_hash_name,
        app_id,
        usd_to_cny_rate=usd_to_cny_rate,
    )
    if action_result:
        if use_cache:
            with _sell_orders_cache_lock:
                _sell_orders_cache[key] = (action_result, time.time() + _SELL_ORDERS_TTL)
        return (action_result, None) if return_error else action_result
    item_nameid = None
    nameid_error = None
    if use_cache:
        item_nameid = db_get_item_nameid(key[0])
        if not item_nameid:
            with _item_nameid_cache_lock:
                entry = _item_nameid_cache.get(key)
            if entry and time.time() < entry[1]:
                item_nameid = entry[0]
    if not item_nameid:
        item_nameid, nameid_error = get_item_nameid(
            session, market_hash_name, app_id, return_error=True
        )
    if not item_nameid:
        if return_error:
            reason_parts: List[str] = []
            if ssr_error:
                reason_parts.append(ssr_error)
            if action_error:
                prefix = "新版 orderbook 接口也失败: " if reason_parts else ""
                reason_parts.append(f"{prefix}{action_error}")
            if nameid_error:
                prefix = "旧版 item_nameid 解析也失败: " if reason_parts else ""
                reason_parts.append(f"{prefix}{nameid_error}")
            reason = "；".join(reason_parts) or "无法获取 Steam 卖单数据"
            return None, reason
        return None
    if request_delay > 0:
        jittered_sleep(request_delay)
    data, histogram_error = fetch_item_orders_histogram(
        session,
        item_nameid,
        country=country,
        language=language,
        currency=CURRENCY_CNY,
        return_error=True,
    )
    if not data:
        return (None, histogram_error or "无法获取 Steam 直方图数据") if return_error else None
    raw_lowest = data.get("lowest_sell_order")
    lowest_price: Optional[float] = None
    if raw_lowest is not None:
        try:
            lowest_price = cents_to_yuan(int(raw_lowest))
        except (ValueError, TypeError):
            pass
    sell_orders = _parse_sell_order_graph(data.get("sell_order_graph", []))
    result = {"lowest_price": lowest_price, "sell_orders": sell_orders}
    if use_cache:
        with _sell_orders_cache_lock:
            _sell_orders_cache[key] = (result, time.time() + _SELL_ORDERS_TTL)
    if return_error:
        if not sell_orders:
            return result, "Steam 返回空卖单图（可能当前无寄售，或接口被限制返回了不完整数据）"
        if lowest_price is None:
            return result, "Steam 返回了卖单图，但 lowest_sell_order 无法解析"
        return result, None
    return result
STEAM_MIN_PRICE = 0.03
def _get_dynamic_thresholds(current_price: float) -> Tuple[float, float]:
    if current_price < 5.0:
        return 0.10, 0.08
    if current_price < 20.0:
        return 0.30, 0.05
    if current_price < 100.0:
        return 1.0, 0.03
    if current_price < 500.0:
        return 5.0, 0.02
    return 10.0, 0.015
def compute_smart_list_price(
    sell_orders: List[Tuple[float, int]],
    *,
    wall_volume_threshold: int = 20,
    max_ignore_volume: int = 4,
    min_lowest_tier_volume: int = 3,
    min_step: float = 0.01,
    min_floor_price: float = STEAM_MIN_PRICE,
    offset: float = 0.0,
) -> Tuple[Optional[float], str]:
    if not sell_orders:
        return None, "无卖单数据"
    sell_orders = sorted(sell_orders, key=lambda x: x[0])
    while len(sell_orders) >= 2 and sell_orders[0][1] <= min_lowest_tier_volume:
        sell_orders = sell_orders[1:]
    if not sell_orders:
        return None, "无卖单数据"
    wall_index = len(sell_orders) - 1
    cumulative = 0
    for i, (price, count) in enumerate(sell_orders):
        cumulative += count
        if cumulative >= wall_volume_threshold:
            wall_index = i
            break
    analysis_scope = sell_orders[: wall_index + 1]
    if len(analysis_scope) < 2:
        target = analysis_scope[0][0] - min_step
        final = max(min_floor_price, target + offset)
        return round(final, 2), "单档无断层"
    final_price = analysis_scope[0][0] - min_step
    reason = "常规压价"
    current_ignore_vol = 0
    for i in range(len(analysis_scope) - 1):
        p_curr, c_curr = analysis_scope[i]
        p_next, _ = analysis_scope[i + 1]
        current_ignore_vol += c_curr
        if current_ignore_vol > max_ignore_volume:
            reason = "阻挡量超阈值停"
            break
        gap_abs, gap_rel = _get_dynamic_thresholds(p_curr)
        diff = p_next - p_curr
        threshold = max(gap_abs, p_curr * gap_rel)
        if diff > threshold:
            final_price = p_next - min_step
            reason = f"断层跳跃({p_curr:.2f}→{p_next:.2f})"
    final = max(min_floor_price, final_price + offset)
    return round(final, 2), reason
def get_lowest_sell_price_cny(
    session,
    market_hash_name: str,
    app_id: int = 730,
    *,
    country: str = "CN",
    language: str = "english",
    request_delay: float = 1.0,
    use_cache: bool = True,
) -> Optional[float]:
    result = get_sell_orders_cny(
        session,
        market_hash_name,
        app_id,
        country=country,
        language=language,
        request_delay=request_delay,
        use_cache=use_cache,
    )
    return result.get("lowest_price") if result else None
