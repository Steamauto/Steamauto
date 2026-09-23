"""Data-source client for the pipeline.

Previously used the iflow website (Playwright-based HTML scraping).
Now replaced with the SteamDT JSON API — no browser needed, much faster
and more reliable.

The public API (IflowClient / fetch_iflow_rows) is intentionally kept
unchanged so that every other module that imports from here continues
to work without modifications.
"""

import math
from typing import Any, Dict, List, Optional

steamdt_fetch_timeout = 15   # HTTP-only, much faster than Playwright
steamdt_retry_attempts = 3

# iflow sort_by → SteamDT salePlan 映射
# SteamDT 的排序是通过 salePlan 参数由服务端完成的
_SORT_BY_TO_SALE_PLAN = {
    "sell": "STEAM_SELL_PRICE",       # 最优寄售（Steam 最低售价）
    "buy":  "STEAM_PURCHASE_PRICE",   # 最优求购（Steam 最高求购价）
}


class IflowClient:
    """Drop-in replacement that fetches from SteamDT instead of iflow.

    The class name is kept as ``IflowClient`` for backward compatibility
    with imports throughout the codebase.
    """

    def __init__(self, timeout_sec: int = steamdt_fetch_timeout) -> None:
        self._timeout = timeout_sec

    def fetch(self, params: Optional[Dict[str, Any]] = None, headless: bool = True) -> List[Any]:
        from steamdt.models import SteamDTQueryParams
        from steamdt.fetcher import fetch_steamdt_data

        p = params or {}

        # Map iflow-style config keys → SteamDT query params
        # Also accept native SteamDT keys if already present
        platform_raw = p.get("platforms", p.get("platform_list", "buff"))
        if isinstance(platform_raw, str):
            # "buff-c5" → ["BUFF", "C5"],  "buff" → ["BUFF"]
            platform_list = [s.strip().upper() for s in platform_raw.replace("-", ",").split(",") if s.strip()]
        elif isinstance(platform_raw, list):
            platform_list = [s.strip().upper() for s in platform_raw if s.strip()]
        else:
            platform_list = ["BUFF"]

        query = SteamDTQueryParams(
            page=int(p.get("page_num", p.get("page", 1))),
            page_size=int(p.get("page_size", 200)),
            type=p.get("type", "swap"),
            want_to_get=p.get("want_to_get", "STEAM_BALANCE"),
            purchase_plan=p.get("purchase_plan", ""),
            sale_plan=p.get("sale_plan", "STEAM_SELL_PRICE"),
            min_sell_price=str(p.get("min_price", p.get("min_sell_price", "2"))),
            max_sell_price=int(p.get("max_price", p.get("max_sell_price", 5000))),
            min_transaction_count=str(p.get("min_volume", p.get("min_transaction_count", "200"))),
            platform_list=platform_list,
            currency=p.get("currency", "CNY"),
            language=p.get("language", "zh_CN"),
        )

        # Determine target platform for link extraction (use the first one)
        target = platform_list[0] if platform_list else "BUFF"

        return fetch_steamdt_data(
            query,
            timeout=float(self._timeout),
            attempts=steamdt_retry_attempts,
            target_platform=target,
        )


def fetch_iflow_rows(config: dict) -> List[Any]:
    """Fetch deal rows from SteamDT.

    Function name kept as ``fetch_iflow_rows`` for backward compat.
    Reads from config["iflow"] (or config["steamdt"] if present) section.
    """
    # Prefer "steamdt" config section, fall back to "iflow" for compat
    iflow_cfg = config.get("steamdt") or config.get("iflow", {})
    pipeline_cfg = config.get("pipeline", {})
    top_n = int(pipeline_cfg.get("iflow_top_n", 50) or 50)

    client = IflowClient(timeout_sec=int(iflow_cfg.get("fetch_timeout", steamdt_fetch_timeout)))

    # sort_by → salePlan: 排序由 SteamDT 服务端完成
    sort_by = (iflow_cfg.get("sort_by") or "sell").strip()
    sale_plan = iflow_cfg.get("sale_plan") or _SORT_BY_TO_SALE_PLAN.get(sort_by, "STEAM_SELL_PRICE")

    params = {
        "page_num": iflow_cfg.get("page_num", 1),
        "page_size": int(iflow_cfg.get("page_size", 200)),
        "platforms": iflow_cfg.get("platforms", "buff"),
        "min_price": iflow_cfg.get("min_price", 2),
        "max_price": iflow_cfg.get("max_price", 5000),
        "min_volume": iflow_cfg.get("min_volume", 200),
        # SteamDT-specific params
        "type": iflow_cfg.get("type", "swap"),
        "want_to_get": iflow_cfg.get("want_to_get", "STEAM_BALANCE"),
        "sale_plan": sale_plan,
    }

    if top_n <= 0:
        target_pages = 1
    else:
        page_size = int(params.get("page_size", 200))
        target_pages = math.ceil(top_n / max(page_size, 1))

    all_rows: List[Any] = []
    start_page = int(params["page_num"])
    page_size = int(params.get("page_size", 200))

    for page_offset in range(target_pages):
        current_page = start_page + page_offset
        params["page_num"] = current_page

        page_rows = client.fetch(params, headless=True)
        if not page_rows:
            break

        all_rows.extend(page_rows)

        if len(page_rows) < page_size:
            break

        if top_n > 0 and len(all_rows) >= top_n:
            break

    return all_rows
