import json
import re
import threading
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Optional

from app.accounts import get_current_account
from app.config_loader import get_steam_credentials, load_app_config_validated
from app.config_schema import DEFAULTS, merge
from app.inventory_cs2 import scan_cs2_inventory
from app.pipeline_context import PipelineContext
from app.services.account_region import refresh_account_region_currency
from app.strategy_engine import apply_strategy_to_config, evaluate_strategy_runtime_modules
from app.state import get_state, append_sale
from app.steam_confirm import auto_confirm_once
from app.steam_listings import fetch_my_listings
from steam.market import list_item, parse_sell_response
from steam.market_orders import compute_smart_list_price, get_sell_orders_cny
from steam.request_policy import MarketCooldown
from steam.session import create_market_session
from utils.delay import jittered_sleep
from utils.money import USD_TO_CNY_DEFAULT, list_price_display_to_cents
from utils.time import parse_steam_history_date
from utils.trend import calculate_trend_robust

_sell_phase_lock = threading.Lock()
_listing_cooldown = MarketCooldown()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _steam_latest_price_and_trend(market_hash_name: str, trend_days: int = 7):
    from app.services.steam_client import SteamClient
    from utils.money import apply_currency
    client = SteamClient()
    raw = client.fetch_history(market_hash_name, app_id=730, return_currency=True)
    if not raw or not isinstance(raw, dict):
        return None, None, None
    history = raw.get("history")
    currency = raw.get("currency")
    if not history:
        return None, None, None
    parsed = []
    for entry in history:
        if len(entry) < 2:
            continue
        dt = parse_steam_history_date(str(entry[0]))
        if dt is None:
            continue
        try:
            p = float(entry[1])
        except (ValueError, TypeError):
            continue
        parsed.append((dt, p))
    if not parsed:
        return None, None, None
    prices = [p for (_, p) in parsed]
    prices_cny, _ = apply_currency(prices, currency, USD_TO_CNY_DEFAULT)
    if not prices_cny:
        return None, None, None
    parsed_cny = list(zip([x[0] for x in parsed], prices_cny))
    parsed_cny.sort(key=lambda x: x[0])
    latest_price = parsed_cny[-1][1]
    newest_dt = parsed_cny[-1][0]
    cutoff = newest_dt - timedelta(days=trend_days)
    in_range = [(dt, p) for dt, p in parsed_cny if dt >= cutoff]
    prices_in_range = [p for (_, p) in in_range]
    trend = calculate_trend_robust(prices_in_range, use_dynamic_sensitivity=True) if len(prices_in_range) >= 3 else 0
    return latest_price, trend, prices_in_range


def _load_rate_map() -> dict:
    """Load exchange rate JSON from config dir. Returns an empty dict on any failure."""
    try:
        fx_file = Path(__file__).resolve().parent.parent / "config" / "exchange_rate.json"
        if fx_file.exists():
            with open(fx_file, "r", encoding="utf-8") as f:
                fx = json.load(f)
            if isinstance(fx, dict) and isinstance(fx.get("rates"), dict):
                return {k: float(v) for k, v in fx["rates"].items() if isinstance(v, (int, float))}
    except Exception:
        pass
    return {}


def _record_listing_success(ctx, aid: str, name: str, list_price: float, listing_delay: float) -> None:
    """Append sale record, mark purchase as listed, and sleep the listing delay."""
    append_sale({"name": name, "goods_id": 0, "price": list_price, "at": time.time(), "assetid": aid or ""})
    if aid:
        purchases = ctx.state.get_purchases()
        for i, p in enumerate(purchases):
            if str(p.get("assetid") or "") == aid:
                db_id = p.get("_db_id")
                if db_id:
                    ctx.state.update_purchase_by_id(db_id, {"listing": True})
                else:
                    ctx.state.update_purchase(i, {"listing": True})
                break
    jittered_sleep(listing_delay)


# ---------------------------------------------------------------------------
# Phase sub-functions
# ---------------------------------------------------------------------------

