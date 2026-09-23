import re
import inspect
import statistics
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.services.iflow_client import fetch_iflow_rows as _fetch_iflow_rows
from app.state import get_purchases, set_status
from app.strategy_engine import evaluate_strategy_runtime_modules, is_strategy_module_enabled
from app.services.steam_client import SteamClient
from app.services.analysis_client import StabilityAnalyzer
from app.services.buff_client import count_lowest_price_orders, first_order_at_price
from app.services.buff_checkout_guard import (
    begin_checkout,
    buff_activity_guard,
    get_unresolved_checkout,
    resolve_checkout,
    update_checkout,
)
from app.services.buff_auth import get_buff_auth_lock
from app.notify import send_pushplus, build_payment_notify_content, wait_email_command
from utils.delay import jittered_sleep
from buff import (
    BuffAuthExpired,
    BuffRequestBlocked,
    BuffVerificationRequired,
    BuffWriteResultUnknown,
)

STEAM_FEE_FACTOR = 1.15  # Steam take rate for calculating net proceeds
BUFF_ORDERS_CACHE_TTL_SECONDS = 3.0
TIME_WINDOW_CLOSED = object()
CURRENCY_QUANTUM = Decimal("0.01")


def _currency_amount(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(
        CURRENCY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _affordable_quantity(
    target_balance: Any,
    accumulated: Any,
    unit_price: Any,
) -> int:
    price = _currency_amount(unit_price)
    if price <= 0:
        return 0
    remaining = _currency_amount(target_balance) - _currency_amount(accumulated)
    if remaining <= 0:
        return 0
    return max(0, int(remaining // price))


class _PurchaseAttemptStatus(Enum):
    SUCCESS = "success"
    SAFE_TO_FALLBACK = "safe_to_fallback"
    CREATED_NOT_PAID = "created_not_paid"
    UNKNOWN_AFTER_SEND = "unknown_after_send"
    COOLING_DOWN = "cooling_down"
    RISK = "risk"
    TIME_WINDOW_CLOSED = "time_window_closed"
    TARGET_REACHED = "target_reached"
    SKIP_NO_FAILED = "skip_no_failed"
    SKIP_VERIFICATION_FAILED = "skip_verification_failed"
    FAILED = "failed"


class PurchaseFlowHalted(RuntimeError):
    """A checkout outcome that forbids any subsequent purchase write."""

    def __init__(
        self,
        message: str,
        *,
        order_id: str = "",
        batch_id: str = "",
    ) -> None:
        super().__init__(message)
        self.order_id = order_id
        self.batch_id = batch_id


class PurchaseWriteResultUnknown(PurchaseFlowHalted):
    pass


class PurchaseOrderCreatedPending(PurchaseFlowHalted):
    pass


class PurchaseCoolingDown(PurchaseFlowHalted):
    pass


def _mark_committed(
    exc: BaseException,
    amount: float,
    *,
    orders: int = 1,
) -> BaseException:
    """Attach durable-commit metadata to a terminal post-commit failure."""

    exc.committed_amount = max(0.0, float(amount or 0.0))
    exc.committed_orders = max(0, int(orders or 0))
    return exc


def _validate_unique_batch_matches(
    matches: Any,
    batch_id: str,
) -> List[Dict[str, Any]]:
    """Reject malformed/duplicate IDs before treating a batch as complete."""

    valid: List[Dict[str, Any]] = []
    seen_bill_ids = set()
    seen_sell_ids = set()
    invalid = not isinstance(matches, list)
    for match in matches if isinstance(matches, list) else []:
        if not isinstance(match, dict):
            invalid = True
            continue
        bill_id = str(match.get("bill_order_id") or "").strip()
        sell_id = str(match.get("id") or "").strip()
        if (
            not bill_id
            or bill_id == "0"
            or not sell_id
            or sell_id == "0"
            or bill_id in seen_bill_ids
            or sell_id in seen_sell_ids
        ):
            invalid = True
            continue
        seen_bill_ids.add(bill_id)
        seen_sell_ids.add(sell_id)
        normalized = dict(match)
        normalized["bill_order_id"] = bill_id
        normalized["id"] = sell_id
        valid.append(normalized)

    if invalid or len(valid) != len(matches):
        error = BuffWriteResultUnknown(
            "BUFF 批量核销结果包含空值或重复订单号，无法确认完整件数",
            method="POST",
        )
        error.partial_results = valid
        error.batch_id = str(batch_id)
        raise error
    return valid


@dataclass(frozen=True)
class _PurchaseAttempt:
    status: _PurchaseAttemptStatus
    amount: Optional[float] = None
    reason: str = ""
    order_id: str = ""
    batch_id: str = ""


def _checkout_credential_identity(buff_client: Any) -> Dict[str, Any]:
    getter = getattr(buff_client, "get_credential_identity", None)
    if callable(getter):
        try:
            value = getter()
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    try:
        generation = int(getattr(buff_client, "_credential_generation", 0) or 0)
    except (TypeError, ValueError):
        generation = 0
    return {
        "credential_generation": generation,
        "credential_fingerprint": "",
    }


def _supports_keyword_argument(callable_obj: Any, name: str) -> bool:
    try:
        parameters = inspect.signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _verify_checkout_session(buff_client: Any, game: str) -> None:
    """Fail before journaling or POST when the live BUFF session is invalid."""

    verify = getattr(buff_client, "verify_session", None)
    if not callable(verify):
        # Lightweight test doubles and legacy adapters do not expose the probe;
        # the production BuffClient always does.
        return
    if not bool(verify(game)):
        raise BuffAuthExpired(
            "BUFF 在线会话预检失败，未发送锁单请求"
        )


def _ensure_checkout_identity_unchanged(buff_client: Any) -> None:
    guard = get_unresolved_checkout() or {}
    expected = str(guard.get("credential_fingerprint") or "")
    current = _checkout_credential_identity(buff_client)
    actual = str(current.get("credential_fingerprint") or "")
    if expected and actual and expected != actual:
        update_checkout(
            expected_intent_id=str(guard.get("intent_id") or "") or None,
            stage="credentials_changed",
            reason="checkout 期间 BUFF 凭证发生变化，禁止继续核销",
            credential_generation=current.get("credential_generation", 0),
            credential_fingerprint=actual,
        )
        raise PurchaseOrderCreatedPending(
            "checkout 期间 BUFF 凭证发生变化，需先人工对账",
            order_id=str(guard.get("order_id") or ""),
            batch_id=str(guard.get("batch_id") or ""),
        )


def _cache_buff_sell_orders(item: Dict[str, Any], orders: list) -> None:
    """Cache one coherent BUFF snapshot and record when it was fetched."""
    item["_buff_sell_orders"] = orders
    item["_buff_sell_orders_fetched_at"] = time.time()


def _buff_orders_cache_is_fresh(
    item: Dict[str, Any],
    ttl_seconds: float = BUFF_ORDERS_CACHE_TTL_SECONDS,
) -> bool:
    orders = item.get("_buff_sell_orders")
    fetched_at = item.get("_buff_sell_orders_fetched_at")
    if not orders or not isinstance(fetched_at, (int, float)):
        return False
    age = time.time() - float(fetched_at)
    return 0 <= age <= max(0.0, min(float(ttl_seconds), BUFF_ORDERS_CACHE_TTL_SECONDS))

def _fetch_steam_sell_data(
    market_hash_name: str,
    config: dict,
    app_id: int = 730,
    *,
    return_error: bool = False,
):
    from app.config_loader import get_steam_credentials
    from steam.session import create_market_session
    from steam.market_orders import get_sell_orders_cny, compute_smart_list_price
    name = (market_hash_name or "").strip()
    if not name:
        reason = "Steam 市场名为空"
        return (None, reason) if return_error else None
    cred = get_steam_credentials()
    cookies = cred.get("cookies", "")
    steam_id = cred.get("steam_id", "")
    if not cookies or not steam_id:
        missing = []
        if not cookies:
            missing.append("Cookie")
        if not steam_id:
            missing.append("steam_id")
        reason = "Steam 凭据缺失: " + "、".join(missing)
        return (None, reason) if return_error else None
    try:
        session = create_market_session(cookies, steam_id)
        cfg = config.get("pipeline", {})
        wall_volume = int(cfg.get("sell_price_wall_volume", 20))
        max_ignore = int(cfg.get("sell_price_max_ignore_volume", 4))
        usd_to_cny_rate = float(cfg.get("usd_to_cny", 7.2))
        orders_result = get_sell_orders_cny(
            session,
            name,
            app_id=app_id,
            request_delay=1.0,
            return_error=True,
            usd_to_cny_rate=usd_to_cny_rate,
        )
        if isinstance(orders_result, tuple) and len(orders_result) == 2:
            result, reason = orders_result
        else:
            result, reason = orders_result, None
        if not result:
            return (None, reason or "Steam 卖单接口返回空数据") if return_error else None
        if not result.get("sell_orders"):
            return (None, reason or "Steam 返回空卖单图") if return_error else None
        orders = result["sell_orders"]
        price, _ = compute_smart_list_price(
            orders,
            wall_volume_threshold=wall_volume,
            max_ignore_volume=max_ignore,
            min_step=0,
            offset=0,
        )
        if price is None:
            reason = "Steam 卖单已获取，但无法计算智能参考价"
            return (None, reason) if return_error else None
        data = {"sell_orders": orders, "smart_price": price}
        return (data, None) if return_error else data
    except Exception as e:
        detail = str(e).strip()
        if len(detail) > 120:
            detail = detail[:117] + "..."
        reason = f"Steam 卖单获取异常: {type(e).__name__}" + (f" - {detail}" if detail else "")
        return (None, reason) if return_error else None

def _check_buff_price(
    item,
    gid,
    plan_price,
    buff_client,
    config: dict,
    log_fn,
):
    # 拉取 Buff 实时最低价，和 iflow 价格对比确认没有跳动
    # 成功返回 (True, 最新价格)，失败返回 (False, None)
    # 注意：BuffAuthExpired 要直接往上抛，不能在这里吃掉
    buff_cfg = config.get("buff", {})
    game_buff = buff_cfg.get("game", "csgo")
    tolerance = float(buff_cfg.get("price_tolerance", 0.5))
    orders = buff_client.get_sell_orders(gid, game_buff)
    if not orders:
        if log_fn:
            reason = "接口返回 None，可能是网络/鉴权/风控问题" if orders is None else "Buff 当前无在售卖单"
            log_fn(f"[Buff]   → 预检未通过: 无法获取 Buff 卖单信息：{reason} (goods_id={gid})", "warn")
        return False, None
    lowest_price, _ = count_lowest_price_orders(orders)
    if lowest_price <= 0:
        if log_fn:
            log_fn(f"[Buff]   → 预检未通过: Buff 最低价无效 (goods_id={gid})", "warn")
        return False, None
    if plan_price is not None and lowest_price - plan_price > tolerance:
        if log_fn:
            log_fn(f"[Buff]   → 预检未通过: Buff 最低价 {lowest_price:.2f} 较 iflow 参考价 {plan_price:.2f} 超出容忍 (差{lowest_price - plan_price:.2f})", "warn")
        return False, None
    item["_buff_lowest_price"] = lowest_price
    _cache_buff_sell_orders(item, orders)
    return True, lowest_price

def _adjust_ref_price_for_daily_high(
    market_hash_name: str,
    current_ref_price: float,
    config: dict,
    log_fn: Optional[Callable[[str, str], None]],
    app_id: int = 730,
) -> float:
    from utils.time import parse_steam_history_date
    from utils.money import apply_currency, USD_TO_CNY_DEFAULT
    name = (market_hash_name or "").strip()
    if not name or current_ref_price <= 0:
        return current_ref_price
    steam_client = SteamClient()
    raw = steam_client.fetch_history(name, app_id=app_id, return_currency=True)
    if not raw:
        return current_ref_price
    history = raw.get("history") if isinstance(raw, dict) else raw
    if not isinstance(history, list) or not history:
        return current_ref_price
    currency = raw.get("currency") if isinstance(raw, dict) else None
    usd_cny = float(config.get("pipeline", {}).get("usd_to_cny", USD_TO_CNY_DEFAULT))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    prices_cny: List[float] = []
    for item in history:
        if len(item) < 2:
            continue
        dt = parse_steam_history_date(str(item[0]))
        if dt is None or dt < cutoff:
            continue
        try:
            p = float(item[1])
            converted, _ = apply_currency([p], currency, usd_cny)
            prices_cny.append(converted[0])
        except (ValueError, TypeError):
            continue
    if len(prices_cny) < 2:
        return current_ref_price
    sorted_prices = sorted(prices_cny)
    trimmed_prices = sorted_prices[1:-1] if len(sorted_prices) >= 3 else sorted_prices
    if not trimmed_prices:
        return current_ref_price
    trimmed_low = trimmed_prices[0]
    trimmed_high = trimmed_prices[-1]
    daily_avg = statistics.mean(trimmed_prices)
    if trimmed_high <= trimmed_low:
        return current_ref_price
    daily_position = (current_ref_price - trimmed_low) / (trimmed_high - trimmed_low)
    if daily_position <= 0.6:
        return current_ref_price
    candidate_price = (current_ref_price + daily_avg) / 2
    conservative_price = min(current_ref_price, candidate_price)
    if log_fn:
        log_fn(f"[Buff]   → 检测到 Steam 价格处于日内高位 (位置: {daily_position:.2f})，存在虚高风险", "warn")
        if conservative_price < current_ref_price:
            log_fn(f"[Buff]   → 降级参考价: {current_ref_price:.2f} -> {conservative_price:.2f} (当前价与去极值均价折中)", "info")
        else:
            log_fn(f"[Buff]   → 降级参考价保持: {current_ref_price:.2f} (去极值均价不低于当前价，避免降级抬价)", "info")
    return conservative_price
def _compute_sell_pressure_from_orders(
    sell_orders: list,
    daily_volume: int,
    n_orders: int = 5,
) -> Optional[float]:
    if daily_volume <= 0 or not sell_orders:
        return None
    target_orders = sorted(sell_orders, key=lambda x: x[0])[:n_orders]
    total_vol = sum(vol for _, vol in target_orders)
    base_pressure = total_vol / daily_volume
    if len(target_orders) < 2:
        return base_pressure
    current_vol = 0
    wall_vol = 0
    for i in range(len(target_orders) - 1):
        p_curr, c_curr = target_orders[i]
        p_next, _ = target_orders[i + 1]
        current_vol += c_curr
        if p_curr < 5.0:
            gap_abs, gap_rel = 0.10, 0.08
        elif p_curr < 20.0:
            gap_abs, gap_rel = 0.30, 0.05
        elif p_curr < 100.0:
            gap_abs, gap_rel = 1.0, 0.03
        elif p_curr < 500.0:
            gap_abs, gap_rel = 5.0, 0.02
        else:
            gap_abs, gap_rel = 10.0, 0.015
        threshold = max(gap_abs, p_curr * gap_rel)
        if (p_next - p_curr) > threshold:
            wall_vol = current_vol
            break
    if wall_vol > 0 and (wall_vol <= max(3, daily_volume * 0.15)):
        return base_pressure * 0.4
    return base_pressure


def _compute_sell_pressure(
    market_hash_name: str,
    daily_volume: int,
    config: dict,
    n_orders: int = 5,
    app_id: int = 730,
) -> Optional[float]:
    data = _fetch_steam_sell_data(market_hash_name, config, app_id)
    if not data or not data.get("sell_orders"):
        return None
    return _compute_sell_pressure_from_orders(data["sell_orders"], daily_volume, n_orders)
def _fetch_smart_market_price(market_hash_name: str, config: dict, app_id: int = 730) -> Optional[float]:
    data = _fetch_steam_sell_data(market_hash_name, config, app_id)
    return data.get("smart_price") if data else None


TARGET_REACHED = object()
SKIP_NO_FAILED = object()
SKIP_VERIFICATION_FAILED = object()


def _parse_threshold(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _goods_id_from_buff_url(url: str) -> int:
    if not url or "buff.163.com" not in url:
        return 0
    m = re.search(r"/goods/(\d+)", url)
    return int(m.group(1)) if m else 0


_RATIO_ATTR = {"sell": "sell_ratio", "buy": "buy_ratio"}


def _log_stability_rejection(
    report: dict,
    stability_cfg: dict,
    smart_price,
    log_fn,
) -> None:
    # 打印一行可读的拒绝原因，方便排查为什么这件东西被跳过
    if not log_fn:
        return
    msg = report.get("msg", "指标验证不通过")
    st = report.get("status", "")
    cv = report.get("cv", 0)
    r2 = report.get("r_squared", 0)
    avg = report.get("avg", 0)
    slope = report.get("slope", 0)
    pp = report.get("price_percentile")
    pp_str = f" 分位={pp:.2f}" if pp is not None else ""
    smart_str = f" 智能选价={smart_price:.2f}" if smart_price is not None else ""
    ma_str = f" EMA7={report.get('ma7',0):.2f} EMA30={report.get('ma30',0):.2f}"
    bb_upper = report.get("bb_upper")
    bb_str = f" BB+={bb_upper:.2f}" if bb_upper is not None else ""
    log_fn(f"[稳定性]   → 拒绝: {msg} status={st} cv={cv:.3f} R2={r2:.3f} 均价={avg:.2f} slope={slope:.4f}{ma_str}{bb_str}{smart_str}{pp_str}", "warn")


def filter_iflow_rows(
    rows: List[Any],
    config: dict,
    log_fn: Optional[Callable[[str, str], None]] = None,
) -> List[Dict[str, Any]]:
    pipeline_cfg = config.get("pipeline", {})
    iflow_cfg = config.get("steamdt") or config.get("iflow", {})
    exclude = pipeline_cfg.get("exclude_keywords", [])
    top_n = int(pipeline_cfg.get("iflow_top_n", 0) or 0)
    if top_n > 0:
        rows = rows[:top_n]
    sort_by = (iflow_cfg.get("sort_by") or "sell").strip()
    ratio_attr = _RATIO_ATTR.get(sort_by, "sell_ratio")
    steam_client = SteamClient()
    filtered = []
    skipped_keyword = 0
    skipped_price = 0
    skipped_no_buff = 0
    for r in rows:
        name = (getattr(r, "name", None) or "").lower()
        name_cn = (getattr(r, "name_cn", None) or "").lower()
        if any(kw in name or kw in name_cn for kw in exclude):
            skipped_keyword += 1
            continue
        try:
            price = float(getattr(r, "min_price", 0))
        except (ValueError, TypeError):
            skipped_price += 1
            continue
        if price <= 0:
            skipped_price += 1
            continue
        try:
            ratio_val = float(getattr(r, ratio_attr, 0) or 0)
        except (ValueError, TypeError):
            ratio_val = 0
        gid = _goods_id_from_buff_url(getattr(r, "platform", "") or "")
        if gid <= 0:
            skipped_no_buff += 1
            continue
        steam_link = getattr(r, "steam_link", None) or ""
        steam_market_name = steam_client.market_hash_name_from_listing_url(
            steam_link
        ) or name
        try:
            vol = int(getattr(r, "volume", "0") or 0)
        except (ValueError, TypeError):
            vol = 0
        filtered.append({
            "name": getattr(r, "name", ""),
            "min_price": price,
            "goods_id": gid,
            "platform": getattr(r, "platform", ""),
            "steam_market_name": steam_market_name,
            "steam_link": steam_link,
            "ratio": ratio_val,
            "daily_volume": vol,
        })
    if log_fn:
        parts = [f"排除关键词={skipped_keyword}", f"价格无效={skipped_price}"]
        if top_n > 0:
            parts.append(f"取前{top_n}条")
        parts.extend([f"非Buff链接={skipped_no_buff}", f"→ 通过 {len(filtered)} 条"])
        log_fn(f"[筛选] {' '.join(parts)}", "info")
    return filtered

def _check_sell_pressure_precheck(
    item,
    steam_sell_data,
    sell_pressure_threshold,
    pipeline_cfg,
    log_fn,
) -> bool:
    # 检查卖压是否过高
    if sell_pressure_threshold is not None and sell_pressure_threshold > 0:
        daily_vol = int(item.get("daily_volume", 0) or 0)
        sell_orders = steam_sell_data.get("sell_orders")
        n_sell_orders = int(pipeline_cfg.get("sell_pressure_orders_n", 5) or 5)
        if daily_vol > 0 and sell_orders:
            pressure = _compute_sell_pressure_from_orders(sell_orders, daily_vol, n_sell_orders)
            if pressure is not None and pressure > sell_pressure_threshold:
                if log_fn:
                    log_fn(f"[稳定性]   → 预检未通过: 卖压过高 前{n_sell_orders}档总量/日销={pressure:.2f} 阈值={sell_pressure_threshold}", "warn")
                return False
        elif daily_vol <= 0 and log_fn:
            log_fn("[稳定性]   → 卖压检查: 日销量为0，跳过", "info")
    return True

def _check_max_discount_precheck(
    item,
    gid,
    smart_price,
    est_ratio,
    ref_price_est,
    plan_price,
    max_discount,
    log_fn,
) -> bool:
    # 检查买入价占Steam参考价的比例是否低于max_discount，超了就不够利润
    if max_discount is not None:
        max_discount_float = float(max_discount)
        if smart_price is None or smart_price <= 0:
            if log_fn:
                log_fn("[稳定性]   → 预检未通过: Steam 卖单已返回，但智能参考价为空或无效", "warn")
            return False
        if est_ratio is None or est_ratio <= 0:
            if log_fn:
                plan_str = f"{plan_price:.2f}" if isinstance(plan_price, (int, float)) else "无效"
                ref_str = f"{ref_price_est:.2f}" if isinstance(ref_price_est, (int, float)) else "无效"
                log_fn(f"[稳定性]   → 预检未通过: 无法计算预估比例 (Buff最低价={plan_str}, Steam参考价={ref_str})", "warn")
            return False
        if est_ratio >= max_discount_float:
            if log_fn:
                log_fn(f"[稳定性]   → 预检未通过: (Buff最低价/Steam参考价)×1.15={est_ratio:.4f} 需<{max_discount_float} (Steam参考价={ref_price_est:.2f})", "warn")
            return False
    return True


def _passes_final_buff_precheck(
    item: Dict[str, Any],
    gid: int,
    config: dict,
    buff_client: Optional[Any],
    smart_price: Optional[float],
    ref_price_est: Optional[float],
    max_discount: Optional[float],
    log_fn: Optional[Callable[[str, str], None]],
) -> bool:
    """Fetch BUFF only after the candidate passed the non-BUFF guards."""
    if not buff_client or not is_strategy_module_enabled(config, "buy", "buy.buff_realtime_price"):
        return True
    buff_ok, realtime_price = _check_buff_price(
        item,
        gid,
        item.get("min_price"),
        buff_client,
        config,
        log_fn,
    )
    if not buff_ok:
        return False

    # The earlier discount check used the feed price. Re-check with the actual
    # BUFF snapshot so a small but threshold-crossing price rise is still safe.
    if max_discount is not None:
        realtime_ratio = None
        if ref_price_est is not None and ref_price_est > 0 and realtime_price is not None:
            realtime_ratio = (realtime_price / ref_price_est) * STEAM_FEE_FACTOR
        if not _check_max_discount_precheck(
            item,
            gid,
            smart_price,
            realtime_ratio,
            ref_price_est,
            realtime_price,
            max_discount,
            log_fn,
        ):
            return False
    return True


def _build_buy_strategy_outputs(
    item: Dict[str, Any],
    steam_sell_data: Optional[Dict[str, Any]] = None,
    smart_price: Optional[float] = None,
    est_ratio: Optional[float] = None,
    ref_price_est: Optional[float] = None,
    report: Optional[Dict[str, Any]] = None,
    pipeline_cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    outputs: Dict[str, Any] = {}
    if steam_sell_data:
        orders = steam_sell_data.get("sell_orders") or []
        daily_volume = int(item.get("daily_volume", 0) or 0)
        sell_pressure = None
        if daily_volume > 0 and orders:
            try:
                n = int((pipeline_cfg or {}).get("sell_pressure_orders_n", 5) or 5)
                top_orders = orders[:max(1, n)]
                volume = sum(int(o.get("quantity", 0) or 0) for o in top_orders if isinstance(o, dict))
                sell_pressure = volume / daily_volume
            except Exception:
                sell_pressure = None
        outputs["buy.steam_sell_depth"] = {
            "smart_price": smart_price if smart_price is not None else steam_sell_data.get("smart_price"),
            "sell_orders_count": len(orders),
            "sell_pressure": sell_pressure,
            "reference_price": ref_price_est,
            "estimated_ratio": est_ratio,
        }
    if report:
        outputs["guard.history_data_window"] = {
            key: report.get(key)
            for key in (
                "status", "avg", "cv", "r_squared", "slope", "price_percentile",
                "ma7", "ma30", "is_stable",
            )
        }
    if est_ratio is not None:
        outputs["guard.max_discount"] = {
            "estimated_ratio": est_ratio,
            "limit": (pipeline_cfg or {}).get("max_discount"),
        }
    return outputs


def _passes_custom_buy_modules(
    item: Dict[str, Any],
    config: dict,
    *,
    steam_sell_data: Optional[Dict[str, Any]] = None,
    smart_price: Optional[float] = None,
    est_ratio: Optional[float] = None,
    ref_price_est: Optional[float] = None,
    report: Optional[Dict[str, Any]] = None,
    log_fn: Optional[Callable[[str, str], None]] = None,
) -> bool:
    outputs = _build_buy_strategy_outputs(
        item,
        steam_sell_data=steam_sell_data,
        smart_price=smart_price,
        est_ratio=est_ratio,
        ref_price_est=ref_price_est,
        report=report,
        pipeline_cfg=config.get("pipeline") or {},
    )
    context = {
        "item": item,
        "config": config,
    }
    results, blocking = evaluate_strategy_runtime_modules(
        config,
        "buy",
        "buy.candidate_guard",
        context=context,
        outputs=outputs,
    )
    if log_fn:
        for result in results:
            level = "warn" if result.get("status") in {"reject", "error"} else "info"
            log_fn(f"[策略模块] {result.get('module_name')}: {result.get('reason')} ({result.get('status')})", level)
    return blocking is None


def pick_stable_item(
    filtered: List[Dict[str, Any]],
    config: dict,
    steam_client: SteamClient,
    analyzer: StabilityAnalyzer,
    is_stop_requested: callable,
    log_fn: Optional[Callable[[str, str], None]] = None,
    exclude_goods_ids: Optional[set] = None,
    buff_client: Optional[Any] = None,
) -> Tuple[Optional[Dict[str, Any]], Set[int]]:
    # 遍历候选饰品，返回第一个通过所有检测的
    # 检测顺序: Buff价格预检 → Steam卖单 → 卖压 → 最高折扣 → 历史稳定性
    stability_cfg = config.get("stability", {})
    stability_days = int(stability_cfg.get("days", 30))
    cv_threshold = float(stability_cfg.get("cv_threshold", 0.05))
    r2_threshold = float(stability_cfg.get("r2_threshold", 0.6))
    min_daily_trades = float(stability_cfg.get("min_daily_trades", 5))
    excluded = exclude_goods_ids or set()
    stability_failed: Set[int] = set()
    n = len(filtered)
    request_interval = float(stability_cfg.get("request_interval_seconds", 2.5))
    failure_delay = max(0, float(stability_cfg.get("request_failure_delay_seconds", 5) or 5))
    legacy_history_enabled = is_strategy_module_enabled(config, "buy", "guard.history_stability", default=False)
    history_data_enabled = legacy_history_enabled or is_strategy_module_enabled(config, "buy", "guard.history_data_window")
    volatility_enabled = legacy_history_enabled or is_strategy_module_enabled(config, "buy", "guard.volatility_cv")
    trend_quality_enabled = legacy_history_enabled or is_strategy_module_enabled(config, "buy", "guard.trend_quality")
    price_position_enabled = legacy_history_enabled or is_strategy_module_enabled(config, "buy", "guard.price_position")
    history_analysis_enabled = any((
        legacy_history_enabled,
        history_data_enabled,
        volatility_enabled,
        trend_quality_enabled,
        price_position_enabled,
    ))
    for i, item in enumerate(filtered):
        if is_stop_requested():
            return None, stability_failed
        gid = item.get("goods_id")
        if gid is not None and gid in excluded:
            continue
        if i > 0 and request_interval > 0:
            jittered_sleep(request_interval)
        name = item.get("name", "")
        market_hash_name = item.get("steam_market_name") or name
        # 带饰品名称前缀的日志包装，方便追踪每条日志对应哪个饰品
        _short = (name[:30] + "…") if len(name) > 30 else name
        item_log = (lambda msg, level, _n=_short: log_fn(f"[{_n}] {msg}", level)) if log_fn else None
        next_name = filtered[i + 1].get("name", "") if i + 1 < n else ""
        set_status("running", step="STABILITY_CHECK", progress_total=n, progress_done=i, progress_item=f"({i+1}/{n}) {name}", next_progress_item=f"({i+2}/{n}) {next_name}" if next_name else "")
        pipeline_cfg = config.get("pipeline", {})
        verbose_detail = bool(pipeline_cfg.get("verbose_debug", False))
        if item_log and verbose_detail:
            item_log(f"[稳定性] 开始预检 ({i+1}/{n}) Buff参考价={item.get('min_price')} 比例={item.get('ratio')}", "debug")
        max_discount = pipeline_cfg.get("max_discount") if is_strategy_module_enabled(config, "buy", "guard.max_discount") else None
        sell_pressure_threshold = _parse_threshold(pipeline_cfg.get("sell_pressure_threshold")) if is_strategy_module_enabled(config, "buy", "guard.sell_pressure") else None
        steam_depth_enabled = is_strategy_module_enabled(config, "buy", "buy.steam_sell_depth")
        need_steam = (
            steam_depth_enabled
            and (
                max_discount is not None
                or (sell_pressure_threshold is not None and sell_pressure_threshold > 0 and int(item.get("daily_volume", 0) or 0) > 0)
            )
        )
        steam_sell_data: Optional[Dict[str, Any]] = None
        smart_price: Optional[float] = None
        est_ratio: Optional[float] = None
        ref_price_est: Optional[float] = None

        if need_steam:
            plan_price = item.get("min_price")

            # 1. 拉取 Steam 挂单数据。BUFF 实时预检延后到所有非 BUFF
            # 策略通过之后，避免对注定淘汰的候选消耗请求。
            if item_log and verbose_detail:
                item_log("[稳定性] 拉取 Steam 卖单深度…", "debug")
            steam_sell_data, steam_error = _fetch_steam_sell_data(
                market_hash_name, config, app_id=730, return_error=True
            )
            if not steam_sell_data:
                if item_log:
                    item_log(f"[稳定性] 预检未通过: 无法获取 Steam 卖单信息：{steam_error or '未知原因'}", "warn")
                if gid:
                    stability_failed.add(gid)
                if failure_delay > 0:
                    jittered_sleep(failure_delay)
                continue
            if item_log and verbose_detail:
                orders_count = len(steam_sell_data.get("sell_orders") or [])
                smart_dbg = steam_sell_data.get("smart_price")
                smart_str = f"{smart_dbg:.2f}" if isinstance(smart_dbg, (int, float)) else "无"
                item_log(f"[稳定性] Steam 卖单获取成功: {orders_count} 档 智能参考价={smart_str}", "debug")

            # 2. 卖压检测
            if not _check_sell_pressure_precheck(
                item, steam_sell_data, sell_pressure_threshold, pipeline_cfg, item_log
            ):
                if gid:
                    stability_failed.add(gid)
                if failure_delay > 0:
                    jittered_sleep(failure_delay)
                continue

            # 3. 计算智能价和预估比例
            smart_price = steam_sell_data.get("smart_price")
            if smart_price is not None and smart_price > 0 and plan_price and plan_price > 0:
                ref_price_est = _adjust_ref_price_for_daily_high(
                    market_hash_name, smart_price, config, log_fn, app_id=730
                )
                est_ratio = (plan_price / ref_price_est) * STEAM_FEE_FACTOR

            # 4. 最高折扣检测
            if not _check_max_discount_precheck(
                item, gid, smart_price, est_ratio, ref_price_est, plan_price, max_discount, item_log
            ):
                if gid:
                    stability_failed.add(gid)
                if failure_delay > 0:
                    jittered_sleep(failure_delay)
                continue

        if not history_analysis_enabled:
            if smart_price is None and not need_steam:
                steam_sell_data = _fetch_steam_sell_data(market_hash_name, config, app_id=730)
                smart_price = steam_sell_data.get("smart_price") if steam_sell_data else None
            item["_steam_sell_data"] = steam_sell_data
            if not _passes_custom_buy_modules(
                item,
                config,
                steam_sell_data=steam_sell_data,
                smart_price=smart_price,
                est_ratio=est_ratio,
                ref_price_est=ref_price_est,
                log_fn=item_log,
            ):
                if gid:
                    stability_failed.add(gid)
                continue
            if not _passes_final_buff_precheck(
                item,
                gid,
                config,
                buff_client,
                smart_price,
                ref_price_est,
                max_discount,
                item_log,
            ):
                if gid:
                    stability_failed.add(gid)
                continue
            if item_log:
                item_log("[稳定性] 历史稳定性模块未启用，跳过历史分析，选定本件", "info")
            return item, stability_failed

        # 6. 拉历史K线 + 稳定性分析
        if item_log:
            item_log("[稳定性] 拉取历史价格…", "info")
        raw = steam_client.fetch_history(market_hash_name, return_currency=True)
        if raw and isinstance(raw, dict):
            history = raw.get("history")
            currency = raw.get("currency")
        else:
            history = raw if isinstance(raw, list) else None
            currency = None
        if not history:
            if gid:
                stability_failed.add(gid)
            if item_log:
                item_log("[稳定性] 无历史数据或请求失败，试下一个", "warn")
            if failure_delay > 0:
                jittered_sleep(failure_delay)
            continue

        # 利润特别大时适当放宽价格分位限制
        dyn_price_percentile_ceil = float(stability_cfg.get("price_percentile_ceil", 0.8)) if price_position_enabled else 999.0
        if est_ratio is not None and est_ratio > 0 and max_discount is not None:
            max_discount_float = float(max_discount)
            huge_offset = float(pipeline_cfg.get("huge_profit_offset", 0.05))
            huge_ratio = max_discount_float - huge_offset
            high_ratio = max_discount_float - (huge_offset / 2.0)
            if est_ratio < huge_ratio:
                dyn_price_percentile_ceil = 0.88
                if item_log:
                    item_log(f"[稳定性] 检测到巨额预期利润 (比例={est_ratio:.4f} < {huge_ratio:.4f})，放宽价格分位点限制至 {dyn_price_percentile_ceil}", "info")
            elif est_ratio < high_ratio:
                dyn_price_percentile_ceil = max(dyn_price_percentile_ceil, 0.85)
                if item_log:
                    item_log(f"[稳定性] 检测到极高预期利润 (比例={est_ratio:.4f} < {high_ratio:.4f})，放宽价格分位点限制至 {dyn_price_percentile_ceil}", "info")

        report = analyzer.analyze(
            history,
            days=stability_days,
            currency=currency,
            cv_threshold=cv_threshold if volatility_enabled else 999.0,
            r2_threshold=r2_threshold if trend_quality_enabled else 2.0,
            min_daily_trades=min_daily_trades if history_data_enabled else 0,
            current_price=smart_price,
            price_percentile_ceil=dyn_price_percentile_ceil,
            r2_rising_threshold=float(stability_cfg.get("r2_rising_threshold", 0.8)) if trend_quality_enabled else -1.0,
            slope_pct_ceil=float(stability_cfg.get("slope_pct_ceil", 0.01)) if trend_quality_enabled else 999.0,
            ma_deviation_ceil=float(stability_cfg.get("ma_deviation_ceil", 1.1)) if price_position_enabled else 999.0,
            last_price_ma30_ceil=float(stability_cfg.get("last_price_ma30_ceil", 1.05)) if price_position_enabled else 999.0,
            slope_stable_floor=float(stability_cfg.get("slope_stable_floor", -0.005)) if trend_quality_enabled else -999.0,
            price_percentile_ceil_rising=float(stability_cfg.get("price_percentile_ceil_rising", 0.5)) if price_position_enabled else 999.0,
            use_vwap=bool(stability_cfg.get("use_vwap", True)),
        )
        if smart_price is not None and not report.get("valid"):
            if gid:
                stability_failed.add(gid)
            if item_log:
                item_log(f"[稳定性] 分析异常: {report.get('msg', '无效')}，试下一个", "warn")
            if failure_delay > 0:
                jittered_sleep(failure_delay)
            continue

        if not report.get("is_stable"):
            _log_stability_rejection(report, stability_cfg, smart_price, item_log)
            if gid:
                stability_failed.add(gid)
            if failure_delay > 0:
                jittered_sleep(failure_delay)
            continue
        if smart_price is None and not need_steam:
            steam_sell_data = _fetch_steam_sell_data(market_hash_name, config, app_id=730)
            smart_price = steam_sell_data.get("smart_price") if steam_sell_data else None
        item["_steam_sell_data"] = steam_sell_data
        if not _passes_custom_buy_modules(
            item,
            config,
            steam_sell_data=steam_sell_data,
            smart_price=smart_price,
            est_ratio=est_ratio,
            ref_price_est=ref_price_est,
            report=report,
            log_fn=item_log,
        ):
            if gid:
                stability_failed.add(gid)
            if failure_delay > 0:
                jittered_sleep(failure_delay)
            continue
        if not _passes_final_buff_precheck(
            item,
            gid,
            config,
            buff_client,
            smart_price,
            ref_price_est,
            max_discount,
            item_log,
        ):
            if gid:
                stability_failed.add(gid)
            continue
        if item_log:
            st = report.get("status", "")
            sl = report.get("slope", 0)
            r2 = report.get("r_squared", 0)
            pp = report.get("price_percentile")
            pp_str = f" 分位={pp:.2f}" if pp is not None else ""
            smart_str = f" 智能选价={smart_price:.2f}" if smart_price is not None else ""
            ma_str = f" EMA7={report.get('ma7',0):.2f} EMA30={report.get('ma30',0):.2f}"
            bb_upper = report.get("bb_upper")
            bb_str = f" BB+={bb_upper:.2f}" if bb_upper is not None else ""
            item_log(f"[稳定性] ✓ 通过 status={st} cv={report.get('cv',0):.3f} R2={r2:.3f} 均价={report.get('avg',0):.2f} slope={sl:.4f}{ma_str}{bb_str}{smart_str}{pp_str}，选定本件", "info")
        return item, stability_failed
    return None, stability_failed
def _do_payment_notify_and_wait(
    item: Dict[str, Any],
    config: dict,
    unit_price: float,
    num: int,
    pay_url: str,
    pay_type: str,
    order_id: str,
    acc: float,
    set_pending_payment: callable,
    wait_payment_confirm: callable,
    confirm_payment: callable,
    is_stop_requested: callable,
    log_fn: Optional[Callable[[str, str], None]],
    on_entering_payment: Optional[Callable[[], None]] = None,
) -> bool:
    """Handle notification and wait for user payment confirmation.
    Returns True if user confirmed, False on cancel/timeout/stop.
    """
    name = item.get("name", "")
    set_pending_payment({
        "pay_url": pay_url,
        "pay_type": pay_type,
        "name": name,
        "order_id": order_id,
    })
    try:
        if on_entering_payment:
            on_entering_payment()
        notify_cfg = config.get("notify") or {}
        push_token = (notify_cfg.get("pushplus_token") or "").strip()
        if push_token:
            sell_ratio = None
            value_ratio = item.get("value_ratio")
            try:
                rv = item.get("ratio")
                if rv is not None:
                    if isinstance(rv, str):
                        rv = rv.strip().replace('%', '')
                    sell_ratio = float(rv)
            except (TypeError, ValueError):
                pass
            mhn = (item.get("steam_market_name") or item.get("name") or "").strip()
            sl = item.get("steam_link")
            content = build_payment_notify_content(
                name, unit_price, pay_url, pay_type, acc,
                sell_ratio=sell_ratio, num=num, value_ratio=value_ratio,
                steam_market_hash_name=mhn, steam_link=sl
            )
            try:
                if send_pushplus(push_token, "Buff 待付款", content):
                    if log_fn:
                        log_fn("[Buff]   → PushPlus 推送已发送", "info")
                else:
                    if log_fn:
                        log_fn("[Buff]   → PushPlus 推送发送失败 (返回False)", "warn")
            except Exception as e:
                if log_fn:
                    log_fn(f"[Buff]   → PushPlus 推送发送异常: {e}", "warn")
        email_user = (notify_cfg.get("email_user") or "").strip()
        email_pass = (notify_cfg.get("email_pass") or "").strip()
        timeout_sec = int(notify_cfg.get("email_timeout_seconds", 300))
        if email_user and email_pass:
            def _email_waiter() -> None:
                res = wait_email_command(config, timeout_seconds=timeout_sec, is_stop_requested=is_stop_requested, log_fn=log_fn)
                confirm_payment(res == "success")
            t = threading.Thread(target=_email_waiter, daemon=True)
            t.start()
            ok = wait_payment_confirm()
        else:
            ok = wait_payment_confirm(timeout_seconds=timeout_sec)
        if log_fn:
            log_fn(f"[Buff]   → 用户确认={'成功' if ok else '取消/失败'}", "info")
        return ok
    finally:
        set_pending_payment(None)
def _do_batch_wait_finalize_and_append(
    buff_client: Any,
    item: Dict[str, Any],
    config: dict,
    unit_price: float,
    num: int,
    goods_id: int,
    batch_id: str,
    checkout_intent_id: str,
    game_buff: str,
    pay_url: str,
    acc: float,
    set_pending_payment: callable,
    wait_payment_confirm: callable,
    confirm_payment: callable,
    is_stop_requested: callable,
    append_purchase: callable,
    log_fn: Optional[Callable[[str, str], None]],
    market_price: Optional[float] = None,
    on_entering_payment: Optional[Callable[[], None]] = None,
    pay_type: str = "wechat",
) -> Optional[float]:
    ok = _do_payment_notify_and_wait(
        item, config, unit_price, num, pay_url, pay_type, batch_id, acc,
        set_pending_payment, wait_payment_confirm, confirm_payment,
        is_stop_requested, log_fn, on_entering_payment,
    )
    if is_stop_requested() or not ok:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_created_pending",
            batch_id=str(batch_id),
            reason="用户取消、超时或付款状态未确认",
        )
        return None
    if log_fn:
        log_fn("[Buff]   → 正在核对批次付款状态并核销预检卖单…", "info")
    _ensure_checkout_identity_unchanged(buff_client)
    update_checkout(
        expected_intent_id=checkout_intent_id,
        stage="batch_finalizing",
        batch_id=str(batch_id),
        reason="付款已确认，正在核销卖单",
    )
    if market_price is None:
        mhn = (item.get("steam_market_name") or item.get("name") or "").strip()
        market_price = _fetch_smart_market_price(mhn, config, app_id=730)
    saved_name = (item.get("steam_market_name") or item.get("name") or "").strip()

    def _persist_matches(matches: List[Dict[str, Any]]) -> float:
        subtotal = 0.0
        for match in matches:
            price = float(match.get("price", 0) or 0)
            subtotal += price
            bill_order_id = str(match.get("bill_order_id") or "")
            rec = {
                "name": saved_name,
                "goods_id": goods_id,
                "price": price,
                "at": time.time(),
                "pending_receipt": True,
                "batch_id": str(batch_id),
                "buff_order_id": bill_order_id,
                "bill_order_id": bill_order_id,
                "buff_sell_order_id": str(match.get("id") or ""),
            }
            if market_price is not None and market_price > 0:
                rec["market_price"] = round(float(market_price), 2)
            append_purchase(rec)
        return subtotal

    def _on_match(
        _match: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> None:
        completed_ids = [
            str(match.get("bill_order_id") or "")
            for match in matches
            if match.get("bill_order_id")
        ]
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_finalizing",
            batch_id=str(batch_id),
            partial_results=matches,
            completed_order_ids=completed_ids,
            reason=f"已取得 {len(matches)} 个 BUFF 核销结果，准备下一次核销",
        )

    try:
        finalize = buff_client.batch_buy_find_and_finalize
        if _supports_keyword_argument(finalize, "on_match"):
            matched = finalize(
                goods_id,
                game_buff,
                unit_price,
                num,
                batch_id,
                on_match=_on_match,
            )
        else:
            matched = finalize(
                goods_id,
                game_buff,
                unit_price,
                num,
                batch_id,
            )
        matched = _validate_unique_batch_matches(matched, batch_id)
    except Exception as exc:
        partial = getattr(exc, "partial_results", None)
        partial = partial if isinstance(partial, list) else []
        completed_ids = [
            str(match.get("bill_order_id") or "")
            for match in partial
            if match.get("bill_order_id")
        ]
        # Persist external identifiers before any local row write.  If a later
        # DB commit or process crashes, reconciliation still has every BUFF id.
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_finalize_unknown",
            batch_id=str(batch_id),
            partial_results=partial,
            completed_order_ids=completed_ids,
            reason=str(exc) or type(exc).__name__,
            last_error_type=type(exc).__name__,
        )
        committed_total = _persist_matches(partial) if partial else 0.0
        if isinstance(exc, BuffWriteResultUnknown):
            _mark_committed(exc, committed_total, orders=len(partial))
            raise
        if isinstance(exc, (BuffAuthExpired, BuffRequestBlocked)):
            pending = PurchaseOrderCreatedPending(
                f"批次已创建，核销未完成，需人工对账: {exc}",
                batch_id=str(batch_id),
            )
            _mark_committed(pending, committed_total, orders=len(partial))
            raise pending from exc
        unknown = BuffWriteResultUnknown(
            "批量核销异常，结果未知，禁止继续购买",
            method="POST",
        )
        _mark_committed(unknown, committed_total, orders=len(partial))
        raise unknown from exc

    if not matched:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_created_pending",
            batch_id=str(batch_id),
            reason="付款后未核销到可确认的订单",
        )
        if log_fn:
            log_fn("[Buff]   → 未找到可确认的核销结果，已停止并等待人工对账", "warn")
        return None
    if log_fn:
        log_fn(f"[Buff]   → 核销成功 {len(matched)} 件", "info")
    completed_ids = [
        str(match.get("bill_order_id") or "")
        for match in matched
        if match.get("bill_order_id")
    ]
    update_checkout(
        expected_intent_id=checkout_intent_id,
        stage="batch_matches_received",
        batch_id=str(batch_id),
        completed_order_ids=completed_ids,
        partial_results=matched,
        reason=f"已取得 {len(matched)} 个 BUFF 核销结果，正在写入本地记录",
    )
    total = _persist_matches(matched)
    if len(matched) < num:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_partial",
            batch_id=str(batch_id),
            completed_order_ids=completed_ids,
            partial_results=matched,
            reason=f"仅核销 {len(matched)}/{num} 件",
        )
        if log_fn:
            log_fn(
                f"[Buff]   → 批量核销仅完成 {len(matched)}/{num} 件；已记录成功部分并停止后续购买",
                "warn",
            )
        pending = PurchaseOrderCreatedPending(
            f"Buff 批次仅核销 {len(matched)}/{num} 件，剩余状态需人工对账",
            batch_id=str(batch_id),
        )
        _mark_committed(pending, total, orders=len(matched))
        raise pending
    update_checkout(
        expected_intent_id=checkout_intent_id,
        stage="purchase_recorded",
        batch_id=str(batch_id),
        completed_order_ids=completed_ids,
        reason="全部核销结果已写入本地交易记录",
    )
    bill_order_ids = [
        m.get("bill_order_id")
        for m in matched
        if m.get("bill_order_id")
    ]
    if bill_order_ids:
        try:
            if buff_client.ask_seller_to_send(bill_order_ids, game_buff) and log_fn:
                log_fn(
                    f"[Buff]   → 已提醒 {len(set(bill_order_ids))} 笔订单的卖家发货，请留意 Steam 报价",
                    "info",
                )
            elif log_fn:
                log_fn("[Buff]   → 提醒卖家发货未成功（可稍后在订单页手动催发货）", "warn")
        except (BuffAuthExpired, BuffRequestBlocked) as exc:
            resolve_checkout(
                "batch_purchase_recorded_shipping_prompt_blocked",
                expected_intent_id=checkout_intent_id,
            )
            _mark_committed(exc, total, orders=len(matched))
            raise
        except Exception as exc:
            update_checkout(
                expected_intent_id=checkout_intent_id,
                stage="shipping_reminder_unknown",
                batch_id=str(batch_id),
                completed_order_ids=completed_ids,
                reason=f"{type(exc).__name__}: {exc}",
                last_error_type=type(exc).__name__,
            )
            if log_fn:
                log_fn(
                    f"[Buff]   → 成交已记录；提醒卖家发货结果未知 ({type(exc).__name__})，已停止后续 BUFF 写请求",
                    "warn",
                )
            halted = PurchaseWriteResultUnknown(
                "成交已记录，但提醒卖家发货的写请求结果未知",
                batch_id=str(batch_id),
            )
            _mark_committed(halted, total, orders=len(matched))
            raise halted from exc
    resolve_checkout(
        "batch_purchase_recorded",
        expected_intent_id=checkout_intent_id,
    )
    return total
def _do_wait_payment_and_append(
    buff_client: Any,
    item: Dict[str, Any],
    config: dict,
    unit_price: float,
    num: int,
    goods_id: int,
    pay_url: str,
    pay_type: str,
    order_id: str,
    checkout_intent_id: str,
    acc: float,
    set_pending_payment: callable,
    wait_payment_confirm: callable,
    confirm_payment: callable,
    is_stop_requested: callable,
    append_purchase: callable,
    log_fn: Optional[Callable[[str, str], None]],
    game_buff: str,
    sell_order_id: str = "",
    market_price: Optional[float] = None,
    on_entering_payment: Optional[Callable[[], None]] = None,
) -> Optional[float]:
    ok = _do_payment_notify_and_wait(
        item, config, unit_price, num, pay_url, pay_type, order_id, acc,
        set_pending_payment, wait_payment_confirm, confirm_payment,
        is_stop_requested, log_fn, on_entering_payment,
    )
    if is_stop_requested() or not ok:
        return None
    if market_price is None:
        mhn = (item.get("steam_market_name") or item.get("name") or "").strip()
        market_price = _fetch_smart_market_price(mhn, config, app_id=730)
    saved_name = (item.get("steam_market_name") or item.get("name") or "").strip()
    base_rec = {
        "name": saved_name,
        "goods_id": goods_id,
        "price": unit_price,
        "at": time.time(),
        "pending_receipt": True,
        "buff_order_id": str(order_id),
        "buff_sell_order_id": str(sell_order_id),
    }
    if market_price is not None and market_price > 0:
        base_rec["market_price"] = round(float(market_price), 2)
    for _ in range(num):
        append_purchase(dict(base_rec))
    update_checkout(
        expected_intent_id=checkout_intent_id,
        stage="purchase_recorded",
        order_id=str(order_id),
        reason="订单已写入本地交易记录",
    )
    try:
        if buff_client.ask_seller_to_send(order_id, game_buff) and log_fn:
            log_fn("[Buff]   → 已提醒卖家发货，请留意 Steam 报价", "info")
        elif log_fn:
            log_fn("[Buff]   → 提醒卖家发货未成功（可稍后在订单页手动催发货）", "warn")
    except (BuffAuthExpired, BuffRequestBlocked) as exc:
        resolve_checkout(
            "single_purchase_recorded_shipping_prompt_blocked",
            expected_intent_id=checkout_intent_id,
        )
        _mark_committed(exc, unit_price * num)
        raise
    except Exception as exc:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="shipping_reminder_unknown",
            order_id=str(order_id),
            reason=f"{type(exc).__name__}: {exc}",
            last_error_type=type(exc).__name__,
        )
        if log_fn:
            log_fn(
                f"[Buff]   → 成交已记录；提醒卖家发货结果未知 ({type(exc).__name__})，已停止后续 BUFF 写请求",
                "warn",
            )
        halted = PurchaseWriteResultUnknown(
            "成交已记录，但提醒卖家发货的写请求结果未知",
            order_id=str(order_id),
        )
        _mark_committed(halted, unit_price * num)
        raise halted from exc
    resolve_checkout(
        "single_purchase_recorded",
        expected_intent_id=checkout_intent_id,
    )
    return unit_price * num
def lock_and_confirm_payment(
    buff_client: Any,
    item: Dict[str, Any],
    config: dict,
    target_balance: float,
    acc: float,
    set_pending_payment: callable,
    wait_payment_confirm: callable,
    confirm_payment: callable,
    is_stop_requested: callable,
    append_purchase: callable,
    log_fn: Optional[Callable[[str, str], None]] = None,
    on_entering_payment: Optional[Callable[[], None]] = None,
    is_time_allowed: Optional[Callable[[], bool]] = None,
) -> Optional[float]:
    buff_cfg = config.get("buff", {})
    game_buff = buff_cfg.get("game", "csgo")
    tolerance = float(buff_cfg.get("price_tolerance", 0.5))
    goods_id = item["goods_id"]
    name = item["name"]
    plan_price = item.get("_buff_lowest_price") or item.get("min_price")
    try:
        orders_cache_ttl = float(
            config.get("pipeline", {}).get(
                "buff_sell_orders_cache_ttl_seconds",
                BUFF_ORDERS_CACHE_TTL_SECONDS,
            )
        )
    except (TypeError, ValueError):
        orders_cache_ttl = BUFF_ORDERS_CACHE_TTL_SECONDS
    orders_cache_ttl = max(2.0, min(orders_cache_ttl, BUFF_ORDERS_CACHE_TTL_SECONDS))
    orders = item.get("_buff_sell_orders") if _buff_orders_cache_is_fresh(item, orders_cache_ttl) else None
    if orders is None:
        orders = buff_client.get_sell_orders(goods_id, game_buff)
        if orders:
            _cache_buff_sell_orders(item, orders)
        if log_fn:
            log_fn(f"[Buff] 拉取在售 goods_id={goods_id} game={game_buff} → {len(orders or [])} 条", "info")
    else:
        if log_fn:
            log_fn(f"[Buff]   → 复用 {orders_cache_ttl:.1f} 秒 TTL 内的 Buff 卖单数据 → {len(orders)} 条", "info")
    if not orders:
        if log_fn:
            reason = "接口返回 None，可能是网络/鉴权/风控问题" if orders is None else "Buff 当前无在售卖单"
            log_fn(f"[Buff]   → 无法获取 Buff 卖单信息：{reason}，跳过本件", "warn")
        return None
    lowest_price, count_at_lowest = count_lowest_price_orders(orders)
    if lowest_price <= 0 or count_at_lowest <= 0:
        if log_fn:
            log_fn("[Buff]   → 最低价或可购买数量无效，停止本次购买", "warn")
        return None
    if log_fn:
        log_fn(f"[Buff]   → 最低价={lowest_price:.2f} 同价数量={count_at_lowest} 参考价={plan_price} 容忍={tolerance} 累计={acc:.2f} 目标={target_balance}", "info")
    if _affordable_quantity(target_balance, acc, lowest_price) < 1:
        if log_fn:
            log_fn(f"[Buff]   → 累计+本件={acc + lowest_price:.2f} 已达/超过目标，不再锁单", "info")
        return TARGET_REACHED
    if plan_price is not None and lowest_price - plan_price > tolerance:
        if log_fn:
            log_fn(f"[Buff]   → 当前价较参考价超出容忍 (差{lowest_price - plan_price:.2f})，跳过", "warn")
        return None
    market_hash_name = (item.get("steam_market_name") or item.get("name") or "").strip()
    scfg = config.get("pipeline", {})
    max_discount = scfg.get("max_discount") if is_strategy_module_enabled(config, "buy", "guard.max_discount") else None
    sell_pressure_threshold = _parse_threshold(scfg.get("sell_pressure_threshold")) if is_strategy_module_enabled(config, "buy", "guard.sell_pressure") else None
    steam_depth_enabled = is_strategy_module_enabled(config, "buy", "buy.steam_sell_depth")
    need_steam = (
        steam_depth_enabled
        and (
            max_discount is not None
            or (sell_pressure_threshold is not None and sell_pressure_threshold > 0 and int(item.get("daily_volume", 0) or 0) > 0)
        )
    )
    cached_steam_data = item.get("_steam_sell_data")
    steam_sell_error = None
    if need_steam:
        if cached_steam_data is not None:
            steam_sell_data = cached_steam_data
        else:
            steam_sell_data, steam_sell_error = _fetch_steam_sell_data(
                market_hash_name, config, app_id=730, return_error=True
            )
        if cached_steam_data is not None and log_fn:
            log_fn("[Buff]   → 复用稳定性阶段已缓存的 Steam 卖单数据", "info")
    else:
        steam_sell_data = None
    ref_price = steam_sell_data.get("smart_price") if steam_sell_data else None
    sell_orders = steam_sell_data.get("sell_orders") if steam_sell_data else None
    if max_discount is not None:
        max_discount = float(max_discount)
        if ref_price is None or ref_price <= 0:
            if log_fn:
                reason = steam_sell_error or "Steam 卖单为空或智能参考价无效"
                log_fn(f"[Buff]   → 二次验证: 无法获取 Steam 参考价：{reason}，跳过本件", "warn")
            return SKIP_VERIFICATION_FAILED
        ref_price = _adjust_ref_price_for_daily_high(
            market_hash_name, ref_price, config, log_fn, app_id=730
        )
        if lowest_price > 0:
            value_ratio = (lowest_price / ref_price) * 1.15
            if value_ratio >= max_discount:
                if log_fn:
                    log_fn(f"[Buff]   → 二次验证未通过 (Buff最低价/参考价)×1.15={value_ratio:.4f} 需<{max_discount} (参考价={ref_price:.2f})", "warn")
                return SKIP_VERIFICATION_FAILED
            if log_fn:
                log_fn(f"[Buff]   → 二次验证通过 (Buff最低价/参考价)×1.15={value_ratio:.4f} 参考价={ref_price:.2f}", "info")
    if ref_price and lowest_price > 0:
        item["value_ratio"] = (lowest_price / ref_price) * 1.15
    n_sell_orders = int(scfg.get("sell_pressure_orders_n", 5) or 5)
    if sell_pressure_threshold is not None and sell_pressure_threshold > 0:
        daily_vol = int(item.get("daily_volume", 0) or 0)
        if daily_vol > 0 and sell_orders:
            pressure = _compute_sell_pressure_from_orders(sell_orders, daily_vol, n_sell_orders)
            if pressure is not None and pressure > sell_pressure_threshold:
                if log_fn:
                    log_fn(f"[Buff]   → 卖压过高 前{n_sell_orders}档总量/日销={pressure:.2f} 阈值={sell_pressure_threshold}，跳过", "warn")
                return None
        elif daily_vol <= 0 and log_fn:
            log_fn("[Buff]   → 卖压检查: 日销量为0，跳过", "info")

    buy_runtime = ((config or {}).get("_strategy_runtime") or {}).get("buy")
    if buy_runtime:
        legacy_safe_enabled = is_strategy_module_enabled(config, "buy", "guard.safe_purchase_limit", default=False)
        hard_cap_enabled = legacy_safe_enabled or is_strategy_module_enabled(config, "buy", "guard.purchase_hard_cap", default=False)
        liquidity_cap_enabled = legacy_safe_enabled or is_strategy_module_enabled(config, "buy", "guard.purchase_liquidity_cap", default=False)
        low_price_guard_enabled = legacy_safe_enabled or is_strategy_module_enabled(config, "buy", "guard.low_price_purchase_guard", default=False)
        held_same_guard_enabled = legacy_safe_enabled or is_strategy_module_enabled(config, "buy", "guard.held_same_item_guard", default=False)
    else:
        hard_cap_enabled = True
        liquidity_cap_enabled = True
        low_price_guard_enabled = True
        held_same_guard_enabled = True
    num_to_buy = 0

    def _classify_failure(result: Optional[Dict[str, Any]]) -> _PurchaseAttempt:
        if not result:
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                reason="写请求无响应，服务端结果未知",
            )
        code = str(result.get("code") or "").strip().lower()
        message = str(result.get("msg") or result.get("error") or "").strip().lower()
        failure_text = f"{code} {message}"
        batch_id = str(result.get("batch_id") or "")
        order_id = str(result.get("order_id") or "")
        if code in {"unknown_after_send", "write_result_unknown", "unknown"} or (
            "created" in result and result.get("created") is None
        ):
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                reason=message or code or "写请求结果未知",
                order_id=order_id,
                batch_id=batch_id,
            )
        if code in {"created_without_pay_url", "created_pending"} or result.get("created") is True:
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.CREATED_NOT_PAID,
                reason=message or code or "订单已创建但支付状态未完成",
                order_id=order_id,
                batch_id=batch_id,
            )
        if "cooling_down" in code or "cooling down" in failure_text or "冷却" in failure_text:
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.COOLING_DOWN,
                reason=message or code or "Buff 处于冷却状态",
            )
        if any(token in failure_text for token in ("risk", "captcha", "verify", "verification", "风控", "验证")):
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.RISK,
                reason=message or code or "Buff 返回风控/验证状态",
            )
        safe_fallback_codes = {
            "unsupported",
            "not_supported",
            "batch_unsupported",
            "not_created",
        }
        if code in safe_fallback_codes and result.get("created") is False:
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.SAFE_TO_FALLBACK,
                reason=message or code,
            )
        if result.get("created") is False:
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.FAILED,
                reason=message or code or "购买请求被明确拒绝",
            )
        # An unrecognised non-OK body is not proof that the server failed to
        # create an order.  Keep the durable gate instead of trying another
        # sell order or candidate.
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
            reason=message or code or "购买接口返回未识别状态，结果未知",
            order_id=order_id,
            batch_id=batch_id,
        )

    def _prepare_purchase_plan() -> Optional[_PurchaseAttempt]:
        """Refresh stale quotes and recompute every price-dependent limit."""
        nonlocal orders, lowest_price, count_at_lowest, num_to_buy
        # Read holdings first: slow local work must not age a freshly fetched quote.
        held_same = 0
        if market_hash_name and held_same_guard_enabled:
            purchases_snapshot = get_purchases()
            holdings = [p for p in purchases_snapshot if not (p.get("sale_price") and float(p.get("sale_price", 0) or 0) > 0)]
            held_same = sum(1 for p in holdings if (p.get("name") or "").strip() == market_hash_name)
        if not _buff_orders_cache_is_fresh(item, orders_cache_ttl):
            if log_fn:
                log_fn(
                    f"[Buff]   → 卖单快照已超过 {orders_cache_ttl:.1f} 秒 TTL，重新拉取后再决定是否锁单",
                    "info",
                )
            fetch_orders = getattr(buff_client, "get_sell_orders", None)
            refreshed_orders = (
                fetch_orders(goods_id, game_buff) if callable(fetch_orders) else None
            )
            if not refreshed_orders:
                if log_fn:
                    log_fn("[Buff]   → 快照过期且刷新失败，本次不发送锁单请求", "warn")
                return _PurchaseAttempt(
                    _PurchaseAttemptStatus.FAILED,
                    reason="卖单快照已过期且刷新失败",
                )
            orders = refreshed_orders
            _cache_buff_sell_orders(item, orders)
            if log_fn:
                refreshed_price, refreshed_count = count_lowest_price_orders(orders)
                log_fn(
                    f"[Buff]   → 已刷新卖单快照 → 最低价={refreshed_price:.2f} 同价数量={refreshed_count}",
                    "info",
                )
        lowest_price, count_at_lowest = count_lowest_price_orders(orders)
        if lowest_price <= 0 or count_at_lowest <= 0:
            if log_fn:
                log_fn("[Buff]   → 刷新后最低价无效，本次不发送锁单请求", "warn")
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.FAILED,
                reason="刷新后的卖单最低价无效",
            )
        affordable_quantity = _affordable_quantity(target_balance, acc, lowest_price)
        if affordable_quantity < 1:
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.TARGET_REACHED,
                reason="刷新后价格超出剩余预算",
            )
        if plan_price is not None and lowest_price - plan_price > tolerance:
            if log_fn:
                log_fn("[Buff]   → 刷新后价格超出容忍，本次不发送锁单请求", "warn")
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.FAILED,
                reason="刷新后的卖单价格超出容忍",
            )
        if max_discount is not None:
            if ref_price is None or ref_price <= 0:
                return _PurchaseAttempt(
                    _PurchaseAttemptStatus.SKIP_VERIFICATION_FAILED,
                    reason="刷新后缺少 Steam 参考价",
                )
            refreshed_ratio = (lowest_price / ref_price) * STEAM_FEE_FACTOR
            if refreshed_ratio >= max_discount:
                if log_fn:
                    log_fn(
                        f"[Buff]   → 刷新后二次验证未通过，比例={refreshed_ratio:.4f} 需<{max_discount}",
                        "warn",
                    )
                return _PurchaseAttempt(
                    _PurchaseAttemptStatus.SKIP_VERIFICATION_FAILED,
                    reason="刷新后的卖单未通过二次验证",
                )
        if ref_price and lowest_price > 0:
            item["value_ratio"] = (lowest_price / ref_price) * STEAM_FEE_FACTOR
        cap_candidates = []
        daily_volume = int(item.get("daily_volume", 0) or 0)
        is_low_price = lowest_price < float(scfg.get("safe_purchase_low_price_threshold", 5.0))
        if hard_cap_enabled:
            cap_candidates.append(int(scfg.get("safe_purchase_hard_qty_cap", 50)))
        if liquidity_cap_enabled:
            volume_cap = int(daily_volume * float(scfg.get("safe_purchase_liquidity_ratio", 0.05)))
            if low_price_guard_enabled and is_low_price:
                volume_cap = int(volume_cap * float(scfg.get("safe_purchase_low_price_penalty", 0.5)))
            cap_candidates.append(volume_cap)
        if low_price_guard_enabled and is_low_price:
            cap_candidates.append(int(scfg.get("safe_purchase_low_price_hard_cap", 30)))
        safe_limit = max(min(cap_candidates), 0) if cap_candidates else count_at_lowest
        if held_same > 0:
            previous_limit = safe_limit
            safe_limit = max(0, safe_limit - held_same)
            if log_fn:
                log_fn(f"[Buff]   → 已持有同名(英文) {held_same} 件，安全上限 {previous_limit} → {safe_limit}", "info")
        if safe_limit <= 0:
            if log_fn:
                log_fn("[Buff]   → 安全采购模块限制为0，跳过本件", "warn")
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.SKIP_NO_FAILED,
                reason="安全采购模块限制为0",
            )
        orig_num = min(count_at_lowest, affordable_quantity)
        num_to_buy = min(orig_num, safe_limit)
        if log_fn and orig_num > num_to_buy:
            log_fn(f"[Buff]   → 安全采购上限={safe_limit}，原计划={orig_num} 实际购买={num_to_buy}", "info")
        return None

    def _time_window_is_open() -> bool:
        if is_time_allowed is None:
            return True
        try:
            return bool(is_time_allowed())
        except Exception:
            # A broken clock/config callback must suppress a purchase write.
            return False

    def _try_single_buy() -> _PurchaseAttempt:
        plan_failure = _prepare_purchase_plan()
        if plan_failure is not None:
            return plan_failure
        o = first_order_at_price(orders, lowest_price)
        if not o:
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.FAILED,
                reason="未找到最低价卖单",
            )
        p = float(o.get("price", 0))
        if not _time_window_is_open():
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.TIME_WINDOW_CLOSED,
                reason="购买时间窗已关闭",
            )
        with get_buff_auth_lock():
            with buff_activity_guard():
                if not _time_window_is_open():
                    return _PurchaseAttempt(
                        _PurchaseAttemptStatus.TIME_WINDOW_CLOSED,
                        reason="等待请求锁期间购买时间窗已关闭",
                    )
                _verify_checkout_session(buff_client, game_buff)
                prepared_preview = None
                prepare_single = getattr(buff_client, "prepare_single_buy", None)
                if callable(prepare_single):
                    preparation = prepare_single(
                        game_buff,
                        goods_id,
                        str(o.get("id") or ""),
                        str(o.get("price") or ""),
                    )
                    if not isinstance(preparation, dict) or not preparation.get(
                        "success"
                    ):
                        preparation_info = (
                            preparation if isinstance(preparation, dict) else {}
                        )
                        return _PurchaseAttempt(
                            _PurchaseAttemptStatus.FAILED,
                            reason=str(
                                preparation_info.get("msg")
                                or preparation_info.get("code")
                                or "BUFF 购买预检未通过"
                            ),
                        )
                    prepared_preview = preparation.get("preview")
                identity = _checkout_credential_identity(buff_client)
                intent = begin_checkout(
                    "single",
                    goods_id,
                    sell_order_id=str(o.get("id") or ""),
                    quantity=1,
                    credential_generation=identity.get(
                        "credential_generation",
                        0,
                    ),
                    credential_fingerprint=str(
                        identity.get("credential_fingerprint") or ""
                    ),
                    price=p,
                )
                checkout_intent_id = str(intent["intent_id"])

                def _on_order_created(order_id: str) -> None:
                    update_checkout(
                        expected_intent_id=checkout_intent_id,
                        stage="order_created",
                        order_id=str(order_id),
                        reason="订单已创建，正在获取支付链接",
                    )

                if log_fn:
                    log_fn(
                        f"[Buff]   → 锁单 order_id={o.get('id')} price={o.get('price')}",
                        "info",
                    )
                try:
                    lock_order = buff_client.lock_and_get_pay_url
                    supports_created = _supports_keyword_argument(
                        lock_order, "on_created"
                    )
                    supports_preview = _supports_keyword_argument(
                        lock_order, "preview"
                    )
                    if supports_created or supports_preview:
                        optional_kwargs = {}
                        if supports_created:
                            optional_kwargs["on_created"] = _on_order_created
                        if supports_preview:
                            optional_kwargs["preview"] = prepared_preview
                        result = lock_order(
                            game_buff,
                            goods_id,
                            o["id"],
                            o["price"],
                            **optional_kwargs,
                        )
                    else:
                        result = lock_order(
                            game_buff,
                            goods_id,
                            o["id"],
                            o["price"],
                        )
                except BuffWriteResultUnknown as e:
                    update_checkout(
                        expected_intent_id=checkout_intent_id,
                        stage="write_result_unknown",
                        order_id=str(getattr(e, "order_id", "") or ""),
                        reason=str(e) or "锁单写请求结果未知",
                        last_error_type=type(e).__name__,
                    )
                    if log_fn:
                        log_fn(f"[Buff]   → 锁单写请求结果未知: {e}", "warn")
                    return _PurchaseAttempt(
                        _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                        reason=str(e) or "锁单写请求结果未知",
                        order_id=str(getattr(e, "order_id", "") or ""),
                    )
                except (BuffAuthExpired, BuffRequestBlocked):
                    # A failed preflight is blocked before POST.  BUFF's exact
                    # HTTP-200 `Login Required` response is also a verified
                    # application-level rejection, so neither case created an
                    # order and neither should leave a reconciliation gate.
                    resolve_checkout(
                        "auth_rejected_or_blocked_not_created",
                        expected_intent_id=checkout_intent_id,
                    )
                    raise
                except Exception as e:
                    update_checkout(
                        expected_intent_id=checkout_intent_id,
                        stage="write_result_unknown",
                        reason=str(e) or "锁单网络/接口异常",
                        last_error_type=type(e).__name__,
                    )
                    if log_fn:
                        log_fn(f"[Buff]   → 锁单网络/接口异常: {e}", "warn")
                    return _PurchaseAttempt(
                        _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                        reason=str(e) or "锁单网络/接口异常",
                    )
        if not result or not result.get("success"):
            attempt = _classify_failure(result)
            if attempt.status in {
                _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                _PurchaseAttemptStatus.CREATED_NOT_PAID,
                _PurchaseAttemptStatus.COOLING_DOWN,
                _PurchaseAttemptStatus.RISK,
            }:
                update_checkout(
                    expected_intent_id=checkout_intent_id,
                    stage=(
                        "order_created_pending"
                        if attempt.status is _PurchaseAttemptStatus.CREATED_NOT_PAID
                        else "write_result_unknown"
                    ),
                    order_id=attempt.order_id,
                    reason=attempt.reason,
                )
            else:
                resolve_checkout(
                    "known_not_created",
                    expected_intent_id=checkout_intent_id,
                )
            if log_fn:
                code_str = result.get('code') if result else '未知'
                msg_str = result.get('msg', '无响应内容') if result else '请求失败或超时'
                log_fn(f"[Buff]   → 锁单失败 code={code_str} msg={msg_str}", "warn")
            return attempt
        order_id = str(result.get("order_id") or "")
        pay_url = str(result.get("pay_url") or "")
        if not order_id:
            update_checkout(
                expected_intent_id=checkout_intent_id,
                stage="write_result_unknown",
                reason="锁单返回成功但缺少订单号",
            )
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                reason="锁单返回成功但缺少订单号",
            )
        if not pay_url:
            identity = _checkout_credential_identity(buff_client)
            update_checkout(
                expected_intent_id=checkout_intent_id,
                stage="order_created_pending",
                order_id=order_id,
                reason="订单已创建但未取得支付链接",
                credential_generation=identity.get("credential_generation", 0),
                credential_fingerprint=str(
                    identity.get("credential_fingerprint") or ""
                ),
            )
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.CREATED_NOT_PAID,
                reason="订单已创建但未取得支付链接",
                order_id=order_id,
            )
        if log_fn:
            log_fn(f"[Buff]   → 锁单成功 order_id={order_id} 等待用户确认付款…", "info")
        identity = _checkout_credential_identity(buff_client)
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="payment_pending",
            order_id=order_id,
            credential_generation=identity.get("credential_generation", 0),
            credential_fingerprint=str(
                identity.get("credential_fingerprint") or ""
            ),
            reason="订单已创建，等待付款确认",
        )
        paid = _do_wait_payment_and_append(
            buff_client,
            item,
            config,
            p,
            1,
            goods_id,
            pay_url,
            result.get("pay_type") or "alipay",
            order_id,
            checkout_intent_id,
            acc,
            set_pending_payment,
            wait_payment_confirm,
            confirm_payment,
            is_stop_requested,
            append_purchase,
            log_fn,
            game_buff,
            sell_order_id=str(o.get("id") or ""),
            market_price=ref_price,
            on_entering_payment=on_entering_payment,
        )
        if paid is None:
            update_checkout(
                expected_intent_id=checkout_intent_id,
                stage="order_created_pending",
                order_id=order_id,
                reason="用户取消、超时或付款状态未确认",
            )
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.CREATED_NOT_PAID,
                reason="订单已创建，用户取消、超时或付款状态未确认",
                order_id=order_id,
            )
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.SUCCESS,
            amount=paid,
            order_id=order_id,
        )

    def _try_batch_buy() -> _PurchaseAttempt:
        if getattr(buff_client, "supports_batch_buy", True) is False:
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.SAFE_TO_FALLBACK,
                reason=(
                    "BUFF 当前批量购买协议尚未安全支持，未发送批量写请求"
                ),
            )
        configured_pay_method = str(
            getattr(buff_client, "_pay_method", None)
            or buff_cfg.get("pay_method")
            or "alipay"
        ).strip().lower()
        if configured_pay_method not in getattr(buff_client, "batch_pay_methods", ("wechat",)):
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.SAFE_TO_FALLBACK,
                reason="当前支付方式不支持批量购买，且尚未发送写请求",
            )
        plan_failure = _prepare_purchase_plan()
        if plan_failure is not None:
            return plan_failure
        if num_to_buy == 1:
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.SAFE_TO_FALLBACK,
                reason="重新校验后仅可购买1件，未发送批量写请求",
            )
        if not _time_window_is_open():
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.TIME_WINDOW_CLOSED,
                reason="购买时间窗已关闭",
            )
        with get_buff_auth_lock():
            with buff_activity_guard():
                if not _time_window_is_open():
                    return _PurchaseAttempt(
                        _PurchaseAttemptStatus.TIME_WINDOW_CLOSED,
                        reason="等待请求锁期间购买时间窗已关闭",
                    )
                _verify_checkout_session(buff_client, game_buff)
                identity = _checkout_credential_identity(buff_client)
                intent = begin_checkout(
                    "batch",
                    goods_id,
                    quantity=num_to_buy,
                    credential_generation=identity.get(
                        "credential_generation",
                        0,
                    ),
                    credential_fingerprint=str(
                        identity.get("credential_fingerprint") or ""
                    ),
                    price=lowest_price,
                )
                checkout_intent_id = str(intent["intent_id"])

                def _on_batch_created(batch_id: str) -> None:
                    update_checkout(
                        expected_intent_id=checkout_intent_id,
                        stage="batch_created",
                        batch_id=str(batch_id),
                        reason="批次已创建，正在获取支付链接",
                    )

                try:
                    create_batch = buff_client.try_batch_buy
                    if _supports_keyword_argument(create_batch, "on_created"):
                        batch_kwargs = {"on_created": _on_batch_created}
                        if _supports_keyword_argument(create_batch, "can_create"):
                            batch_kwargs["can_create"] = lambda: (
                                not is_stop_requested() and _time_window_is_open()
                            )
                        batch_result = create_batch(
                            goods_id,
                            game_buff,
                            orders,
                            lowest_price,
                            num_to_buy,
                            **batch_kwargs,
                        )
                    else:
                        batch_result = create_batch(
                            goods_id,
                            game_buff,
                            orders,
                            lowest_price,
                            num_to_buy,
                        )
                except BuffWriteResultUnknown as e:
                    update_checkout(
                        expected_intent_id=checkout_intent_id,
                        stage="write_result_unknown",
                        batch_id=str(getattr(e, "batch_id", "") or ""),
                        reason=str(e) or "批量创建写请求结果未知",
                        last_error_type=type(e).__name__,
                    )
                    if log_fn:
                        log_fn(
                            f"[Buff]   → 批量创建写请求结果未知: {e}",
                            "warn",
                        )
                    return _PurchaseAttempt(
                        _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                        reason=str(e) or "批量创建写请求结果未知",
                        batch_id=str(getattr(e, "batch_id", "") or ""),
                    )
                except (BuffAuthExpired, BuffRequestBlocked):
                    resolve_checkout(
                        "auth_rejected_or_blocked_not_created",
                        expected_intent_id=checkout_intent_id,
                    )
                    raise
                except Exception as e:
                    update_checkout(
                        expected_intent_id=checkout_intent_id,
                        stage="write_result_unknown",
                        reason=str(e) or "批量购买接口异常",
                        last_error_type=type(e).__name__,
                    )
                    if log_fn:
                        log_fn(f"[Buff]   → 批量购买接口异常: {e}", "warn")
                    return _PurchaseAttempt(
                        _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                        reason=str(e) or "批量购买接口异常",
                    )
        if not batch_result or not batch_result.get("success"):
            attempt = _classify_failure(batch_result)
            if attempt.status in {
                _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                _PurchaseAttemptStatus.CREATED_NOT_PAID,
                _PurchaseAttemptStatus.COOLING_DOWN,
                _PurchaseAttemptStatus.RISK,
            }:
                update_checkout(
                    expected_intent_id=checkout_intent_id,
                    stage=(
                        "batch_created_pending"
                        if attempt.status is _PurchaseAttemptStatus.CREATED_NOT_PAID
                        else "write_result_unknown"
                    ),
                    batch_id=attempt.batch_id,
                    reason=attempt.reason,
                )
            else:
                resolve_checkout(
                    "known_not_created",
                    expected_intent_id=checkout_intent_id,
                )
            if log_fn:
                log_fn(f"[Buff]   → 批量购买未完成: {attempt.reason}", "warn")
            return attempt
        batch_id = str(batch_result.get("batch_id") or "")
        pay_url = str(batch_result.get("pay_url") or "")
        if not batch_id:
            update_checkout(
                expected_intent_id=checkout_intent_id,
                stage="write_result_unknown",
                reason="批量接口返回成功但缺少批次号",
            )
            if log_fn:
                log_fn("[Buff]   → 批量接口返回成功但缺少批次号，停止且不改单件", "warn")
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                reason="批量接口返回成功但缺少批次号",
            )
        if not pay_url:
            identity = _checkout_credential_identity(buff_client)
            update_checkout(
                expected_intent_id=checkout_intent_id,
                stage="batch_created_pending",
                batch_id=batch_id,
                reason="批次已创建但缺少支付链接",
                credential_generation=identity.get("credential_generation", 0),
                credential_fingerprint=str(
                    identity.get("credential_fingerprint") or ""
                ),
            )
            if log_fn:
                log_fn("[Buff]   → 批次已创建但缺少支付链接，停止且不改单件", "warn")
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.CREATED_NOT_PAID,
                reason="批次已创建但缺少支付链接",
                batch_id=batch_id,
            )
        if log_fn:
            log_fn(f"[Buff]   → 批量锁单成功 batch_id={batch_id} 数量={num_to_buy} 单价={lowest_price:.2f} 总价={batch_result.get('total_price', 0):.2f} 等待用户确认付款…", "info")
        identity = _checkout_credential_identity(buff_client)
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="payment_pending",
            batch_id=batch_id,
            credential_generation=identity.get("credential_generation", 0),
            credential_fingerprint=str(
                identity.get("credential_fingerprint") or ""
            ),
            reason="批次已创建，等待付款确认",
        )
        try:
            paid = _do_batch_wait_finalize_and_append(
                buff_client,
                item,
                config,
                lowest_price,
                num_to_buy,
                goods_id,
                batch_id,
                checkout_intent_id,
                game_buff,
                pay_url,
                acc,
                set_pending_payment,
                wait_payment_confirm,
                confirm_payment,
                is_stop_requested,
                append_purchase,
                log_fn,
                market_price=ref_price,
                on_entering_payment=on_entering_payment,
                pay_type=str(batch_result.get("pay_type") or "wechat"),
            )
        except BuffWriteResultUnknown as e:
            update_checkout(
                expected_intent_id=checkout_intent_id,
                stage="batch_finalize_unknown",
                batch_id=batch_id,
                reason=str(e) or "批量核销写请求结果未知",
                last_error_type=type(e).__name__,
            )
            committed_amount = float(
                getattr(e, "committed_amount", 0.0) or 0.0
            )
            if committed_amount > 0:
                halted = PurchaseWriteResultUnknown(
                    str(e) or "批量核销写请求结果未知",
                    batch_id=batch_id,
                )
                _mark_committed(
                    halted,
                    committed_amount,
                    orders=int(getattr(e, "committed_orders", 1) or 1),
                )
                raise halted from e
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                reason=str(e) or "批量核销写请求结果未知",
                batch_id=batch_id,
            )
        if paid is None:
            update_checkout(
                expected_intent_id=checkout_intent_id,
                stage="batch_created_pending",
                batch_id=batch_id,
                reason="用户取消、超时或核销状态未确认",
            )
            return _PurchaseAttempt(
                _PurchaseAttemptStatus.CREATED_NOT_PAID,
                reason="批次已创建，用户取消、超时或核销状态未确认",
                batch_id=batch_id,
            )
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.SUCCESS,
            amount=paid,
            batch_id=batch_id,
        )

    def _finish_attempt(attempt: _PurchaseAttempt) -> Optional[float]:
        if attempt.status is _PurchaseAttemptStatus.SUCCESS:
            return attempt.amount
        if attempt.status is _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND:
            raise PurchaseWriteResultUnknown(
                attempt.reason or "BUFF 写请求结果未知，已停止后续购买",
                order_id=attempt.order_id,
                batch_id=attempt.batch_id,
            )
        if attempt.status is _PurchaseAttemptStatus.CREATED_NOT_PAID:
            raise PurchaseOrderCreatedPending(
                attempt.reason or "BUFF 订单已创建但未完成，已停止后续购买",
                order_id=attempt.order_id,
                batch_id=attempt.batch_id,
            )
        if attempt.status is _PurchaseAttemptStatus.COOLING_DOWN:
            raise PurchaseCoolingDown(
                attempt.reason or "Buff Cooling Down，已停止后续购买请求",
            )
        if attempt.status is _PurchaseAttemptStatus.RISK:
            raise BuffVerificationRequired(
                attempt.reason or "Buff 返回风控/验证状态，已停止后续购买请求"
            )
        if attempt.status is _PurchaseAttemptStatus.TIME_WINDOW_CLOSED:
            return TIME_WINDOW_CLOSED
        if attempt.status is _PurchaseAttemptStatus.TARGET_REACHED:
            return TARGET_REACHED
        if attempt.status is _PurchaseAttemptStatus.SKIP_NO_FAILED:
            return SKIP_NO_FAILED
        if attempt.status is _PurchaseAttemptStatus.SKIP_VERIFICATION_FAILED:
            return SKIP_VERIFICATION_FAILED
        return None

    plan_failure = _prepare_purchase_plan()
    if plan_failure is not None:
        return _finish_attempt(plan_failure)
    if num_to_buy == 1:
        return _finish_attempt(_try_single_buy())

    batch_attempt = _try_batch_buy()
    if batch_attempt.status is _PurchaseAttemptStatus.SAFE_TO_FALLBACK:
        if log_fn:
            log_fn(f"[Buff]   → 批量未创建，降级为单件购买: {batch_attempt.reason}", "info")
        return _finish_attempt(_try_single_buy())
    return _finish_attempt(batch_attempt)
