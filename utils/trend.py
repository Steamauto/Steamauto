import math
from typing import Sequence


def _dynamic_sensitivity(price: float) -> float:
    if price < 5.0:
        return 0.015
    if price < 50.0:
        return 0.008
    return 0.004


def calculate_trend_robust(
    prices: Sequence[float],
    trend_sensitivity: float = 0.005,
    min_abs_slope: float = 0.005,
    use_dynamic_sensitivity: bool = False,
) -> int:
    if len(prices) < 3:
        return 0

    data = [float(p) for p in prices]
    sorted_data = sorted(data)
    n = len(sorted_data)
    if n % 2 == 1:
        median = sorted_data[n // 2]
    else:
        median = (sorted_data[n // 2 - 1] + sorted_data[n // 2]) / 2.0

    if median <= 0 or math.isnan(median):
        y = data
    else:
        filtered_data = [p for p in data if abs(p - median) / median < 0.3]
        if len(filtered_data) < 3:
            y = data
        else:
            y = filtered_data

    m = len(y)
    if m < 2:
        return 0

    mean_x = (m - 1) / 2.0
    mean_y = sum(y) / float(m)
    denom = sum((i - mean_x) ** 2 for i in range(m))

    if denom == 0:
        slope = 0.0
    else:
        slope = sum((i - mean_x) * (y[i] - mean_y) for i in range(m)) / denom

    intercept = mean_y - slope * mean_x
    base_price = intercept if intercept > 0 else mean_y

    if base_price <= 0:
        return 0

    if use_dynamic_sensitivity:
        trend_sensitivity = _dynamic_sensitivity(base_price)

    relative_slope = slope / base_price
    if relative_slope > trend_sensitivity and slope > min_abs_slope:
        return 1
    if relative_slope < -trend_sensitivity and slope < -min_abs_slope:
        return -1
    return 0