def _resolve_steam_session(ctx: PipelineContext, cred_steam: dict):
    """Validate credentials and create a Steam market session.

    Returns ``(session, session_id_effective)`` or ``None`` if setup fails.
    """
    steam_id = cred_steam.get("steam_id")
    session_id = cred_steam.get("session_id")
    cookies = cred_steam.get("cookies")
    if not steam_id or not session_id or not cookies:
        ctx.log("未配置 Steam steam_id / session_id / cookies，跳过出售阶段", "warn", category="steam")
        return None
    session = create_market_session(cookies, steam_id)
    session_id_effective = session.cookies.get("sessionid") or session_id
    if not session_id_effective:
        ctx.log("Cookie 中无 sessionid，无法上架", "warn", category="steam")
        return None
    return session, session_id_effective


def _get_inventory(ctx: PipelineContext, items: Optional[list]) -> Optional[list]:
    """Return the inventory item list, scanning from Steam if *items* is ``None``.

    Attempts auto-relogin once on auth expiry.  Returns ``None`` when the
    inventory cannot be obtained and the sell phase should abort.
    """
    if items is not None:
        return items
    ok, items, err = scan_cs2_inventory()
    if not ok and err and ("登录已过期" in err or "重新登录" in err):
        ctx.log("Steam 登录已过期，尝试自动重新登录…", "warn", category="steam")
        try:
            from app.services.steam_auth import try_steam_auto_relogin
            relogin_ok, _, relogin_msg = try_steam_auto_relogin()
            if relogin_ok:
                ctx.log(f"自动重新登录成功: {relogin_msg}，重新获取库存", "info", category="steam")
                jittered_sleep(2, jitter_ratio=0.2)
                ok, items, err = scan_cs2_inventory()
            else:
                ctx.log(f"自动重新登录失败: {relogin_msg}", "warn", category="steam")
        except Exception as e:
            ctx.log(f"自动重新登录异常: {type(e).__name__} - {e}", "error", category="steam")
    if not ok:
        ctx.log(f"获取库存失败: {err}，跳过", "warn", category="steam")
        return None
    return items


