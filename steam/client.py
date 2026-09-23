import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, quote, unquote, urlparse
import requests
import urllib3
from .request_policy import parse_retry_after
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
CURRENCY_CNY = "CNY"
CURRENCY_USD = "USD"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://steamcommunity.com/market/",
}
_LISTING_URL_RE = re.compile(
    r"https?://steamcommunity\.com/market/listings/[^\s\])]+",
    re.I,
)
_MARKET_GROUP_ID_RE = re.compile(r"^G[A-Za-z0-9]{8,}$")
_SSR_RENDER_CONTEXT_RE = re.compile(
    r'window\.SSR\.renderContext=JSON\.parse\("((?:\\.|[^"\\])*)"\);',
    re.S,
)


def _coerce_listing_url(value: str) -> str:
    raw = (value or "").strip()
    if raw.lower().startswith(("http://", "https://")):
        return raw
    m = _LISTING_URL_RE.search(raw)
    return m.group(0) if m else raw


def _parse_listing_url(url: str) -> Optional[Tuple[int, str]]:
    url = _coerce_listing_url(url)
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host not in {"steamcommunity.com", "www.steamcommunity.com"}:
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]
    if len(parts) < 4 or parts[0] != "market" or parts[1] != "listings":
        return None
    try:
        app_id = int(parts[2])
    except (TypeError, ValueError):
        return None
    name = unquote(parts[3]).strip()
    return (app_id, name) if name else None


def _is_market_group_id(value: str) -> bool:
    return bool(_MARKET_GROUP_ID_RE.fullmatch((value or "").strip()))


def _extract_ssr_render_context(html: str) -> Optional[dict]:
    m = _SSR_RENDER_CONTEXT_RE.search(html or "")
    if not m:
        return None
    try:
        return json.loads(json.loads(f'"{m.group(1)}"'))
    except Exception:
        return None


def _extract_ssr_queries(html: str) -> List[dict]:
    ctx = _extract_ssr_render_context(html)
    if not ctx:
        return []
    try:
        query_data = json.loads(ctx.get("queryData") or "{}")
    except Exception:
        return []
    queries = query_data.get("queries") if isinstance(query_data, dict) else None
    return queries if isinstance(queries, list) else []


def _extract_market_query_name(query_key: Any, kind: str) -> str:
    if (
        isinstance(query_key, list)
        and len(query_key) >= 4
        and query_key[0] == "market"
        and query_key[1] == kind
        and isinstance(query_key[3], str)
    ):
        return query_key[3].strip()
    return ""


def _normalize_market_tag(value: Any) -> str:
    tag = str(value or "").strip()
    if tag.startswith("tag_"):
        tag = tag[4:]
    return tag.casefold()


def _extract_category_filters(url: str) -> List[set]:
    parsed = urlparse(_coerce_listing_url(url))
    groups: List[set] = []
    for key, values in parse_qs(parsed.query, keep_blank_values=False).items():
        if not key.startswith("category_"):
            continue
        tags = {_normalize_market_tag(v) for v in values if _normalize_market_tag(v)}
        if tags:
            groups.append(tags)
    return groups


def _description_matches_filters(data: Dict[str, Any], filter_groups: List[set]) -> bool:
    if not filter_groups:
        return False
    tags = {
        _normalize_market_tag(row.get("internal_name"))
        for row in data.get("tags") or []
        if isinstance(row, dict)
    }
    return bool(tags) and all(tags.intersection(group) for group in filter_groups)


