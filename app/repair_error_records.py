import json
import re
import sys
import time
import unicodedata
import urllib.parse
from collections import defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests
from bs4 import BeautifulSoup
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass
CREDENTIALS_FILE = ROOT / "config" / "credentials.json"
MYHISTORY_RENDER_URL = "https://steamcommunity.com/market/myhistory/render/"
HISTORY_PAGE_SIZE = 500
HISTORY_MAX_PAGES = 40
HOVER_PATTERN = re.compile(
    r"CreateItemHoverFromContainer\s*\(\s*g_rgAssets\s*,\s*'(history_row_\d+_\d+)_name'\s*,\s*(\d+)\s*,\s*'(\d+)'\s*,\s*'(\d+)'"
)
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
def _load_rate_map() -> dict:
    try:
        fx_file = ROOT / "config" / "exchange_rate.json"
        if fx_file.exists():
            with open(fx_file, "r", encoding="utf-8") as f:
                fx = json.load(f)
            if isinstance(fx, dict) and isinstance(fx.get("rates"), dict):
                return {k: float(v) for k, v in fx["rates"].items() if isinstance(v, (int, float))}
    except Exception:
        pass
    return {}
def _currency_code_from_price_text(text: str) -> str:
    s = text or ""
    if "¥" in s or "￥" in s or "CNY" in s or "RMB" in s:
        return "CNY"
    if "HK" in s and "$" in s:
        return "HKD"
    if "₹" in s:
        return "INR"
    if "₽" in s:
        return "RUB"
    if "€" in s:
        return "EUR"
    if "USD" in s or "US$" in s:
        return "USD"
    if "$" in s:
        return "USD"
    return "CNY"
def _parse_sold_history_page(data: dict, rate_map: dict) -> tuple:
    row_to_assetid = {}
    for m in HOVER_PATTERN.finditer(data.get("hovers") or ""):
        row_to_assetid[m.group(1)] = str(m.group(4))
    sold = {}
    sold_names = {}
    html = data.get("results_html") or ""
    soup = BeautifulSoup(html, "html.parser")
    row_count = 0
    for row in soup.find_all("div", class_="market_listing_row"):
        row_id = row.get("id") or ""
        if not row_id.startswith("history_row_"):
            continue
        row_count += 1
        assetid = row_to_assetid.get(row_id)
        if not assetid:
            fallback = re.search(r"assetid[\"']?\s*[:=]\s*[\"']?(\d+)[\"']?", str(row), re.I)
            if fallback:
                assetid = str(fallback.group(1))
            else:
                link = row.find("a", href=re.compile(r"assetid=\d+"))
                if link and link.get("href"):
                    ma = re.search(r"assetid=(\d+)", link["href"])
                    if ma:
                        assetid = str(ma.group(1))
            if not assetid:
                continue
        else:
            assetid = str(assetid)
        status_div = row.find("div", class_="market_listing_listed_date_combined")
        status_text = (status_div.get_text(strip=True) or "") if status_div else ""
        if not any(s in status_text for s in ("Sold", "已售出", "出售")):
            continue
        name = ""
        name_el = row.find("span", class_="market_listing_item_name") or row.find("a", class_="market_listing_item_name_link")
        if name_el:
            name = (name_el.get_text(strip=True) or "").strip()
        if not name:
            link = row.find("a", href=re.compile(r"listings/730/"))
            if link and link.get("href"):
                m = re.search(r"listings/730/(.+)$", link["href"])
                if m:
                    name = urllib.parse.unquote(m.group(1)).strip() or "(未知)"
        if not name:
            name = "(未知)"
        price_el = row.find("span", class_="market_listing_price")
        if not price_el:
            continue
        raw_text = price_el.get_text() or ""
        cur_code = _currency_code_from_price_text(raw_text)
        text = raw_text.replace(",", ".")
        m = re.search(r"[\d.]+", text)
        if m:
            try:
                raw = float(m.group(0))
                cny_raw = raw
                if cur_code != "CNY":
                    rate = rate_map.get(cur_code)
                    if rate:
                        cny_raw = raw * rate
                sale_price = round(cny_raw * 1.15, 2)
                sold[assetid] = sale_price
                sold_names[assetid] = name
            except (ValueError, TypeError):
                pass
    return sold, sold_names, row_count
