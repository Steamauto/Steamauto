def calculate_safe_purchase_limit(
    item_price: float,
    daily_volume: int,
    hard_qty_cap: int = 50,
    liquidity_ratio: float = 0.05,
    low_price_threshold: float = 5.0,
    low_price_penalty: float = 0.5,
    low_price_hard_cap: int = 30,
) -> int:
    if item_price <= 0:
        return 0
    volume_cap = int(daily_volume * liquidity_ratio)
    cap = hard_qty_cap
    if item_price < low_price_threshold:
        volume_cap = int(volume_cap * low_price_penalty)
        cap = low_price_hard_cap
    return max(min(volume_cap, cap), 0)