def _description_market_hash_name(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    name = data.get("market_hash_name") or data.get("market_name") or ""
    return name.strip() if isinstance(name, str) else ""


def _extract_group_market_hash_name(html: str, source_url: str = "") -> Optional[str]:
    queries = _extract_ssr_queries(html)
    if not queries:
        return None
    filter_groups = _extract_category_filters(source_url) if source_url else []
    descriptions: List[str] = []
    for query in queries:
        if not isinstance(query, dict):
            continue
        data = (query.get("state") or {}).get("data")
        name = _description_market_hash_name(data)
        if not name or _is_market_group_id(name):
            continue
        descriptions.append(name)
        if isinstance(data, dict) and _description_matches_filters(data, filter_groups):
            return name
    for query in queries:
        if not isinstance(query, dict):
            continue
        name = _extract_market_query_name(query.get("queryKey"), "orderbook")
        if name and not _is_market_group_id(name):
            return name
    return descriptions[0] if descriptions else None


def _extract_line1(html: str) -> Optional[list]:
    prefix = "var line1="
    i = html.find(prefix)
    if i == -1:
        return None
    start = i + len(prefix)
    if start >= len(html) or html[start] != "[":
        return None
    depth = 0
    for j in range(start, len(html)):
        c = html[j]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return json.loads(html[start : j + 1])
    return None
def detect_currency(html: str) -> Optional[str]:
    s = html or ""
    upper = s.upper()
    if "CNY" in upper or "RMB" in upper or "\u4eba\u6c11\u5e01" in s:
        return CURRENCY_CNY
    if "CN\u00a5" in upper or "CN\uffe5" in upper or "\uffe5" in s:
        return CURRENCY_CNY
    if "HK$" in upper or "HKD" in upper:
        return "HKD"
    if "\u20b9" in s or "INR" in upper:
        return "INR"
    if "\u20bd" in s or "RUB" in upper:
        return "RUB"
    if "\u20ac" in s or "EUR" in upper:
        return "EUR"
    if "\u20ba" in s or "TRY" in upper:
        return "TRY"
    if "R$" in upper or "BRL" in upper:
        return "BRL"
    if "ARS" in upper or "AR$" in upper:
        return "ARS"
    if "CLP" in upper or "CL$" in upper:
        return "CLP"
    if "JPY" in upper or "JP\u00a5" in upper or "\u5186" in s:
        return "JPY"
    if "USD" in upper or "US$" in upper or " USD" in upper or '"USD"' in upper:
        return CURRENCY_USD
    if "$" in s:  
        return CURRENCY_USD
    return None
def build_listing_url(market_hash_name: str, app_id: int = 730) -> str:
    encoded = quote(market_hash_name, safe="")
    return f"https://steamcommunity.com/market/listings/{app_id}/{encoded}"
def market_hash_name_from_listing_url(url: str) -> Optional[str]:
    parsed = _parse_listing_url(url)
    if not parsed:
        return None
    _, name = parsed
    if _is_market_group_id(name):
        return None
    return name


def resolve_market_hash_name_from_listing_url(
    url: str,
    *,
    timeout: int = 15,
    session=None,
    proxies: Optional[dict] = None,
) -> Optional[str]:
    parsed = _parse_listing_url(url)
    if not parsed:
        return None
    _, name = parsed
    if not _is_market_group_id(name):
        return name
    request_url = _coerce_listing_url(url)
    client = session or requests.Session()
    headers = {**DEFAULT_HEADERS, "Referer": request_url}
    kwargs = {
        "headers": headers,
        "timeout": timeout,
        "proxies": proxies,
        "allow_redirects": True,
        "verify": False,
    }
    try:
        try:
            resp = client.get(request_url, **kwargs)
        except TypeError:
            kwargs.pop("verify", None)
            resp = client.get(request_url, **kwargs)
        if getattr(resp, "status_code", None) != 200:
            return None
        return _extract_group_market_hash_name(getattr(resp, "text", "") or "", request_url)
    except Exception:
        return None
def _parse_cookie_str(s: str) -> dict:
    out = {}
    for part in (s or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out


class SteamHistoryError(Exception):
    """History failure metadata; never retains raw bodies, URLs, or credentials."""

    def __init__(self, reason: str, response=None):
        self.status_code = response.status_code if response is not None else None
        self.retry_after = None
        self.retryable = self.status_code is None or self.status_code == 408 or (
            self.status_code is not None and self.status_code >= 500
        )
        details = [reason]
        if response is not None:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
            if len(content_type) > 80 or not re.fullmatch(r"[\w.+-]+/[\w.+-]+", content_type):
                content_type = "unknown"
            try:
                data = response.json()
                body_kind = "null" if data is None else {
                    dict: "object", list: "array", str: "string", bool: "boolean",
                }.get(type(data), "number")
            except ValueError:
                body_kind = "non-json" if response.content else "empty"
            details.extend([
                f"HTTP {self.status_code}", f"content_type={content_type}",
                f"body={body_kind}", f"bytes={len(response.content)}",
            ])
            if self.status_code == 429:
                self.retry_after = parse_retry_after(response.headers.get("Retry-After"))
                details.append(f"cooldown={self.retry_after:g}s")
        super().__init__("; ".join(details))


def fetch_history(
    market_hash_name: str,
    app_id: int = 730,
    *,
    headers: Optional[dict] = None,
    timeout: int = 15,
    verify: bool = False,
    proxies: Optional[dict] = None,
    cookies: Optional[Union[dict, str]] = None,
    return_currency: bool = False,
    raise_on_error: bool = False,
) -> Union[Optional[list], Optional[dict]]:
    encoded = quote(market_hash_name, safe="")
    url = f"https://steamcommunity.com/market/pricehistory/?appid={app_id}&market_hash_name={encoded}"
    h = {**DEFAULT_HEADERS, **(headers or {})}
    if proxies is None:
        px = {}
        if os.environ.get("HTTP_PROXY"):
            px["http"] = os.environ["HTTP_PROXY"]
        if os.environ.get("HTTPS_PROXY"):
            px["https"] = os.environ["HTTPS_PROXY"]
        proxies = px if px else None
    if isinstance(cookies, str):
        cookies = _parse_cookie_str(cookies)
    try:
        resp = requests.get(
            url,
            headers=h,
            verify=verify,
            proxies=proxies,
            cookies=cookies,
            timeout=timeout,
        )
        if resp.status_code != 200:
            raise SteamHistoryError("http_error", resp)
        try:
            data = resp.json()
        except ValueError:
            raise SteamHistoryError("invalid_json", resp) from None
        if not isinstance(data, dict):
            raise SteamHistoryError("unexpected_json", resp)
        if data.get("success") not in (True, 1):
            raise SteamHistoryError("unsuccessful_response", resp)
        if not isinstance(data.get("prices"), list):
            raise SteamHistoryError("invalid_prices", resp)
        history = data["prices"]
        if return_currency:
            currency_clue = data.get("price_prefix", "") + data.get("price_suffix", "")
            currency = detect_currency(currency_clue)
            if not currency:
                currency = detect_currency(resp.text)
            return {"history": history, "currency": currency}
        return history
    except SteamHistoryError:
        if raise_on_error:
            raise
        return None
    except Exception as exc:
        if raise_on_error:
            raise SteamHistoryError(type(exc).__name__) from None
        return None
