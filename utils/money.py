import json
import math
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
USD_TO_CNY_DEFAULT = 7.2
CURRENCY_CNY = "CNY"
CURRENCY_USD = "USD"
_EXCHANGE_RATE_FILE = Path(__file__).resolve().parent.parent / "config" / "exchange_rate.json"
_EXCHANGE_RATE_TTL = 300
_exchange_rate_cache: Tuple[float, Dict[str, float]] = (0.0, {})
g_rgWalletInfo = {
    "rwgrsn": -2,
    "success": True,
    "wallet_balance": "14669",
    "wallet_country": "CN",
    "wallet_currency": 23,
    "wallet_currency_increment": "1",
    "wallet_delayed_balance": "356",
    "wallet_fee": "1",
    "wallet_fee_base": "0",
    "wallet_fee_minimum": "7",
    "wallet_fee_percent": "0.05",
    "wallet_market_minimum": "7",
    "wallet_max_balance": "1400000",
    "wallet_publisher_fee_percent_default": "0.10",
    "wallet_state": "",
    "wallet_trade_max_balance": "1260000"
}


def calculate_fee(base_amt: int, pct: float, rg_wallet: dict) -> int:
    if pct > 0:
        return max(int(rg_wallet['wallet_fee_minimum']), math.floor(base_amt * pct))
    return 0


def get_total_with_fees(base_amt: int, ppct: float, spct: float, rg_wallet: dict) -> int:
    n_base = base_amt
    n_pub_fee = calculate_fee(base_amt, ppct, rg_wallet)
    n_steam_fee = calculate_fee(base_amt, spct, rg_wallet)
    return n_base + n_pub_fee + n_steam_fee


def to_valid_market_price(n_price: int, rg_wallet: dict) -> int:
    n_floor = int(rg_wallet['wallet_market_minimum'])
    n_increment = int(rg_wallet['wallet_currency_increment'])
    if n_price <= n_floor:
        return n_floor
    if n_increment > 1:
        d_amount = n_price / n_increment
        d_sign = -1 if d_amount < 0 else 1
        d_amount = (d_sign * math.floor(abs(d_amount) + 0.5)) * n_increment
        return int(d_amount)
    return n_price


def get_item_price_from_total(n_total: int, rg_wallet: dict) -> int:
    ppct = float(rg_wallet['wallet_publisher_fee_percent_default'])
    spct = float(rg_wallet['wallet_fee_percent'])
    n_increment = int(rg_wallet['wallet_currency_increment'])
    n_floor = int(rg_wallet['wallet_market_minimum'])
    n_fee_min = int(rg_wallet['wallet_fee_minimum'])
    
    n_initial_guess = math.floor(n_total / (1.0 + ppct + spct))
    n_max_base = n_total - (2 * n_fee_min)
    n_base = to_valid_market_price(min(n_initial_guess, n_max_base), rg_wallet)
    
    for _ in range(3):
        n_calculated = get_total_with_fees(n_base, ppct, spct, rg_wallet)
        if n_calculated == n_total:
            return n_base
        if n_calculated < n_total:
            n_base += n_increment
        else:
            n_base -= n_increment
            break
    return max(n_floor, n_base)


def usd_to_cny(amount: float, rate: float = USD_TO_CNY_DEFAULT) -> float:
    return amount * rate
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
    except Exception:
        rates = {}
    _exchange_rate_cache = (now, rates)
    return rates
def apply_currency(
    prices: List[float],
    currency: Optional[str],
    usd_to_cny_rate: float = USD_TO_CNY_DEFAULT,
    rate_map: Optional[Dict[str, float]] = None,
) -> Tuple[List[float], str]:
    code = (currency or CURRENCY_CNY).strip().upper()
    if code == CURRENCY_CNY:
        return prices, CURRENCY_CNY
    rates = rate_map if rate_map is not None else _load_exchange_rates()
    if code == CURRENCY_USD:
        rate = rates.get(CURRENCY_USD) or usd_to_cny_rate
        return [p * rate for p in prices], CURRENCY_CNY
    rate = rates.get(code)
    if rate:
        return [p * rate for p in prices], CURRENCY_CNY
    return prices, code
def yuan_to_cents(yuan: float) -> int:
    return max(1, int(round(yuan * 100)))


def list_price_display_to_cents(display_amount: float, account_currency: str = "CNY") -> int:
    display_amount = round(display_amount, 2)
    total_cents = max(1, int(round(display_amount * 100)))
    
    wallet_info = dict(g_rgWalletInfo)
    if account_currency.upper() != "CNY":
        wallet_info["wallet_fee_minimum"] = "1"
        wallet_info["wallet_market_minimum"] = "1"
        
    return get_item_price_from_total(total_cents, wallet_info)
def cents_to_yuan(cents: int) -> float:
    return cents / 100.0