def _build_listing_plan(
    ctx: PipelineContext,
    cfg: dict,
    session,
    sellable: list,
    sell_strategy: int,
    pipeline_cfg: dict,
    purchases_snapshot: list,
    ok_listings: bool,
    active_listing_ids: set,
    listing_assetid_to_name: dict,
    assetid_to_name_map: dict,
    account_currency: str,
    rate_map: dict,
) -> list:
    """Decide which sellable items to actually list and at what price.

    Returns a list of dicts ready for ``_submit_listings``.
    """
    wall_volume = int(pipeline_cfg.get("sell_price_wall_volume", 20))
    max_ignore = int(pipeline_cfg.get("sell_price_max_ignore_volume", 4))
    max_per_item = max(1, int(pipeline_cfg.get("max_listings_per_item", 5) or 5))
    sell_offset = float(pipeline_cfg.get("sell_price_offset", 0))
    trend_days = int(pipeline_cfg.get("sell_trend_days", 7))

    to_list_by_name: dict = defaultdict(int)
    skip_same_name_cap: dict = defaultdict(int)
    to_list = []
    seen_assetids: set = set()

    for it in sellable:
        if ctx.is_stop_requested():
            ctx.set_status("stopped", "已停止")
            return to_list

        name = it.get("name") or ""
        market_hash_name = (it.get("market_hash_name") or name).strip()
        aid = str(it.get("assetid", "")).strip()

        if aid in seen_assetids:
            continue
        seen_assetids.add(aid)
        if not market_hash_name:
            ctx.log(f"[出售] 跳过无名物品 assetid={aid}", "info", category="steam")
            continue
        
        buy_record = _find_buy_record(purchases_snapshot, aid, market_hash_name)
        if not buy_record:
            ctx.log(f"[出售] {name} assetid={aid} 不在本地购买记录中（非倒余额库>存），为保护个人物品跳过出售", "info", category="steam")
            continue

        # Same-name in-steam cap check
        if ok_listings and listing_assetid_to_name:
            steam_same_name = sum(
                1 for lid in active_listing_ids
                if (listing_assetid_to_name.get(lid) or "").strip() == market_hash_name
            )
        elif ok_listings:
            steam_same_name = sum(
                1 for lid in active_listing_ids
                if (assetid_to_name_map.get(lid) or "").strip() == market_hash_name
            )
        else:
            steam_same_name = sum(
                1 for p in purchases_snapshot
                if p.get("listing") and ((p.get("market_hash_name") or p.get("name") or "").strip() == market_hash_name)
            )

        already_in_this_round = to_list_by_name.get(market_hash_name, 0)
        if steam_same_name + already_in_this_round >= max_per_item:
            skip_same_name_cap[market_hash_name] += 1
            continue

        try:
            orders_result = get_sell_orders_cny(
                session,
                market_hash_name,
                app_id=int(it.get("appid", 730)),
                return_error=True,
            )
            if isinstance(orders_result, tuple) and len(orders_result) == 2:
                orders_data, orders_error = orders_result
            else:
                orders_data, orders_error = orders_result, None
        except Exception as e:
            ctx.log(f"[出售] {name} assetid={aid} 拉取卖单异常: {type(e).__name__} - {e}", "error", category="steam")
            continue
        if not orders_data or not orders_data.get("sell_orders"):
            ctx.log(f"[出售] {name} assetid={aid} 无法获取 Steam 卖单：{orders_error or '未知原因'}，跳过", "warn", category="steam")
            continue

        list_price, reason = compute_smart_list_price(
            orders_data["sell_orders"],
            wall_volume_threshold=wall_volume,
            max_ignore_volume=max_ignore,
            offset=sell_offset,
        )
        if list_price is None or list_price <= 0:
            ctx.log(f"[出售] {name} assetid={aid} 无法计算定价({reason})，跳过", "warn", category="steam")
            continue
        list_price = round(float(list_price), 2)
        trend = None
        profit_output = {}

        # Currency conversion for display
        display_price = list_price
        if account_currency != "CNY":
            rate = rate_map.get(account_currency)
            if not rate:
                # 汇率缺失时直接终止整批上架，避免以CNY数值直接提交非人民币账号造成大额亏损
                ctx.log(
                    f"[出售] 账号币种={account_currency}，但汇率文件缺少该币种数据，"
                    "无法安全定价，终止本次出售以防价格错误（可等汇率更新后重试）",
                    "error", category="steam",
                )
                return []
            display_price = round(list_price / rate, 2)
            ctx.debug(
                f"[出售] 账号币种={account_currency}, CNY={list_price:.2f} -> {account_currency}={display_price:.2f}",
                category="steam",
            )

        # Sell strategy 2/3: skip if rising trend
        if sell_strategy in (2, 3):
            _, trend, _ = _steam_latest_price_and_trend(market_hash_name, trend_days=trend_days)
            if trend is not None and trend > 0:
                ctx.log(f"[出售] {name} assetid={aid} 近{trend_days}天上升趋势，等待", "info", category="steam")
                continue

        # Sell strategy 3: ratio guard
        if sell_strategy == 3:
            buy_record = _find_buy_record(purchases_snapshot, aid, market_hash_name)
            if buy_record:
                buy_price = float(buy_record.get("price") or 0)
                market_price_at_buy = float(buy_record.get("market_price") or 0)
                if buy_price > 0 and market_price_at_buy > 0 and list_price > 0:
                    current_ratio = buy_price / (list_price / 1.15)
                    original_ratio = buy_price / (market_price_at_buy / 1.15)
                    ratio_multiplier = float(pipeline_cfg.get("profit_ratio_multiplier", 1.05) or 1.05)
                    ratio_limit = original_ratio * ratio_multiplier
                    profit_output = {
                        "current_ratio": current_ratio,
                        "original_ratio": original_ratio,
                        "ratio_limit": ratio_limit,
                    }
                    if current_ratio > ratio_limit:
                        ctx.log(
                            f"[出售] 策略3不满足: {name} 当前买入/挂刀价比例({current_ratio:.4f}) "
                            f"高于 购入时买入/市场底价比例的{ratio_multiplier:.2f}倍({ratio_limit:.4f})，避免过低价格售出，跳过",
                            "info", category="steam",
                        )
                        continue
                    ctx.log(
                        f"[出售] 策略3满足: {name} 当前买入/挂刀价比例({current_ratio:.4f}) <= {ratio_limit:.4f}，允许出售",
                        "info", category="steam",
                    )

        custom_outputs = {
            "guard.max_listings_per_item": {
                "steam_same_name": steam_same_name,
                "round_same_name": already_in_this_round,
                "max_per_item": max_per_item,
            },
            "pricing.steam_wall_price": {
                "list_price": list_price,
                "reason": reason,
                "order_count": len(orders_data.get("sell_orders") or []),
            },
            "pricing.steam_wall_gap": {
                "list_price": list_price,
                "reason": reason,
                "order_count": len(orders_data.get("sell_orders") or []),
            },
            "pricing.price_offset": {"sell_price_offset": sell_offset},
            "guard.rising_trend_wait": {"trend": trend, "trend_days": trend_days},
            "guard.profit_ratio": profit_output,
        }
        custom_context = {
            "item": it,
            "buy_record": buy_record or {},
            "listing": {"list_price": list_price, "display_price": display_price},
            "config": cfg,
        }
        custom_results, blocking = evaluate_strategy_runtime_modules(
            cfg,
            "sell",
            "sell.listing_guard",
            context=custom_context,
            outputs=custom_outputs,
        )
        for result in custom_results:
            level = "warn" if result.get("status") in {"reject", "error"} else "info"
            ctx.log(
                f"[策略模块] {name} assetid={aid} {result.get('module_name')}: "
                f"{result.get('reason')} ({result.get('status')})",
                level,
                category="steam",
            )
        if blocking:
            continue

        price_cents = list_price_display_to_cents(display_price, account_currency)
        to_list_by_name[market_hash_name] += 1
        n_this_name = to_list_by_name[market_hash_name]
        ctx.log(
            f"[出售] 列入待上架 assetid={aid} {name} 价格={list_price:.2f}"
            f"（该同名 Steam 在售 {steam_same_name}，本轮回第 {n_this_name} 件，上限 {max_per_item}）",
            "info", category="steam",
        )
        to_list.append({
            "it": it, "list_price": list_price, "reason": reason,
            "price_cents": price_cents, "market_hash_name": market_hash_name,
            "name": name, "aid": aid,
        })

    if skip_same_name_cap:
        total_skip = sum(skip_same_name_cap.values())
        parts = [f"{n} x{c}" for n, c in sorted(skip_same_name_cap.items(), key=lambda x: -x[1])[:5]]
        if len(skip_same_name_cap) > 5:
            parts.append(f"等共 {len(skip_same_name_cap)} 种")
        ctx.log(f"[出售] 同名在售已达上限跳过 共 {total_skip} 件（{', '.join(parts)}）", "info", category="steam")

    return to_list


