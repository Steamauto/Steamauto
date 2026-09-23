import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from utils.time import parse_steam_history_date

CV_STABLE = 0.15
CV_STABLE_LOW = 0.05
MIN_TRADES = 5
MIN_DAILY_TRADES = 5
TRIM_RATIO = 0.1
SLOPE_DAYS = 7
SLOPE_DOWN_THRESHOLD = -0.5
R2_TREND_THRESHOLD = 0.6
USD_TO_CNY = 7.2
STATUS_STABLE = "STABLE"
STATUS_RISING = "RISING"
STATUS_FALLING = "FALLING"
STATUS_CHAOS = "CHAOS"
STATUS_UNKNOWN = "UNKNOWN"
def _percentile(sorted_arr: List[float], p: float) -> float:
    n = len(sorted_arr)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_arr[0]
    pos = (n - 1) * p / 100.0
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_arr[lo] * (1 - frac) + sorted_arr[hi] * frac
def _iqr_bounds(prices: List[float]) -> Tuple[float, float]:
    if not prices or len(prices) < 3:
        return float("-inf"), float("inf")
    sorted_prices = sorted(prices)
    n = len(sorted_prices)
    q1 = _percentile(sorted_prices, 25)
    q3 = _percentile(sorted_prices, 75)
    iqr = q3 - q1
    mean_p = statistics.mean(sorted_prices)
    min_buffer = max(0.5, mean_p * 0.05)
    effective_iqr = max(iqr, min_buffer)
    return q1 - 1.5 * effective_iqr, q3 + 1.5 * effective_iqr
def clean_prices_iqr(prices: List[float]) -> List[float]:
    if not prices or len(prices) < 3:
        return list(prices)
    sorted_prices = sorted(prices)
    lower_bound, upper_bound = _iqr_bounds(prices)
    clean = [p for p in sorted_prices if lower_bound <= p <= upper_bound]
    if len(clean) == 0:
        trim_count = max(1, int(len(sorted_prices) * 0.1))
        n = len(sorted_prices)
        return sorted_prices[trim_count : n - trim_count] if trim_count * 2 < n else sorted_prices
    return clean
def _vwap_iqr(prices: List[float], volumes: List[int]) -> Optional[float]:
    if not prices or len(prices) != len(volumes):
        return None
    lower, upper = _iqr_bounds(prices)
    sum_pv = 0.0
    sum_v = 0
    for p, v in zip(prices, volumes):
        if v > 0 and lower <= p <= upper:
            sum_pv += p * v
            sum_v += v
    if sum_v <= 0:
        return None
    return sum_pv / sum_v
def _apply_currency(prices: list, currency: Optional[str], usd_to_cny: float) -> tuple:
    from utils.money import apply_currency
    return apply_currency(prices, currency, usd_to_cny)
def _parse_item_date(date_str: str) -> Optional[datetime]:
    return parse_steam_history_date(date_str)
def _safe_volume(item: list) -> int:
    if len(item) < 3:
        return 0
    try:
        return int(item[2]) if isinstance(item[2], str) else int(item[2])
    except (ValueError, TypeError):
        return 0
def _linear_regression_slope(ys: List[float]) -> Optional[float]:
    n = len(ys)
    if n < 2:
        return None
    xs = list(range(n))
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return None
    return (n * sum_xy - sum_x * sum_y) / denom
def _linear_regression_r_squared(ys: List[float]) -> Optional[float]:
    n = len(ys)
    if n < 2:
        return None
    slope = _linear_regression_slope(ys)
    if slope is None:
        return None
    xs = list(range(n))
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    intercept = mean_y - slope * mean_x
    y_pred = [slope * x + intercept for x in xs]
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot == 0:
        return 0.0
    ss_res = sum((y - yp) ** 2 for y, yp in zip(ys, y_pred))
    return 1.0 - ss_res / ss_tot
