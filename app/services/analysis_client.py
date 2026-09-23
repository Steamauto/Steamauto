from typing import Any, Dict, List, Optional
from analysis import analyze_by_time as _analyze_by_time
from utils.money import USD_TO_CNY_DEFAULT
class StabilityAnalyzer:
    def __init__(self, usd_to_cny: float = USD_TO_CNY_DEFAULT) -> None:
        self._usd_to_cny = usd_to_cny
    def analyze(
        self,
        history: Optional[List],
        days: int = 30,
        currency: Optional[str] = None,
        cv_threshold: float = 0.05,
        r2_threshold: float = 0.6,
        min_daily_trades: float = 5,
        current_price: Optional[float] = None,
        price_percentile_ceil: float = 0.8,
        r2_rising_threshold: float = 0.8,
        slope_pct_ceil: float = 0.01,
        ma_deviation_ceil: float = 1.1,
        last_price_ma30_ceil: float = 1.05,
        slope_stable_floor: float = -0.005,
        price_percentile_ceil_rising: float = 0.5,
        use_vwap: bool = True,
    ) -> Dict[str, Any]:
        return _analyze_by_time(
            history,
            days=days,
            currency=currency,
            usd_to_cny=self._usd_to_cny,
            cv_threshold=cv_threshold,
            r2_threshold=r2_threshold,
            min_daily_trades=min_daily_trades,
            current_price=current_price,
            price_percentile_ceil=price_percentile_ceil,
            r2_rising_threshold=r2_rising_threshold,
            slope_pct_ceil=slope_pct_ceil,
            ma_deviation_ceil=ma_deviation_ceil,
            last_price_ma30_ceil=last_price_ma30_ceil,
            slope_stable_floor=slope_stable_floor,
            price_percentile_ceil_rising=price_percentile_ceil_rising,
            use_vwap=use_vwap,
        )