def _find_buy_record(purchases_snapshot: list, aid: str, market_hash_name: str) -> Optional[dict]:
    """Return the current purchase record that owns *aid*.

    A market name is not an ownership identifier: the inventory may contain a
    personal drop or a later acquisition with the same name as an old purchase.
    Automatic listing therefore fails closed unless a current, exact asset-id
    record exists.
    """
    aid = str(aid or "").strip()
    if not aid:
        return None

    for p in purchases_snapshot:
        if str(p.get("assetid") or "").strip() != aid:
            continue

        # Do not let stale/finished records authorize another listing.  These
        # checks also protect against a stale inventory snapshot immediately
        # after a listing or sale.
        sale_price = p.get("sale_price")
        if sale_price is not None:
            try:
                if float(sale_price) > 0:
                    continue
            except (TypeError, ValueError):
                continue
        if p.get("sold_at") not in (None, "", 0, 0.0):
            continue
        if p.get("pending_receipt") or p.get("listing"):
            continue
        return p
    return None


def _listing_response_body_preview(value: object, limit: int = 160) -> str:
    """Return a compact, bounded response excerpt suitable for logs."""
    if value is None:
        return "<empty>"
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    source_was_truncated = len(text) > max(limit * 4, 640)
    if source_was_truncated:
        text = text[: max(limit * 4, 640)]
    text = " ".join(text.split())
    if not text:
        return "<empty>"
    text = re.sub(
        r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]+",
        r"\1 ***",
        text,
    )
    text = re.sub(
        (
            r"(?i)\b("
            r"steamloginsecure|sessionid|access[_-]?token|"
            r"refresh[_-]?token|token|cookie"
            r")(\s*[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;&}<]+)"
        ),
        r"\1\2***",
        text,
    )
    if source_was_truncated or len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _parse_listing_response(out: object) -> tuple[Optional[dict], str]:
    """Validate a low-level listing response and return diagnostics on failure."""
    if out is None:
        return None, "无响应"
    if not isinstance(out, dict):
        return None, f"返回类型异常: {type(out).__name__}"

    status_code = out.get("status_code")
    status_label = str(status_code) if status_code is not None else "unknown"
    raw_text = out.get("text")
    _, data = parse_sell_response(raw_text)
    if data is None:
        return (
            None,
            f"HTTP {status_label}; body={_listing_response_body_preview(raw_text)}",
        )
    return data, ""