def _ema(values: List[float], span: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (span + 1.0)
    ema = values[0]
    for v in values[1:]:
        ema = v * alpha + ema * (1.0 - alpha)
    return ema
def _analyze_market_status(
    slope: float,
    r_squared: float,
    r2_threshold: float = R2_TREND_THRESHOLD,
) -> str:
    if r_squared > r2_threshold:
        if slope > 0:
            return STATUS_RISING
        else:
            return STATUS_FALLING
    return STATUS_STABLE
def _daily_avg_prices_last_n(
    dt_prices: List[Tuple[datetime, float]], n: int = SLOPE_DAYS
) -> List[float]:
    by_day: dict = defaultdict(list)
    for dt, p in dt_prices:
        by_day[dt.date()].append(p)
    daily = [(d, statistics.mean(ps)) for d, ps in sorted(by_day.items())]
    if len(daily) <= n:
        return [p for _, p in daily]
    return [p for _, p in daily[-n:]]
def analyze_by_time(
    history: Optional[list],
    days: int = 30,
    *,
    currency: Optional[str] = None,
    usd_to_cny: float = USD_TO_CNY,
    cv_threshold: float = CV_STABLE_LOW,
    min_daily_trades: float = MIN_DAILY_TRADES,
    trim_ratio: float = TRIM_RATIO,
    slope_down_threshold: float = SLOPE_DOWN_THRESHOLD,
    slope_days: int = SLOPE_DAYS,
    r2_threshold: float = R2_TREND_THRESHOLD,
    current_price: Optional[float] = None,
    price_percentile_ceil: float = 0.8,
    r2_rising_threshold: float = 0.8,
    slope_pct_ceil: float = 0.01,
    ma_deviation_ceil: float = 1.1,
    last_price_ma30_ceil: float = 1.05,
    slope_stable_floor: float = -0.005,
    price_percentile_ceil_rising: float = 0.5,
    use_vwap: bool = True,
) -> dict:
    if not history:
        return {"valid": False, "msg": "无历史数据"}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    dt_prices: List[Tuple[datetime, float]] = []
    volumes = []
    for item in history:
        if len(item) < 2:
            continue
        dt = _parse_item_date(str(item[0]))
        if dt is None or dt < cutoff:
            continue
        try:
            p = float(item[1])
            dt_prices.append((dt, p))
            volumes.append(_safe_volume(item))
        except (ValueError, TypeError):
            continue
    if not dt_prices:
        return {"valid": False, "msg": "无有效价格数据"}
    raw_prices = [p for _, p in dt_prices]
    prices, out_currency = _apply_currency(raw_prices, currency, usd_to_cny)
    dt_prices_cny = [(dt, prices[i]) for i, (dt, _) in enumerate(dt_prices)]
    count = len(prices)
    if count < MIN_TRADES:
        return {"valid": False, "msg": f"最近 {days} 天内成交过少 ({count} 单)"}
    clean_prices = clean_prices_iqr(prices)
    last_price = max(dt_prices_cny, key=lambda x: x[0])[1] if dt_prices_cny else None
    if current_price is None and last_price is not None:
        current_price = last_price
    avg = statistics.mean(clean_prices)
    vwap = _vwap_iqr(prices, volumes) if use_vwap else None
    ref_price = vwap if (use_vwap and vwap is not None) else avg
    stdev = statistics.stdev(clean_prices) if len(clean_prices) > 1 else 0
    cv = stdev / avg if avg > 0 else 0
    total_volume = sum(volumes)
    daily_last = _daily_avg_prices_last_n(dt_prices_cny, n=slope_days)
    daily_30 = _daily_avg_prices_last_n(dt_prices_cny, n=min(30, days))
    ma7 = _ema(daily_last, span=slope_days) if daily_last else 0.0
    ma30 = _ema(daily_30, span=min(30, days)) if daily_30 else 0.0
    bb_stdev = statistics.stdev(daily_30) if len(daily_30) > 1 else 0.0
    min_band = ma30 * 0.02
    bb_upper = ma30 + max(2 * bb_stdev, min_band)
    bb_lower = ma30 - max(2 * bb_stdev, min_band)
    slope = _linear_regression_slope(daily_last)
    if slope is None:
        slope = 0.0
    r_squared = _linear_regression_r_squared(daily_last)
    if r_squared is None:
        r_squared = 0.0
    cv_filter_enabled = cv_threshold < 1
    actual_cv_threshold = cv_threshold
    ref_for_cv = current_price if current_price is not None else ref_price
    if cv_filter_enabled and ref_for_cv is not None and ref_for_cv > 0:
        if ref_for_cv <= 15.0:
            actual_cv_threshold = max(cv_threshold, 0.08)
        elif ref_for_cv >= 100.0:
            actual_cv_threshold = min(cv_threshold, 0.04)
        else:
            ratio = (ref_for_cv - 15.0) / 85.0
            interpolated = 0.08 - ratio * 0.04
            actual_cv_threshold = interpolated
    reasons = []
    if len(daily_last) < 5:
        status = STATUS_UNKNOWN
        is_stable = False
        reasons.append(f"近{slope_days}天均价点仅{len(daily_last)}个(需>=5)")
    else:
        status = _analyze_market_status(
            slope, r_squared,
            r2_threshold=r2_threshold,
        )
        base_ok = count > (days * min_daily_trades)
        if not base_ok:
            reasons.append(f"总交易数{count}过低(要求>={int(days * min_daily_trades)})")

        if status == STATUS_STABLE:
            is_stable = base_ok and slope >= slope_stable_floor and (not cv_filter_enabled or cv <= actual_cv_threshold)
            if not is_stable:
                if slope < slope_stable_floor:
                    reasons.append(f"趋势下跌(斜率{slope:.4f}<{slope_stable_floor})")
                if cv_filter_enabled and cv > actual_cv_threshold:
                    reasons.append(f"波动过大(CV={cv:.4f}>{actual_cv_threshold:.4f})")
        elif status == STATUS_RISING:
            slope_pct = (slope / ref_price) if ref_price > 0 else 1.0
            is_stable = base_ok and r_squared > r2_rising_threshold and slope_pct <= slope_pct_ceil and (not cv_filter_enabled or cv <= actual_cv_threshold)
            if not is_stable:
                if r_squared <= r2_rising_threshold:
                    reasons.append(f"上涨趋势分散(R²={r_squared:.4f}<={r2_rising_threshold})")
                if slope_pct > slope_pct_ceil:
                    reasons.append(f"暴涨风险(斜率占比={slope_pct:.4f}>{slope_pct_ceil})")
                if cv_filter_enabled and cv > actual_cv_threshold:
                    reasons.append(f"波动过大(CV={cv:.4f}>{actual_cv_threshold:.4f})")
        else:
            is_stable = False
            reasons.append(f"趋势异常({status})")

    price_min = min(clean_prices)
    price_max = max(clean_prices)
    percentile_ceil = price_percentile_ceil_rising if status == STATUS_RISING else price_percentile_ceil
    price_percentile: Optional[float] = None
    if current_price is not None and price_max > price_min:
        price_percentile = (current_price - price_min) / (price_max - price_min)
        if percentile_ceil < 1 and price_percentile > percentile_ceil:
            is_stable = False
            reasons.append(f"当前价处于历史高位(分位={price_percentile:.2f}>{percentile_ceil})")
    elif current_price is not None and price_max == price_min:
        price_percentile = 0.5

    recent_percentile: Optional[float] = None
    cutoff_14 = datetime.now(timezone.utc) - timedelta(days=14)
    recent_prices_cny = [p for dt, p in dt_prices_cny if dt >= cutoff_14]
    if recent_prices_cny:
        recent_clean = clean_prices_iqr(recent_prices_cny)
        if recent_clean:
            recent_min = min(recent_clean)
            recent_max = max(recent_clean)
            if current_price is not None and recent_max > recent_min:
                recent_percentile = (current_price - recent_min) / (recent_max - recent_min)
                if percentile_ceil < 1 and recent_percentile > percentile_ceil:
                    is_stable = False
                    reasons.append(f"当前价处于近14天高位(分位={recent_percentile:.2f}>{percentile_ceil})")
            elif current_price is not None and recent_max == recent_min:
                recent_percentile = 0.5

    if ma_deviation_ceil < 999 and ma30 > 0 and ma7 > bb_upper:
        is_stable = False
        reasons.append(f"近期均线暴涨(EMA7={ma7:.2f}>BB+={bb_upper:.2f})")

    last_price_ma30_ratio: Optional[float] = None
    last_price_ma30_ceil_exceeded = False
    if ma30 > 0 and last_price is not None:
        last_price_ma30_ratio = last_price / ma30
        if last_price_ma30_ceil < 999 and (last_price > bb_upper or last_price < bb_lower):
            is_stable = False
            if last_price > bb_upper:
                last_price_ma30_ceil_exceeded = True
                reasons.append(f"最后成交价偏高({last_price:.2f}>BB+={bb_upper:.2f})")
            else:
                reasons.append(f"最后成交价偏低({last_price:.2f}<BB-={bb_lower:.2f})")

    if slope > 0:
        trend = "up"
    elif slope < slope_down_threshold:
        trend = "down"
    else:
        trend = "sideways"
    return {
        "valid": True,
        "days": days,
        "count": count,
        "total_volume": total_volume,
        "avg_daily_volume": round(total_volume / days, 1),
        "avg": round(avg, 2),
        "median": round(statistics.median(clean_prices), 2),
        "min": min(clean_prices),
        "max": max(clean_prices),
        "cv": round(cv, 4),
        "slope": round(slope, 4),
        "r_squared": round(r_squared, 4),
        "trend": trend,
        "status": status,
        "is_stable": is_stable,
        "msg": (" | ".join(reasons) or "未满足基础指标") if not is_stable else "验证通过",
        "currency": out_currency,
        "price_percentile": round(price_percentile, 4) if price_percentile is not None else None,
        "recent_percentile": round(recent_percentile, 4) if recent_percentile is not None else None,
        "ma7": round(ma7, 2),
        "ma30": round(ma30, 2),
        "last_price": round(last_price, 2) if last_price is not None else None,
        "last_price_ma30_ratio": round(last_price_ma30_ratio, 4) if last_price_ma30_ratio is not None else None,
        "last_price_ma30_ceil_exceeded": last_price_ma30_ceil_exceeded,
        "vwap": round(vwap, 2) if vwap is not None else None,
        "percentile_ceil": percentile_ceil,
        "bb_upper": round(bb_upper, 2) if 'bb_upper' in locals() else None,
        "bb_lower": round(bb_lower, 2) if 'bb_lower' in locals() else None,
    }
def calculate_stability(
    history: Optional[list],
    window: int = 300,
    *,
    currency: Optional[str] = None,
    usd_to_cny: float = USD_TO_CNY,
    cv_threshold: float = CV_STABLE,
) -> Optional[dict]:
    if not history:
        return None
    recent = history[-window:]
    prices = [float(item[1]) for item in recent if len(item) > 1]
    if not prices:
        return None
    prices, out_currency = _apply_currency(prices, currency, usd_to_cny)
    avg = statistics.mean(prices)
    stdev = statistics.stdev(prices) if len(prices) > 1 else 0
    cv = stdev / avg if avg > 0 else 0
    return {
        "avg": round(avg, 2),
        "median": round(statistics.median(prices), 2),
        "min": min(prices),
        "max": max(prices),
        "cv": round(cv, 3),
        "is_stable": cv < cv_threshold,
        "count": len(prices),
        "currency": out_currency,
    }