def _history_total_count(data: dict):
    for key in ("total_count", "totalCount", "total"):
        try:
            value = int(data.get(key))
            if value >= 0:
                return value
        except Exception:
            pass
    return None
def _fetch_sold_with_names(cookies: dict) -> tuple:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
    }
    sold = {}
    sold_names = {}
    rate_map = _load_rate_map()
    start = 0
    total_count = None
    for _ in range(HISTORY_MAX_PAGES):
        params = {"query": "", "start": start, "count": HISTORY_PAGE_SIZE, "contextid": 2, "appid": 730}
        r = requests.get(MYHISTORY_RENDER_URL, params=params, headers=headers, cookies=cookies, verify=False, timeout=TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        data = r.json() if r.text else {}
        if not data.get("success"):
            raise RuntimeError(data.get("message", "请求失败"))
        if total_count is None:
            total_count = _history_total_count(data)
        page_sold, page_names, row_count = _parse_sold_history_page(data, rate_map)
        sold.update(page_sold)
        sold_names.update(page_names)
        if row_count <= 0:
            break
        start += HISTORY_PAGE_SIZE
        if total_count is not None and start >= total_count:
            break
        if total_count is None and row_count < HISTORY_PAGE_SIZE:
            break
    return sold, sold_names
def _norm_name(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    s = unicodedata.normalize("NFC", s)
    s = s.replace("(Factory New)", "(FN)").replace("(Minimal Wear)", "(MW)")
    s = s.replace("(Field-Tested)", "(FT)").replace("(Well-Worn)", "(WW)").replace("(Battle-Scarred)", "(BS)")
    return s.strip()
def _build_merged(inv_items: list, sold_map: dict, sold_names: dict, listing_assetids: set, listing_name_by_assetid: dict) -> dict:
    name_to_candidates = defaultdict(list)
    for it in inv_items or []:
        aid = str(it.get("assetid") or "").strip()
        name = _norm_name(it.get("market_hash_name") or it.get("name"))
        if not aid:
            continue
        if name:
            name_to_candidates[name].append({"assetid": aid, "source": "inventory", "sale_price": None})
    for aid, price in (sold_map or {}).items():
        aid = str(aid).strip()
        name = _norm_name((sold_names or {}).get(aid, ""))
        if not aid:
            continue
        name_to_candidates[name].append({"assetid": aid, "source": "sold", "sale_price": price})
    for aid in listing_assetids or set():
        aid = str(aid).strip()
        name = _norm_name((listing_name_by_assetid or {}).get(aid, ""))
        if not aid:
            continue
        name_to_candidates[name].append({"assetid": aid, "source": "listing", "sale_price": None})
    for name in name_to_candidates:
        name_to_candidates[name].sort(key=lambda x: (0 if x["source"] == "sold" else 1 if x["source"] == "listing" else 2, x["assetid"]))
    return name_to_candidates
def _record_name_counts(purchases: list) -> dict:
    out = defaultdict(int)
    for p in purchases:
        name = _norm_name(p.get("name") or "")
        if name:
            out[name] += 1
    return out
def _source_order(source: str, order: tuple) -> int:
    try:
        return order.index(source)
    except ValueError:
        return len(order) + 1
def _pick_candidate(name_to_candidates: dict, name: str, used_assetids: set, source_order: tuple = ("listing", "inventory", "sold")) -> dict:
    candidates = name_to_candidates.get(name) or []
    ordered = sorted(candidates, key=lambda x: (_source_order(x.get("source"), source_order), str(x.get("assetid") or "")))
    for c in ordered:
        if c["assetid"] not in used_assetids:
            return c
    return None
def _has_sale_price(purchase: dict) -> bool:
    try:
        return purchase.get("sale_price") is not None and float(purchase.get("sale_price") or 0) > 0
    except (TypeError, ValueError):
        return False
def _candidate_priority(candidate: dict) -> int:
    return {"sold": 0, "listing": 1, "inventory": 2}.get(candidate.get("source"), 9)
def _candidate_by_assetid(name_to_candidates: dict) -> dict:
    by_assetid = {}
    all_candidates = []
    for candidates in (name_to_candidates or {}).values():
        all_candidates.extend(candidates or [])
    for candidate in sorted(all_candidates, key=lambda c: (_candidate_priority(c), str(c.get("assetid") or ""))):
        aid = str(candidate.get("assetid") or "").strip()
        if aid and aid not in by_assetid:
            by_assetid[aid] = candidate
    return by_assetid
def _apply_candidate(purchase: dict, c: dict, sold_at: float) -> None:
    existing_sold_at = purchase.get("sold_at")
    purchase["assetid"] = c["assetid"]
    if c["source"] == "sold":
        purchase["sale_price"] = c["sale_price"]
        purchase["sold_at"] = existing_sold_at if existing_sold_at is not None else sold_at
        purchase["listing"] = False
        purchase["listing_status"] = None
        purchase["pending_receipt"] = False
    elif c["source"] == "listing":
        purchase["listing"] = True
        purchase["listing_status"] = None
        purchase["sale_price"] = None
        purchase["sold_at"] = None
        purchase["pending_receipt"] = False
    else:
        purchase["listing"] = False
        purchase["listing_status"] = None
        purchase["sale_price"] = None
        purchase["sold_at"] = None
        purchase["pending_receipt"] = False
def _reset_unresolved_active_record(purchase: dict) -> None:
    if _has_sale_price(purchase):
        purchase["listing"] = False
        purchase["listing_status"] = None
        purchase["pending_receipt"] = False
        return
    if purchase.get("pending_receipt"):
        return
    purchase["assetid"] = None
    purchase["listing"] = False
    purchase["listing_status"] = "error"
    purchase["sale_price"] = None
    purchase["sold_at"] = None
def _rebuild_records(purchases: list, name_to_candidates: dict, sold_at: float) -> tuple:
    before_records = [dict(p) for p in purchases]
    by_assetid = _candidate_by_assetid(name_to_candidates)
    matched_indexes = set()
    used_assetids = set()
    matched = 0
    for i, p in enumerate(purchases):
        original_assetid = str(p.get("assetid") or "").strip()
        if not original_assetid:
            continue
        c = by_assetid.get(original_assetid)
        if not c or c["assetid"] in used_assetids:
            continue
        _apply_candidate(p, c, sold_at)
        used_assetids.add(c["assetid"])
        matched_indexes.add(i)
        matched += 1
    for i, p in enumerate(purchases):
        if i in matched_indexes or _has_sale_price(p) or p.get("pending_receipt"):
            continue
        name = _norm_name(p.get("name") or "")
        if not name:
            continue
        c = _pick_candidate(name_to_candidates, name, used_assetids)
        if not c:
            continue
        _apply_candidate(p, c, sold_at)
        used_assetids.add(c["assetid"])
        matched_indexes.add(i)
        matched += 1
    preserved_sold = 0
    unresolved = 0
    for i, p in enumerate(purchases):
        if i in matched_indexes:
            continue
        if _has_sale_price(p):
            p["listing"] = False
            p["listing_status"] = None
            p["pending_receipt"] = False
            preserved_sold += 1
            continue
        if p.get("pending_receipt"):
            unresolved += 1
            continue
        before_assetid = str(p.get("assetid") or "").strip()
        _reset_unresolved_active_record(p)
        if before_assetid or str(p.get("listing_status") or "").lower() == "error":
            unresolved += 1
    changed = sum(1 for before, after in zip(before_records, purchases) if before != after)
    return matched, unresolved, len(purchases), preserved_sold, changed
def run(log_fn=None):
    if not CREDENTIALS_FILE.exists():
        err = f"未找到 {CREDENTIALS_FILE}"
        if log_fn:
            log_fn(err, "error")
        return False, {"error": err}
    with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
        cred = json.load(f)
    steam = cred.get("steam") or {}
    cookies_str = steam.get("cookies") or ""
    if not cookies_str:
        return False, {"error": "credentials 中无 steam.cookies"}
    c = _cookies_to_dict(cookies_str)
    if not c.get("steamLoginSecure"):
        return False, {"error": "Cookie 中无 steamLoginSecure"}
    from app.state import get_purchases, get_sales, replace_transactions
    purchases = list(get_purchases() or [])
    sales = list(get_sales() or [])
    repair_total = len(purchases)
    if log_fn:
        log_fn(f"开始全量重建操作记录，共 {repair_total} 条", "info")
    if repair_total <= 0:
        return True, {"filled": 0, "missing": 0, "total": 0, "list_by_name": {}}
    inv_items = []
    if log_fn:
        log_fn("正在拉取 CS2 库存…", "info")
    try:
        from app.inventory_cs2 import scan_cs2_inventory
        ok, inv_items, err = scan_cs2_inventory()
        if not ok and log_fn:
            log_fn(f"拉取库存: {err}", "warn")
    except Exception as e:
        if log_fn:
            log_fn(f"拉取库存异常: {e}", "warn")
    if log_fn:
        log_fn("正在拉取 Steam 市场历史 Sold 记录…", "info")
    try:
        sold_map, sold_names = _fetch_sold_with_names(c)
        if log_fn:
            log_fn(f"解析到售出 {len(sold_map)} 条", "info")
    except Exception as e:
        if log_fn:
            log_fn(f"拉取售出历史异常: {e}，将只使用库存/在售列表重建，并保留已售记录", "warn")
        sold_map, sold_names = {}, {}
    if log_fn:
        log_fn("正在拉取出售中列表…", "info")
    listing_assetids = set()
    listing_name_by_assetid = {}
    try:
        from app.steam_listings import fetch_my_listings
        ok, listing_assetids, err, listing_name_by_assetid = fetch_my_listings(c, debug_fn=None)
        if ok:
            if log_fn:
                log_fn(f"在售 {len(listing_assetids)} 条", "info")
        elif log_fn:
            log_fn(f"拉取在售列表: {err}", "warn")
    except Exception as e:
        if log_fn:
            log_fn(f"拉取在售列表异常: {e}", "warn")
    name_to_candidates = _build_merged(inv_items, sold_map, sold_names, listing_assetids, listing_name_by_assetid)
    record_name_counts = _record_name_counts(purchases)
    list_by_name = {name: len(name_to_candidates.get(name) or []) for name in record_name_counts.keys()}
    list_total = sum(list_by_name.values())
    if log_fn:
        log_fn(f"按操作记录名称统计候选条数 {list_total}，操作记录数 {repair_total}", "info")
    sold_at = time.time()
    filled, missing, repair_total, preserved_sold, changed = _rebuild_records(purchases, name_to_candidates, sold_at)
    if log_fn:
        log_fn(f"全量重建确认 {filled} 条，保留已售 {preserved_sold} 条，变更 {changed} 条", "info")
        if missing:
            log_fn(f"仍有 {missing} 条记录未能从库存/在售/售出历史确认，已标记为待处理或保留待收货状态", "warn")
    if changed > 0:
        replace_transactions(purchases, sales)
        if log_fn:
            log_fn("已将重建后的交易记录保存到数据库", "info")
    elif log_fn:
        log_fn("重建结果与当前数据库一致，数据库未改动", "info")
    if log_fn:
        log_fn("--- 列表（仅操作记录中有的名称）各饰品数量 ---", "info")
        total_items = 0
        for name in sorted(list_by_name.keys()):
            cnt = list_by_name[name]
            total_items += cnt
            log_fn(f"  {name}: {cnt} 个", "info")
        log_fn(f"列表合计: {total_items} 个，不同饰品: {len(list_by_name)} 种", "info")
    return True, {
        "filled": filled,
        "missing": missing,
        "total": repair_total,
        "preserved_sold": preserved_sold,
        "changed": changed,
        "list_by_name": list_by_name,
    }
def main():
    def log(msg, level="info"):
        print(f"[{level}] {msg}")
    ok, result = run(log_fn=log)
    if not ok:
        print("修复失败:", result.get("error", ""))
        sys.exit(1)
    filled = result.get("filled", 0)
    missing = result.get("missing", 0)
    total = result.get("total", 0)
    print(f"完成. 重建确认 {filled}/{total} 条，状态已更新（持有中/已出售/出售中）.")
    if missing:
        print(f"仍有 {missing} 条未解决，请检查名称是否与列表一致或 Steam 列表是否拉全（库存/售出历史/在售）. ")
if __name__ == "__main__":
    main()