def _listing_response_message(data: dict, limit: int = 80) -> str:
    value = data.get("message")
    if value in (None, ""):
        return ""
    return _listing_response_body_preview(value, limit=limit)


def _listing_response_is_success(out: object, data: dict, message: str) -> bool:
    """Accept success only from the expected Steam HTTP response contract."""
    if not isinstance(out, dict) or out.get("status_code") != 200:
        return False
    message_lower = message.lower()
    return (
        data.get("success") is True
        or "pending confirmation" in message_lower
        or "already have a listing" in message_lower
    )


def _listing_rate_limited(ctx: PipelineContext, out: object) -> bool:
    if not isinstance(out, dict) or out.get("status_code") != 429:
        return False
    remaining = _listing_cooldown.defer(out.get("retry_after"))
    preview = _listing_response_body_preview(out.get("text"))
    ctx.log(
        f"[出售] 上架接口 HTTP 429; body={preview}，冷却约 {remaining:.0f}s，"
        "停止本批次，剩余物品未提交，不自动重试上架",
        "warn", category="steam",
    )
    return True


def _submit_listings(
    ctx: PipelineContext,
    to_list: list,
    session,
    session_id_effective: str,
    listing_delay: float,
) -> int:
    """POST listing requests to Steam and handle retries.

    Returns the count of successfully submitted listings.
    """
    remaining = _listing_cooldown.remaining()
    if remaining > 0:
        ctx.log(f"[出售] 上架接口冷却中，约 {remaining:.0f}s 后可重试，本批次未提交", "warn", category="steam")
        return 0
    listed = 0
    for entry in to_list:
        if ctx.is_stop_requested():
            ctx.set_status("stopped", "已停止")
            return listed

        it = entry["it"]
        list_price = entry["list_price"]
        reason = entry["reason"]
        price_cents = entry["price_cents"]
        name = entry["name"]
        aid = entry["aid"]

        ctx.log(f"[出售] 上架请求 {name} assetid={aid} 价格={list_price:.2f} ({reason})", "info", category="steam")

        def _do_list():
            return list_item(
                session, session_id_effective,
                int(it.get("appid", 730)),
                str(it.get("contextid") or "2"),
                str(it.get("assetid", "")),
                price_cents,
            )

        try:
            out = _do_list()
            if _listing_rate_limited(ctx, out):
                break
            data, response_error = _parse_listing_response(out)
            if data is None:
                ctx.log(
                    f"[出售] {name} assetid={aid} 上架响应异常: {response_error}",
                    "warn",
                    category="steam",
                )
                jittered_sleep(listing_delay)
                continue

            msg = _listing_response_message(data)
            msg_lower = msg.lower()

            if _listing_response_is_success(out, data, msg):
                listed += 1
                ctx.log(f"[出售] 已上架 assetid={aid} {name} 价格={list_price:.2f} ({reason})", "info", category="steam")
                _record_listing_success(ctx, aid, name, list_price, listing_delay)
                continue

            if "previous action completes" in msg_lower or "until your previous" in msg_lower:
                ctx.log(f"[出售] {name} assetid={aid} 前一操作未完成，等待 5s 后重试", "info", category="steam")
                jittered_sleep(5)
                out2 = _do_list()
                if _listing_rate_limited(ctx, out2):
                    break
                data2, retry_error = _parse_listing_response(out2)
                if data2 is None:
                    ctx.log(
                        f"[出售] {name} assetid={aid} 上架重试响应异常: {retry_error}",
                        "warn",
                        category="steam",
                    )
                else:
                    msg2 = _listing_response_message(data2).lower()
                    if _listing_response_is_success(out2, data2, msg2):
                        listed += 1
                        ctx.log(f"[出售] 已上架 assetid={aid} {name} 价格={list_price:.2f} ({reason}) [重试成功]", "info", category="steam")
                        _record_listing_success(ctx, aid, name, list_price, listing_delay)
                        continue

            response_preview = (
                _listing_response_body_preview(out.get("text"))
                if isinstance(out, dict)
                else type(out).__name__
            )
            ctx.log(f"[出售] 上架失败 assetid={aid} {name}: {msg or response_preview}", "warn", category="steam")
        except Exception as ex:
            error_detail = _listing_response_body_preview(ex)
            ctx.log(f"[出售] 上架时发生未捕获异常 assetid={aid} {name}: {type(ex).__name__} - {error_detail}", "error", category="steam")

        jittered_sleep(listing_delay)

    return listed


