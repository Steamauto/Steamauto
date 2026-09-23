import numpy as np
def _dynamic_sensitivity(price: float) -> float:
    if price < 5.0:
        return 0.015
    if price < 50.0:
        return 0.008
    return 0.004
def calculate_trend_robust(
    prices,
    trend_sensitivity: float = 0.005,
    min_abs_slope: float = 0.005,
    use_dynamic_sensitivity: bool = False,
):
    if len(prices) < 3:
        return 0
    data = np.array(prices, dtype=float)
    median = np.median(data)
    if median is None or median <= 0 or np.isnan(median):
        y = data
    else:
        filtered_data = data[np.abs(data - median) / median < 0.3]
        if len(filtered_data) < 3:
            y = data
        else:
            y = filtered_data
    x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1)
    base_price = intercept if intercept > 0 else float(np.mean(y))
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