def _auto_confirm_listings(ctx: PipelineContext, cfg: dict, steam_id: str, cookies: str) -> None:
    """Confirm pending Steam Guard confirmations after listing, if configured."""
    steam_confirm_cfg = cfg.get("steam_confirm") or {}
    if not bool(steam_confirm_cfg.get("enabled")):
        return
    identity_secret = (steam_confirm_cfg.get("identity_secret") or "").strip()
    device_id = (steam_confirm_cfg.get("device_id") or "").strip()
    if not identity_secret or not device_id:
        ctx.log("[确认] 已开启自动确认，但 identity_secret/device_id 未配置，跳过", "warn", category="steam")
        return
    jittered_sleep(2)
    ctx.log("[确认] 正在检查待确认列表…", "info", category="steam")
    okc, n, errc = auto_confirm_once(
        identity_secret=identity_secret,
        device_id=device_id,
        steam_id=str(steam_id),
        cookies=str(cookies),
    )
    if okc:
        ctx.log(f"[确认] 已自动确认 {n} 项", "info", category="steam")
    else:
        ctx.log(f"[确认] 自动确认失败: {errc}", "warn", category="steam")


# ---------------------------------------------------------------------------
# Sell phase orchestrator
# ---------------------------------------------------------------------------

def _run_sell_phase_impl(cfg: dict, state, flow_id: str, items: Optional[list] = None) -> None:
    cfg = apply_strategy_to_config(cfg, "sell")
    pipeline_cfg = cfg.get("pipeline", {})
    verbose = bool(pipeline_cfg.get("verbose_debug", False))
    ctx = PipelineContext(state, flow_id, verbose=verbose)
    sell_strategy = int(pipeline_cfg.get("sell_strategy", 1))

    if sell_strategy == 4:
        ctx.log("策略4 暂停自动出售，跳过", "info")
        return

    cred_steam = get_steam_credentials()
    session_result = _resolve_steam_session(ctx, cred_steam)
    if session_result is None:
        return
    session, session_id_effective = session_result

    items = _get_inventory(ctx, items)
    if items is None:
        return

    sellable = [it for it in items if it.get("can_sell")]
    if not sellable:
        ctx.log("当前无可出售物品", "info", category="steam")
        return

    listing_delay = max(1, int(pipeline_cfg.get("listing_delay_seconds", 3) or 3))
    steam_debug = bool(pipeline_cfg.get("verbose_debug") or pipeline_cfg.get("steam_listings_debug"))
    debug_fn = (lambda m: ctx.log(m, "debug", category="steam")) if steam_debug else None

    ok_listings, active_listing_ids, err_listings, listing_assetid_to_name = fetch_my_listings(
        cred_steam.get("cookies"), debug_fn=debug_fn
    )
    if ok_listings:
        ctx.log(f"[出售] Steam 在售列表拉取成功，共 {len(active_listing_ids)} 个在售", "info", category="steam")
    else:
        ctx.log(f"[出售] Steam 在售列表拉取失败: {err_listings or '未知'}，同名在售校验将按本地购买记录", "warn", category="steam")

    purchases_snapshot = ctx.state.get_purchases()
    account = get_current_account()
    if account is None:
        # 工厂重置后账号列表为空，直接跳过出售避免以错误币种上架
        ctx.log("[出售] 无有效账号（可能刚执行了出厂重置），跳过本次出售", "warn", category="steam")
        return
    account_currency_cached = (account.get("currency_code") or "").strip().upper()
    region_check = refresh_account_region_currency(
        account.get("id"),
        cookies_raw=cred_steam.get("cookies", ""),
    )
    if not region_check.get("ok"):
        ctx.log(
            "[出售] 无法实时确认 Steam 账号结算币种，已拒绝上架，"
            f"防止跨区价格误卖。原因: {region_check.get('error') or '未知原因'}",
            "error",
            category="steam",
        )
        return
    account_currency = (region_check.get("currency_code") or "").strip().upper()
    account_region = (region_check.get("region_code") or "").strip().upper()
    if not account_currency:
        ctx.log(
            "[出售] Steam 未返回结算币种，已拒绝上架，防止跨区价格误卖。",
            "error",
            category="steam",
        )
        return
    if account_currency_cached and account_currency_cached != account_currency:
        ctx.log(
            f"[出售] 检测到账号币种变化: 缓存={account_currency_cached} 实时={account_currency}，"
            "已更新并使用实时币种定价。",
            "warn",
            category="steam",
        )
    if account_region:
        ctx.debug(
            f"[出售] 账号地区按结算币种派生: 币种={account_currency} 地区={account_region}",
            category="steam",
        )
    rate_map = _load_rate_map()

    assetid_to_name_map = {
        str(p.get("assetid") or "").strip(): (p.get("market_hash_name") or p.get("name") or "").strip()
        for p in purchases_snapshot if str(p.get("assetid") or "").strip()
    }

    ctx.log(
        f"策略{sell_strategy}，可出售 {len(sellable)} 件"
        f"（智能定价：墙+断层；上架间隔={listing_delay}s）",
        "info", category="steam",
    )

    to_list = _build_listing_plan(
        ctx, cfg, session, sellable, sell_strategy, pipeline_cfg,
        purchases_snapshot, ok_listings, active_listing_ids,
        listing_assetid_to_name, assetid_to_name_map, account_currency, rate_map,
    )

    if not to_list:
        ctx.debug("[出售] 本轮回无需上架", category="steam")
        return

    ctx.log(f"[出售] 开始上架 {len(to_list)} 件", "info", category="steam")
    listed = _submit_listings(ctx, to_list, session, session_id_effective, listing_delay)

    if listed:
        ctx.log(f"[出售] 本轮回共上架 {listed} 件，等待下一轮", "info", category="steam")
        _auto_confirm_listings(ctx, cfg, cred_steam.get("steam_id", ""), cred_steam.get("cookies", ""))


def _run_sell_phase(cfg: dict, state, flow_id: str, items: Optional[list] = None) -> None:
    if not _sell_phase_lock.acquire(blocking=False):
        state.log("[出售] 已有出售任务在执行，本次跳过", "info", category="steam", flow_id=flow_id)
        return
    try:
        _run_sell_phase_impl(cfg, state, flow_id, items)
    finally:
        _sell_phase_lock.release()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_sell_phase_on_inventory_update(items: list) -> None:
    cfg = merge(DEFAULTS, load_app_config_validated())
    state = get_state()
    t = threading.Thread(
        target=_run_sell_phase,
        args=(cfg, state, "inventory"),
        kwargs={"items": items},
        daemon=True,
    )
    t.start()
