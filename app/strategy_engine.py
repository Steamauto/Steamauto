import copy
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config_loader import load_app_config_validated, save_app_config_validated
from config import get_app_config_path


STRATEGY_STORE_PATH = get_app_config_path().parent / "strategies.json"
USER_MODULE_STORE_PATH = get_app_config_path().parent / "strategy_modules.json"

STRATEGY_TYPES = {"buy", "sell"}
SELL_LEGACY_TO_SYSTEM = {
    1: "system.sell.immediate",
    2: "system.sell.trend",
    3: "system.sell.profit_guard",
    4: "system.sell.pause",
}
SELL_SYSTEM_TO_LEGACY = {v: k for k, v in SELL_LEGACY_TO_SYSTEM.items()}

SENSITIVE_KEY_RE = re.compile(r"(cookie|token|secret|password|passwd|session|credential)", re.I)

DEFAULT_MODULE_MAX_INSTANCES = 1
STRATEGY_STEP_LIMITS = {"buy": 22, "sell": 14}
BUY_REQUIRED_MODULES = [
    "buy.steamdt_top_n",
    "buy.basic_candidate_filter",
    "buy.buff_realtime_price",
    "buy.steam_sell_depth",
    "guard.target_balance",
    "action.buff_lock_pay",
]
SELL_LIST_REQUIRED_MODULES = [
    "sell.sellable_inventory_filter",
    "guard.max_listings_per_item",
]
SELL_PRICING_CORE_MODULES = [
    "pricing.steam_wall_price",
    "pricing.steam_wall_gap",
]


BUILTIN_MODULES: Dict[str, Dict[str, Any]] = {
    "buy.steamdt_top_n": {
        "id": "buy.steamdt_top_n",
        "name": "SteamDT 取前 N 条",
        "category": "buy.source",
        "strategy_types": ["buy"],
        "description": "限制本轮从 SteamDT 候选中检查的最大数量。",
        "params_schema": {"iflow_top_n": {"type": "integer", "min": 0, "default": 50, "label": "SteamDT 取前 N 条"}},
    },
    "buy.exclude_keywords": {
        "id": "buy.exclude_keywords",
        "name": "关键词排除",
        "category": "buy.filter",
        "strategy_types": ["buy"],
        "description": "排除名称命中关键词的候选饰品。",
        "params_schema": {"exclude_keywords": {"type": "array", "default": ["印花"], "label": "关键词排除"}},
    },
    "buy.basic_candidate_filter": {
        "id": "buy.basic_candidate_filter",
        "name": "基础候选校验",
        "category": "buy.filter",
        "strategy_types": ["buy"],
        "description": "过滤无效价格、无 Buff 链接或 goods_id 缺失的候选。",
        "params_schema": {},
    },
    "buy.buff_realtime_price": {
        "id": "buy.buff_realtime_price",
        "name": "Buff 实时卖单",
        "category": "buy.data",
        "strategy_types": ["buy"],
        "description": "购买前刷新 Buff 卖单，避免按过期候选价锁单。",
        "params_schema": {},
    },
    "buy.steam_sell_depth": {
        "id": "buy.steam_sell_depth",
        "name": "Steam 卖单深度",
        "category": "buy.data",
        "strategy_types": ["buy"],
        "description": "拉取 Steam 卖单并计算智能参考价。",
        "params_schema": {},
    },
    "guard.max_discount": {
        "id": "guard.max_discount",
        "name": "最高折扣",
        "category": "guard",
        "strategy_types": ["buy"],
        "description": "要求 (Buff 价格 / Steam 参考价) × 1.15 低于阈值。",
        "params_schema": {"max_discount": {"type": "number", "min": 0.001, "max": 1, "default": 0.9, "label": "最高折扣"}},
    },
    "guard.sell_pressure": {
        "id": "guard.sell_pressure",
        "name": "卖压保护",
        "category": "guard",
        "strategy_types": ["buy"],
        "description": "用 Steam 前 N 档卖单总量与日销量比值过滤卖压过高的饰品。",
        "params_schema": {
            "sell_pressure_orders_n": {"type": "integer", "min": 1, "default": 5, "label": "卖压前 N 档"},
            "sell_pressure_threshold": {"type": "number", "min": 0, "default": 2.0, "label": "卖压阈值"},
        },
    },
    "guard.history_stability": {
        "id": "guard.history_stability",
        "name": "历史稳定性（兼容）",
        "category": "guard.legacy",
        "strategy_types": ["buy"],
        "description": "旧版合并模块，仅用于兼容已保存策略；新策略请使用拆分后的历史模块。",
        "hidden": True,
        "params_schema": {
            "days": {"type": "integer", "min": 1, "default": 30, "label": "统计天数"},
            "cv_threshold": {"type": "number", "min": 0.001, "max": 0.999, "default": 0.05, "label": "CV 阈值"},
            "r2_threshold": {"type": "number", "min": 0.001, "max": 0.999, "default": 0.6, "label": "R² 趋势阈值"},
            "min_daily_trades": {"type": "number", "min": 0, "default": 5, "label": "最低日成交数"},
            "price_percentile_ceil": {"type": "number", "min": 0.001, "max": 1, "default": 0.8, "label": "价格分位上限"},
            "r2_rising_threshold": {"type": "number", "min": 0.001, "max": 0.999, "default": 0.8, "label": "RISING R² 审慎阈值"},
            "slope_pct_ceil": {"type": "number", "min": 0, "default": 0.01, "label": "RISING 日斜率上限"},
            "ma_deviation_ceil": {"type": "number", "min": 1, "default": 1.1, "label": "MA7/MA30 偏离上限"},
            "last_price_ma30_ceil": {"type": "number", "min": 1, "default": 1.05, "label": "last_price/MA30 上限"},
            "slope_stable_floor": {"type": "number", "default": -0.005, "label": "STABLE slope 下限"},
            "price_percentile_ceil_rising": {"type": "number", "min": 0.001, "max": 1, "default": 0.5, "label": "RISING 分位上限"},
            "use_vwap": {"type": "boolean", "default": True, "label": "使用 VWAP"},
        },
    },
    "guard.history_data_window": {
        "id": "guard.history_data_window",
        "name": "历史数据窗口",
        "category": "guard.history",
        "strategy_types": ["buy"],
        "description": "控制历史分析使用的天数、最低成交活跃度和参考均价方式。",
        "params_schema": {
            "days": {"type": "integer", "min": 1, "default": 30, "label": "统计天数"},
            "min_daily_trades": {"type": "number", "min": 0, "default": 5, "label": "最低日成交数"},
            "use_vwap": {"type": "boolean", "default": True, "label": "使用 VWAP"},
        },
    },
    "guard.volatility_cv": {
        "id": "guard.volatility_cv",
        "name": "波动率过滤",
        "category": "guard.history",
        "strategy_types": ["buy"],
        "description": "按历史价格 CV 波动率过滤不稳定饰品。",
        "params_schema": {
            "cv_threshold": {"type": "number", "min": 0.001, "max": 0.999, "default": 0.05, "label": "CV 阈值"},
        },
    },
    "guard.trend_quality": {
        "id": "guard.trend_quality",
        "name": "趋势质量过滤",
        "category": "guard.history",
        "strategy_types": ["buy"],
        "description": "控制趋势拟合、上涨趋势审慎阈值和稳定趋势斜率下限。",
        "params_schema": {
            "r2_threshold": {"type": "number", "min": 0.001, "max": 0.999, "default": 0.6, "label": "R² 趋势阈值"},
            "r2_rising_threshold": {"type": "number", "min": 0.001, "max": 0.999, "default": 0.8, "label": "RISING R² 审慎阈值"},
            "slope_pct_ceil": {"type": "number", "min": 0, "default": 0.01, "label": "RISING 日斜率上限"},
            "slope_stable_floor": {"type": "number", "default": -0.005, "label": "STABLE slope 下限"},
        },
    },
    "guard.price_position": {
        "id": "guard.price_position",
        "name": "价格位置过滤",
        "category": "guard.history",
        "strategy_types": ["buy"],
        "description": "按价格分位、均线偏离和最后成交价位置过滤追高风险。",
        "params_schema": {
            "price_percentile_ceil": {"type": "number", "min": 0.001, "max": 1, "default": 0.8, "label": "价格分位上限"},
            "price_percentile_ceil_rising": {"type": "number", "min": 0.001, "max": 1, "default": 0.5, "label": "RISING 分位上限"},
            "ma_deviation_ceil": {"type": "number", "min": 1, "default": 1.1, "label": "MA7/MA30 偏离上限"},
            "last_price_ma30_ceil": {"type": "number", "min": 1, "default": 1.05, "label": "last_price/MA30 上限"},
        },
    },
    "guard.safe_purchase_limit": {
        "id": "guard.safe_purchase_limit",
        "name": "安全采购上限（兼容）",
        "category": "guard.legacy",
        "strategy_types": ["buy"],
        "description": "旧版合并模块，仅用于兼容已保存策略；新策略请使用拆分后的采购上限模块。",
        "hidden": True,
        "params_schema": {
            "safe_purchase_hard_qty_cap": {"type": "integer", "min": 1, "default": 50, "label": "硬上限"},
            "safe_purchase_liquidity_ratio": {"type": "number", "min": 0, "default": 0.05, "label": "流动性比例"},
            "safe_purchase_low_price_threshold": {"type": "number", "min": 0, "default": 5.0, "label": "低价阈值"},
            "safe_purchase_low_price_penalty": {"type": "number", "min": 0, "default": 0.5, "label": "低价惩罚系数"},
            "safe_purchase_low_price_hard_cap": {"type": "integer", "min": 1, "default": 30, "label": "低价硬上限"},
        },
    },
    "guard.purchase_hard_cap": {
        "id": "guard.purchase_hard_cap",
        "name": "单品硬数量上限",
        "category": "guard.purchase_limit",
        "strategy_types": ["buy"],
        "description": "给单个饰品设置固定采购数量上限，避免单品仓位过大。",
        "params_schema": {
            "safe_purchase_hard_qty_cap": {"type": "integer", "min": 1, "default": 50, "label": "硬上限"},
        },
    },
    "guard.purchase_liquidity_cap": {
        "id": "guard.purchase_liquidity_cap",
        "name": "流动性采购上限",
        "category": "guard.purchase_limit",
        "strategy_types": ["buy"],
        "description": "按日销量比例限制采购数量，避免一次买掉过多流动性。",
        "params_schema": {
            "safe_purchase_liquidity_ratio": {"type": "number", "min": 0, "default": 0.05, "label": "日销量比例"},
        },
    },
    "guard.low_price_purchase_guard": {
        "id": "guard.low_price_purchase_guard",
        "name": "低价采购惩罚",
        "category": "guard.purchase_limit",
        "strategy_types": ["buy"],
        "description": "低于指定价格时收紧采购数量，降低低价垃圾单品堆仓风险。",
        "params_schema": {
            "safe_purchase_low_price_threshold": {"type": "number", "min": 0, "default": 5.0, "label": "低价阈值"},
            "safe_purchase_low_price_penalty": {"type": "number", "min": 0, "default": 0.5, "label": "流动性惩罚系数"},
            "safe_purchase_low_price_hard_cap": {"type": "integer", "min": 1, "default": 30, "label": "低价硬上限"},
        },
    },
    "guard.held_same_item_guard": {
        "id": "guard.held_same_item_guard",
        "name": "同名持仓扣减",
        "category": "guard.purchase_limit",
        "strategy_types": ["buy"],
        "description": "采购上限会扣除当前已持有的同名饰品数量，避免继续加仓同一品种。",
        "params_schema": {},
    },
    "guard.target_balance": {
        "id": "guard.target_balance",
        "name": "目标余额保护",
        "category": "guard",
        "strategy_types": ["buy"],
        "description": "限制本轮买入累计金额不超过目标余额。",
        "params_schema": {"target_balance": {"type": "number", "min": 0, "default": 100, "label": "目标余额"}},
        "safety_required": True,
    },
    "action.buff_lock_pay": {
        "id": "action.buff_lock_pay",
        "name": "Buff 锁单付款",
        "category": "action",
        "strategy_types": ["buy"],
        "description": "执行 Buff 锁单、付款等待、记录购买和催发货。",
        "params_schema": {},
    },
    "sell.sellable_inventory_filter": {
        "id": "sell.sellable_inventory_filter",
        "name": "可出售库存",
        "category": "sell.filter",
        "strategy_types": ["sell"],
        "description": "仅处理 Steam 标记为可出售的库存物品。",
        "params_schema": {},
    },
    "guard.max_listings_per_item": {
        "id": "guard.max_listings_per_item",
        "name": "同名在售上限",
        "category": "guard",
        "strategy_types": ["sell"],
        "description": "同一饰品名的在售数量达到上限后不再继续上架。",
        "params_schema": {"max_listings_per_item": {"type": "integer", "min": 1, "default": 5, "label": "同名在售上限"}},
    },
    "pricing.steam_wall_gap": {
        "id": "pricing.steam_wall_gap",
        "name": "墙+断层一体定价",
        "category": "pricing",
        "strategy_types": ["sell"],
        "description": "一体化计算基础上架价和价格补偿，可替换拆分后的价格墙定价模块。",
        "params_schema": {
            "sell_price_wall_volume": {"type": "integer", "min": 1, "default": 20, "label": "价格墙数量阈值"},
            "sell_price_max_ignore_volume": {"type": "integer", "min": 0, "default": 4, "label": "断层跳跃容忍量"},
            "sell_price_offset": {"type": "number", "default": 0, "label": "价格补偿"},
        },
    },
    "pricing.steam_wall_price": {
        "id": "pricing.steam_wall_price",
        "name": "价格墙定价",
        "category": "pricing",
        "strategy_types": ["sell"],
        "description": "基于 Steam 卖单墙和价格断层计算基础上架价。",
        "params_schema": {
            "sell_price_wall_volume": {"type": "integer", "min": 1, "default": 20, "label": "价格墙数量阈值"},
            "sell_price_max_ignore_volume": {"type": "integer", "min": 0, "default": 4, "label": "断层跳跃容忍量"},
        },
    },
    "pricing.price_offset": {
        "id": "pricing.price_offset",
        "name": "上架价补偿",
        "category": "pricing",
        "strategy_types": ["sell"],
        "description": "在智能定价结果上追加固定补偿，可用于略高或略低挂牌。",
        "params_schema": {
            "sell_price_offset": {"type": "number", "default": 0, "label": "价格补偿"},
        },
    },
    "guard.rising_trend_wait": {
        "id": "guard.rising_trend_wait",
        "name": "上涨趋势等待",
        "category": "guard",
        "strategy_types": ["sell"],
        "description": "近 N 天价格上涨时暂缓出售。",
        "params_schema": {"sell_trend_days": {"type": "integer", "min": 1, "default": 7, "label": "趋势判断天数"}},
    },
    "guard.profit_ratio": {
        "id": "guard.profit_ratio",
        "name": "利润比例保护",
        "category": "guard",
        "strategy_types": ["sell"],
        "description": "当前买入/挂刀价比例不能高于购入时比例的指定倍数。",
        "params_schema": {"profit_ratio_multiplier": {"type": "number", "min": 1, "default": 1.05, "label": "利润比例倍数"}},
    },
    "action.pause_auto_sell": {
        "id": "action.pause_auto_sell",
        "name": "暂停自动出售",
        "category": "action",
        "strategy_types": ["sell"],
        "description": "出售阶段直接跳过，保留手动出售。",
        "params_schema": {},
    },
    "action.steam_list": {
        "id": "action.steam_list",
        "name": "Steam 上架",
        "category": "action",
        "strategy_types": ["sell"],
        "description": "按策略计算价格后提交 Steam 上架。",
        "params_schema": {},
    },
}


SYSTEM_STRATEGIES: List[Dict[str, Any]] = [
    {
        "id": "system.buy.default",
        "name": "系统默认购入策略",
        "strategy_type": "buy",
        "origin": "system",
        "readonly": True,
        "description": "复刻当前自动购入流程：候选过滤、实时价格、折扣、卖压、稳定性和安全采购保护。",
        "steps": [
            {"module_id": "buy.steamdt_top_n", "enabled": True, "params": {}},
            {"module_id": "buy.exclude_keywords", "enabled": True, "params": {}},
            {"module_id": "buy.basic_candidate_filter", "enabled": True, "params": {}},
            {"module_id": "buy.buff_realtime_price", "enabled": True, "params": {}},
            {"module_id": "buy.steam_sell_depth", "enabled": True, "params": {}},
            {"module_id": "guard.sell_pressure", "enabled": True, "params": {}},
            {"module_id": "guard.max_discount", "enabled": True, "params": {}},
            {"module_id": "guard.history_data_window", "enabled": True, "params": {}},
            {"module_id": "guard.volatility_cv", "enabled": True, "params": {}},
            {"module_id": "guard.trend_quality", "enabled": True, "params": {}},
            {"module_id": "guard.price_position", "enabled": True, "params": {}},
            {"module_id": "guard.purchase_hard_cap", "enabled": True, "params": {}},
            {"module_id": "guard.purchase_liquidity_cap", "enabled": True, "params": {}},
            {"module_id": "guard.low_price_purchase_guard", "enabled": True, "params": {}},
            {"module_id": "guard.held_same_item_guard", "enabled": True, "params": {}},
            {"module_id": "guard.target_balance", "enabled": True, "params": {}},
            {"module_id": "action.buff_lock_pay", "enabled": True, "params": {}},
        ],
    },
    {
        "id": "system.sell.immediate",
        "name": "系统出售策略1：立即上架",
        "strategy_type": "sell",
        "origin": "system",
        "readonly": True,
        "description": "可出售后按 Steam 卖单墙+断层智能定价并上架。",
        "steps": [
            {"module_id": "sell.sellable_inventory_filter", "enabled": True, "params": {}},
            {"module_id": "guard.max_listings_per_item", "enabled": True, "params": {}},
            {"module_id": "pricing.steam_wall_price", "enabled": True, "params": {}},
            {"module_id": "pricing.price_offset", "enabled": True, "params": {}},
            {"module_id": "action.steam_list", "enabled": True, "params": {}},
        ],
    },
    {
        "id": "system.sell.trend",
        "name": "系统出售策略2：趋势等待",
        "strategy_type": "sell",
        "origin": "system",
        "readonly": True,
        "description": "策略1基础上，近 N 天上涨时等待。",
        "steps": [
            {"module_id": "sell.sellable_inventory_filter", "enabled": True, "params": {}},
            {"module_id": "guard.max_listings_per_item", "enabled": True, "params": {}},
            {"module_id": "pricing.steam_wall_price", "enabled": True, "params": {}},
            {"module_id": "pricing.price_offset", "enabled": True, "params": {}},
            {"module_id": "guard.rising_trend_wait", "enabled": True, "params": {}},
            {"module_id": "action.steam_list", "enabled": True, "params": {}},
        ],
    },
    {
        "id": "system.sell.profit_guard",
        "name": "系统出售策略3：利润保护",
        "strategy_type": "sell",
        "origin": "system",
        "readonly": True,
        "description": "策略2基础上，避免以明显更差的价格低价售出。",
        "steps": [
            {"module_id": "sell.sellable_inventory_filter", "enabled": True, "params": {}},
            {"module_id": "guard.max_listings_per_item", "enabled": True, "params": {}},
            {"module_id": "pricing.steam_wall_price", "enabled": True, "params": {}},
            {"module_id": "pricing.price_offset", "enabled": True, "params": {}},
            {"module_id": "guard.rising_trend_wait", "enabled": True, "params": {}},
            {"module_id": "guard.profit_ratio", "enabled": True, "params": {}},
            {"module_id": "action.steam_list", "enabled": True, "params": {}},
        ],
    },
    {
        "id": "system.sell.pause",
        "name": "系统出售策略4：暂停自动出售",
        "strategy_type": "sell",
        "origin": "system",
        "readonly": True,
        "description": "自动出售阶段跳过，保留手动出售。",
        "steps": [
            {"module_id": "action.pause_auto_sell", "enabled": True, "params": {}},
        ],
    },
]

MODULE_CONSTRAINTS: Dict[str, Dict[str, Any]] = {
    "guard.max_discount": {"requires": ["buy.steam_sell_depth"]},
    "guard.sell_pressure": {"requires": ["buy.steam_sell_depth"]},
    "guard.history_stability": {
        "conflicts": [
            "guard.history_data_window",
            "guard.volatility_cv",
            "guard.trend_quality",
            "guard.price_position",
        ],
    },
    "guard.history_data_window": {"conflicts": ["guard.history_stability"]},
    "guard.volatility_cv": {"conflicts": ["guard.history_stability"]},
    "guard.trend_quality": {"conflicts": ["guard.history_stability"]},
    "guard.price_position": {"conflicts": ["guard.history_stability"]},
    "guard.safe_purchase_limit": {
        "conflicts": [
            "guard.purchase_hard_cap",
            "guard.purchase_liquidity_cap",
            "guard.low_price_purchase_guard",
            "guard.held_same_item_guard",
        ],
    },
    "guard.purchase_hard_cap": {"conflicts": ["guard.safe_purchase_limit"]},
    "guard.purchase_liquidity_cap": {"conflicts": ["guard.safe_purchase_limit"]},
    "guard.low_price_purchase_guard": {"conflicts": ["guard.safe_purchase_limit"]},
    "guard.held_same_item_guard": {"conflicts": ["guard.safe_purchase_limit"]},
    "pricing.steam_wall_gap": {
        "conflicts": ["pricing.steam_wall_price", "pricing.price_offset"],
    },
    "pricing.steam_wall_price": {"conflicts": ["pricing.steam_wall_gap"]},
    "pricing.price_offset": {
        "conflicts": ["pricing.steam_wall_gap"],
        "requires_any": [["pricing.steam_wall_price", "pricing.steam_wall_gap"]],
    },
    "action.pause_auto_sell": {"conflicts": ["action.steam_list"]},
    "action.steam_list": {
        "conflicts": ["action.pause_auto_sell"],
        "requires": ["sell.sellable_inventory_filter", "guard.max_listings_per_item"],
        "requires_any": [["pricing.steam_wall_price", "pricing.steam_wall_gap"]],
    },
}

USER_MODULE_CODE_KEYS = {"code", "source", "script", "entrypoint", "package", "command"}
DECLARATIVE_MODULE_KINDS = {"declarative", "rule"}
USER_MODULE_EFFECTS = {"guard", "filter", "info"}
USER_MODULE_FAIL_STATUSES = {"reject", "wait", "pass", "error"}
USER_MODULE_STAGES = {
    "buy": {"buy.candidate_guard"},
    "sell": {"sell.listing_guard"},
}
CONDITION_OPERATORS = {
    "eq", "ne", "gt", "gte", "lt", "lte",
    "contains", "not_contains", "in", "not_in",
    "exists", "missing", "between",
}

MODULE_DATA_OUTPUTS: Dict[str, Dict[str, str]] = {
    "buy.buff_realtime_price": {
        "lowest_price": "Current Buff lowest sell order.",
        "order_count": "Number of visible Buff sell orders.",
        "available": "Whether a usable Buff order exists.",
    },
    "buy.steam_sell_depth": {
        "smart_price": "Steam smart reference price in CNY.",
        "sell_orders_count": "Number of Steam sell orders.",
        "sell_pressure": "Top order volume divided by daily volume.",
        "reference_price": "Reference price after daily-high adjustment.",
        "estimated_ratio": "(Buff price / Steam reference price) * fee factor.",
    },
    "guard.history_data_window": {
        "status": "History analyzer status.",
        "avg": "Average history price.",
        "cv": "Coefficient of variation.",
        "r_squared": "Trend fit quality.",
        "slope": "Trend slope.",
        "price_percentile": "Current price percentile in history window.",
        "ma7": "Short moving average.",
        "ma30": "Long moving average.",
        "is_stable": "Whether the history analyzer accepted the item.",
    },
    "guard.max_discount": {
        "estimated_ratio": "Computed discount ratio.",
        "limit": "Configured max discount threshold.",
    },
    "guard.sell_pressure": {
        "sell_pressure": "Computed sell pressure.",
        "limit": "Configured sell pressure threshold.",
    },
    "guard.max_listings_per_item": {
        "steam_same_name": "Already listed same-name count on Steam.",
        "round_same_name": "Same-name count planned in this run.",
        "max_per_item": "Configured same-name listing cap.",
    },
    "pricing.steam_wall_price": {
        "list_price": "Computed CNY listing price.",
        "reason": "Pricing explanation from Steam order depth.",
        "order_count": "Number of Steam sell orders used.",
    },
    "pricing.steam_wall_gap": {
        "list_price": "Computed CNY listing price.",
        "reason": "Pricing explanation from Steam order depth.",
        "order_count": "Number of Steam sell orders used.",
    },
    "pricing.price_offset": {
        "sell_price_offset": "Fixed listing price offset.",
    },
    "guard.rising_trend_wait": {
        "trend": "Recent trend value.",
        "trend_days": "Configured trend lookback days.",
    },
    "guard.profit_ratio": {
        "current_ratio": "Current buy/listing ratio.",
        "original_ratio": "Buy-time ratio.",
        "ratio_limit": "Configured allowed ratio limit.",
    },
}

MODULE_SAMPLE_OUTPUTS: Dict[str, Dict[str, Any]] = {
    "buy.buff_realtime_price": {"lowest_price": 9.8, "order_count": 6, "available": True},
    "buy.steam_sell_depth": {
        "smart_price": 13.2,
        "sell_orders_count": 42,
        "sell_pressure": 1.1,
        "reference_price": 13.2,
        "estimated_ratio": 0.85,
    },
    "guard.history_data_window": {
        "status": "STABLE",
        "avg": 12.4,
        "cv": 0.032,
        "r_squared": 0.72,
        "slope": 0.001,
        "price_percentile": 0.48,
        "ma7": 12.6,
        "ma30": 12.2,
        "is_stable": True,
    },
    "guard.volatility_cv": {"cv": 0.032, "limit": 0.05},
    "guard.trend_quality": {"r_squared": 0.72, "slope": 0.001, "status": "STABLE"},
    "guard.price_position": {"price_percentile": 0.48, "ma7": 12.6, "ma30": 12.2},
    "guard.max_discount": {"estimated_ratio": 0.85, "limit": 0.9},
    "guard.sell_pressure": {"sell_pressure": 1.1, "limit": 2.0},
    "guard.purchase_hard_cap": {"hard_cap": 50},
    "guard.purchase_liquidity_cap": {"liquidity_cap": 5},
    "guard.low_price_purchase_guard": {"low_price_adjustment": 1.0},
    "guard.held_same_item_guard": {"held_same_name": 0},
    "guard.target_balance": {"target_balance": 100, "projected_spend": 9.8},
    "sell.sellable_inventory_filter": {"sellable": True},
    "guard.max_listings_per_item": {"steam_same_name": 1, "round_same_name": 0, "max_per_item": 5},
    "pricing.steam_wall_price": {"list_price": 12.8, "reason": "sample wall price", "order_count": 30},
    "pricing.steam_wall_gap": {"list_price": 12.8, "reason": "sample wall-gap price", "order_count": 30},
    "pricing.price_offset": {"sell_price_offset": 0},
    "guard.rising_trend_wait": {"trend": -0.01, "trend_days": 7},
    "guard.profit_ratio": {"current_ratio": 0.72, "original_ratio": 0.75, "ratio_limit": 0.7875},
}

STRATEGY_CONTEXT_FIELDS: Dict[str, Dict[str, str]] = {
    "buy": {
        "item.name": "Candidate item name.",
        "item.goods_id": "Buff goods id.",
        "item.min_price": "Candidate Buff price.",
        "item.daily_volume": "SteamDT daily volume.",
        "item.ratio": "Candidate ratio from source data.",
        "item.steam_market_name": "Steam market hash name.",
        "outputs.buy.steam_sell_depth.smart_price": "Steam smart reference price.",
        "outputs.buy.steam_sell_depth.estimated_ratio": "Estimated buy discount ratio.",
        "outputs.guard.history_data_window.cv": "History CV.",
        "outputs.guard.history_data_window.price_percentile": "Current price percentile.",
    },
    "sell": {
        "item.name": "Inventory item name.",
        "item.assetid": "Steam asset id.",
        "item.market_hash_name": "Steam market hash name.",
        "buy_record.price": "Recorded buy price.",
        "buy_record.market_price": "Buy-time market price.",
        "listing.list_price": "Computed CNY listing price.",
        "outputs.guard.max_listings_per_item.steam_same_name": "Already listed same-name count.",
        "outputs.pricing.steam_wall_price.list_price": "Computed CNY listing price.",
        "outputs.pricing.steam_wall_gap.list_price": "Computed CNY listing price from the one-piece pricing module.",
        "outputs.guard.rising_trend_wait.trend": "Recent Steam price trend.",
        "outputs.guard.profit_ratio.current_ratio": "Current buy/listing ratio.",
    },
}


class StrategyError(ValueError):
    pass


def _read_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return copy.deepcopy(fallback)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else copy.deepcopy(fallback)
    except Exception:
        return copy.deepcopy(fallback)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _strategy_store() -> dict:
    data = _read_json(STRATEGY_STORE_PATH, {"strategies": []})
    data["strategies"] = [s for s in data.get("strategies", []) if isinstance(s, dict)]
    return data


def _save_strategy_store(data: dict) -> None:
    _write_json(STRATEGY_STORE_PATH, {"strategies": data.get("strategies", [])})


def _module_store() -> dict:
    data = _read_json(USER_MODULE_STORE_PATH, {"modules": []})
    data["modules"] = [m for m in data.get("modules", []) if isinstance(m, dict)]
    return data


def _save_module_store(data: dict) -> None:
    _write_json(USER_MODULE_STORE_PATH, {"modules": data.get("modules", [])})


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        clean = {}
        for k, v in value.items():
            if SENSITIVE_KEY_RE.search(str(k)):
                continue
            clean[k] = _sanitize_value(v)
        return clean
    if isinstance(value, list):
        return [_sanitize_value(v) for v in value]
    return value


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return slug or str(int(time.time()))


def _custom_id(strategy_type: str, name: str) -> str:
    return f"custom.{strategy_type}.{_slugify(name)}.{int(time.time())}"


def _system_by_id() -> Dict[str, Dict[str, Any]]:
    return {s["id"]: copy.deepcopy(s) for s in SYSTEM_STRATEGIES}


def _user_module_by_id() -> Dict[str, Dict[str, Any]]:
    out = {}
    for m in _module_store().get("modules", []):
        mid = str(m.get("id") or "").strip()
        if mid:
            out[mid] = copy.deepcopy(m)
    return out


def _clean_module_id_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _clean_requires_any(value: Any) -> List[List[str]]:
    if not isinstance(value, list):
        return []
    groups: List[List[str]] = []
    for group in value:
        cleaned = _clean_module_id_list(group)
        if cleaned:
            groups.append(cleaned)
    return groups


def _clean_logic(value: Any) -> str:
    logic = str(value or "all").strip().lower()
    return "any" if logic == "any" else "all"


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _clean_stage_list(strategy_types: List[str], stage: Any, stages: Any) -> List[str]:
    raw = []
    if isinstance(stages, list):
        raw.extend(str(v).strip() for v in stages if str(v).strip())
    if str(stage or "").strip():
        raw.append(str(stage).strip())
    if not raw:
        for stype in strategy_types:
            raw.extend(sorted(USER_MODULE_STAGES.get(stype) or []))
    allowed = set()
    for stype in strategy_types:
        allowed.update(USER_MODULE_STAGES.get(stype) or set())
    return sorted({s for s in raw if s in allowed})


def _clean_conditions(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        left = str(item.get("left") or item.get("path") or "").strip()
        op = str(item.get("op") or item.get("operator") or "eq").strip().lower()
        if not left or op not in CONDITION_OPERATORS:
            continue
        cleaned: Dict[str, Any] = {
            "left": left,
            "op": op,
            "label": str(item.get("label") or "").strip(),
        }
        if "right_path" in item:
            cleaned["right_path"] = str(item.get("right_path") or "").strip()
        elif item.get("right_is_path"):
            cleaned["right_path"] = str(item.get("right") or item.get("value") or "").strip()
        elif "value" in item:
            cleaned["value"] = _sanitize_value(item.get("value"))
        elif "right" in item:
            cleaned["value"] = _sanitize_value(item.get("right"))
        out.append(cleaned)
    return out


def _has_user_code_fields(data: dict) -> bool:
    return any(key in data for key in USER_MODULE_CODE_KEYS)


def _is_declarative_user_module(mod: dict) -> bool:
    if (mod or {}).get("origin") != "user":
        return False
    kind = str(mod.get("module_kind") or mod.get("kind") or "").strip().lower()
    if kind not in DECLARATIVE_MODULE_KINDS:
        return False
    return not _has_user_code_fields(mod)


def _normalize_user_module_kind(data: dict) -> str:
    raw = str(data.get("module_kind") or data.get("kind") or "").strip().lower()
    if raw in DECLARATIVE_MODULE_KINDS and not _has_user_code_fields(data):
        return "declarative"
    return "external_code" if _has_user_code_fields(data) else "manifest"


def _normalize_max_instances(value: Any) -> int:
    try:
        return max(1, int(value or DEFAULT_MODULE_MAX_INSTANCES))
    except (TypeError, ValueError):
        return DEFAULT_MODULE_MAX_INSTANCES


def _apply_module_defaults(module_id: str, mod: Dict[str, Any]) -> Dict[str, Any]:
    constraints = MODULE_CONSTRAINTS.get(module_id) or {}
    for key, value in constraints.items():
        mod[key] = copy.deepcopy(value)
    mod.setdefault("data_outputs", copy.deepcopy(MODULE_DATA_OUTPUTS.get(module_id) or {}))
    mod.setdefault("sample_output", copy.deepcopy(MODULE_SAMPLE_OUTPUTS.get(module_id) or {}))
    mod["max_instances"] = _normalize_max_instances(mod.get("max_instances"))
    mod["conflicts"] = _clean_module_id_list(mod.get("conflicts"))
    uses_modules = _clean_module_id_list(mod.get("uses_modules"))
    mod["uses_modules"] = uses_modules
    mod["requires"] = sorted(set(_clean_module_id_list(mod.get("requires")) + uses_modules))
    mod["requires_any"] = _clean_requires_any(mod.get("requires_any"))
    if mod.get("origin") == "user":
        strategy_types = [t for t in (mod.get("strategy_types") or []) if t in STRATEGY_TYPES]
        mod["stages"] = _clean_stage_list(strategy_types, mod.get("stage"), mod.get("stages"))
        mod["effect"] = str(mod.get("effect") or "guard").strip().lower()
        if mod["effect"] not in USER_MODULE_EFFECTS:
            mod["effect"] = "guard"
        mod["logic"] = _clean_logic(mod.get("logic"))
        mod["conditions"] = _clean_conditions(mod.get("conditions"))
        fail_status = str(mod.get("fail_status") or "reject").strip().lower()
        mod["fail_status"] = fail_status if fail_status in USER_MODULE_FAIL_STATUSES else "reject"
    return mod


def _module_by_id() -> Dict[str, Dict[str, Any]]:
    out = {}
    for mid, mod in BUILTIN_MODULES.items():
        item = copy.deepcopy(mod)
        item.setdefault("origin", "builtin")
        item.setdefault("builtin", True)
        item.setdefault("enabled", True)
        out[mid] = _apply_module_defaults(mid, item)
    for mid, mod in _user_module_by_id().items():
        mod.setdefault("origin", "user")
        mod.setdefault("enabled", False)
        mod.setdefault("executable", False)
        mod.setdefault("builtin", False)
        out[mid] = _apply_module_defaults(mid, mod)
    return out


def list_custom_strategies() -> List[Dict[str, Any]]:
    return [copy.deepcopy(s) for s in _strategy_store().get("strategies", [])]


def list_strategies() -> List[Dict[str, Any]]:
    return [copy.deepcopy(s) for s in SYSTEM_STRATEGIES] + list_custom_strategies()


def get_strategy(strategy_id: str) -> Optional[Dict[str, Any]]:
    for s in list_strategies():
        if s.get("id") == strategy_id:
            return s
    return None


def _legacy_sell_id(config: dict) -> str:
    try:
        n = int((config.get("pipeline") or {}).get("sell_strategy", 1))
    except Exception:
        n = 1
    return SELL_LEGACY_TO_SYSTEM.get(n, "system.sell.pause")


def get_active_strategy_ids(config: Optional[dict] = None) -> Dict[str, str]:
    cfg = config or load_app_config_validated()
    scfg = cfg.get("strategies") or {}
    buy_id = scfg.get("active_buy_strategy_id") or "system.buy.default"
    sell_id = scfg.get("active_sell_strategy_id") or _legacy_sell_id(cfg)
    if not get_strategy(buy_id) or get_strategy(buy_id).get("strategy_type") != "buy":
        buy_id = "system.buy.default"
    if not get_strategy(sell_id) or get_strategy(sell_id).get("strategy_type") != "sell":
        sell_id = _legacy_sell_id(cfg)
    return {"buy": buy_id, "sell": sell_id}


def _normalize_step(raw: dict) -> dict:
    module_id = str(raw.get("module_id") or raw.get("module") or "").strip()
    return {
        "module_id": module_id,
        "enabled": _coerce_bool(raw.get("enabled"), True),
        "params": _sanitize_value(raw.get("params") if isinstance(raw.get("params"), dict) else {}),
    }


def normalize_strategy(raw: dict, *, imported: bool = False) -> Dict[str, Any]:
    data = _sanitize_value(raw or {})
    strategy_type = str(data.get("strategy_type") or data.get("type") or "").strip()
    if strategy_type not in STRATEGY_TYPES:
        raise StrategyError("strategy_type 必须是 buy 或 sell")
    name = str(data.get("name") or "未命名策略").strip() or "未命名策略"
    sid = str(data.get("id") or "").strip()
    if not sid or sid.startswith("system."):
        sid = _custom_id(strategy_type, name)
    if not sid.startswith("custom."):
        sid = f"custom.{strategy_type}.{_slugify(sid)}"
    steps = [_normalize_step(s) for s in data.get("steps", []) if isinstance(s, dict)]
    if not steps:
        raise StrategyError("策略至少需要一个模块")
    return {
        "id": sid,
        "name": name,
        "strategy_type": strategy_type,
        "origin": "custom",
        "readonly": False,
        "description": str(data.get("description") or "").strip(),
        "steps": steps,
        "created_at": data.get("created_at") or time.time(),
        "updated_at": time.time(),
        "imported": bool(imported or data.get("imported")),
    }


def _validate_step_params(module_id: str, mod: dict, params: dict) -> List[str]:
    errors: List[str] = []
    schema = mod.get("params_schema") if isinstance(mod.get("params_schema"), dict) else {}
    if not isinstance(params, dict):
        return [f"Module {module_id} params must be an object"]
    for key, meta in schema.items():
        if not isinstance(meta, dict) or key not in params:
            continue
        value = params.get(key)
        label = meta.get("label") or key
        expected = str(meta.get("type") or "string").lower()
        if value is None or value == "":
            continue
        try:
            if expected == "integer":
                if isinstance(value, bool):
                    raise ValueError
                numeric = int(value)
                if float(value) != numeric:
                    raise ValueError
                if "min" in meta and numeric < int(meta["min"]):
                    errors.append(f"{label} must be >= {meta['min']}")
                if "max" in meta and numeric > int(meta["max"]):
                    errors.append(f"{label} must be <= {meta['max']}")
            elif expected == "number":
                if isinstance(value, bool):
                    raise ValueError
                numeric = float(value)
                if "min" in meta and numeric < float(meta["min"]):
                    errors.append(f"{label} must be >= {meta['min']}")
                if "max" in meta and numeric > float(meta["max"]):
                    errors.append(f"{label} must be <= {meta['max']}")
            elif expected == "boolean":
                if not isinstance(value, bool):
                    errors.append(f"{label} must be boolean")
            elif expected == "array":
                if not isinstance(value, list):
                    errors.append(f"{label} must be an array")
            elif expected == "select":
                options = meta.get("options") if isinstance(meta.get("options"), list) else []
                allowed = [o.get("value") if isinstance(o, dict) else o for o in options]
                if allowed and value not in allowed:
                    errors.append(f"{label} must be one of: {', '.join(map(str, allowed))}")
        except (TypeError, ValueError):
            errors.append(f"{label} has invalid value")
    return errors


def _validate_user_module_contract(module_id: str, mod: dict, *, for_activation: bool) -> List[str]:
    errors: List[str] = []
    if (mod or {}).get("origin") != "user":
        return errors
    if not for_activation:
        return errors
    if not _is_declarative_user_module(mod):
        errors.append(f"User module {module_id} is registered but cannot be activated because it is not a safe declarative module")
        return errors
    if mod.get("enabled") is False:
        errors.append(f"User module {module_id} is disabled")
    if not (mod.get("stages") or []):
        errors.append(f"User module {module_id} has no supported runtime stage")
    if mod.get("effect") not in USER_MODULE_EFFECTS:
        errors.append(f"User module {module_id} has unsupported effect")
    if mod.get("effect") in {"guard", "filter"} and not (mod.get("conditions") or []):
        errors.append(f"User module {module_id} needs at least one condition")
    return errors


def validate_strategy(strategy: dict, *, for_activation: bool = False) -> List[str]:
    errors: List[str] = []
    stype = strategy.get("strategy_type")
    if stype not in STRATEGY_TYPES:
        errors.append("strategy_type 必须是 buy 或 sell")
        return errors
    modules = _module_by_id()
    steps = strategy.get("steps") or []
    step_limit = STRATEGY_STEP_LIMITS.get(stype, 16)
    if len(steps) > step_limit:
        errors.append(f"{'购入' if stype == 'buy' else '出售'}策略最多添加 {step_limit} 个模块")
    enabled_ids = set()
    enabled_indices: Dict[str, int] = {}
    all_counts: Dict[str, int] = {}
    for idx, step in enumerate(steps):
        module_id = step.get("module_id")
        if not module_id:
            errors.append(f"第 {idx + 1} 步缺少 module_id")
            continue
        all_counts[module_id] = all_counts.get(module_id, 0) + 1
        mod = modules.get(module_id)
        if not mod:
            errors.append(f"未知模块: {module_id}")
            continue
        if stype not in (mod.get("strategy_types") or []):
            errors.append(f"模块 {module_id} 不能用于 {stype} 策略")
        errors.extend(_validate_step_params(module_id, mod, step.get("params") if isinstance(step.get("params"), dict) else {}))
        if step.get("enabled") is not False:
            enabled_ids.add(module_id)
            enabled_indices.setdefault(module_id, idx)
            if for_activation and (mod.get("origin") == "user" or mod.get("builtin") is False):
                errors.append(f"用户模块 {module_id} 首版只允许登记，不能启用")
            if for_activation and mod.get("enabled") is False and mod.get("origin") == "user":
                errors.append(f"用户模块 {module_id} 当前未启用")
    if for_activation:
        declarative_user_ids = set()
        for module_id in sorted(enabled_ids):
            mod = modules.get(module_id) or {}
            if mod.get("origin") == "user":
                errors.extend(_validate_user_module_contract(module_id, mod, for_activation=True))
                if _is_declarative_user_module(mod):
                    declarative_user_ids.add(module_id)
        if declarative_user_ids:
            errors = [
                e for e in errors
                if not any(module_id in e and not e.startswith("User module ") for module_id in declarative_user_ids)
            ]
    for module_id, count in sorted(all_counts.items()):
        mod = modules.get(module_id)
        if not mod:
            continue
        max_instances = _normalize_max_instances(mod.get("max_instances"))
        if count > max_instances:
            label = mod.get("name") or module_id
            errors.append(f"模块 {label} 最多只能添加 {max_instances} 次")
    conflict_pairs = set()
    for module_id in sorted(enabled_ids):
        mod = modules.get(module_id) or {}
        label = mod.get("name") or module_id
        for other_id in mod.get("conflicts") or []:
            if other_id in enabled_ids:
                pair = tuple(sorted((module_id, other_id)))
                if pair in conflict_pairs:
                    continue
                conflict_pairs.add(pair)
                other_label = (modules.get(other_id) or {}).get("name") or other_id
                errors.append(f"模块 {label} 与 {other_label} 互斥，不能同时启用")
        for req_id in mod.get("requires") or []:
            if req_id not in enabled_ids:
                req_label = (modules.get(req_id) or {}).get("name") or req_id
                errors.append(f"模块 {label} 需要同时启用 {req_label}")
        for req_id in mod.get("requires") or []:
            if req_id in enabled_ids and enabled_indices.get(req_id, 0) > enabled_indices.get(module_id, 0):
                req_label = (modules.get(req_id) or {}).get("name") or req_id
                errors.append(f"Module {label} must run after {req_label}")
        for group in mod.get("requires_any") or []:
            if group and not any(req_id in enabled_ids for req_id in group):
                if module_id == "action.steam_list" and set(group) == set(SELL_PRICING_CORE_MODULES):
                    continue
                req_labels = [
                    (modules.get(req_id) or {}).get("name") or req_id
                    for req_id in group
                ]
                errors.append(f"模块 {label} 需要至少启用一个依赖模块：{' / '.join(req_labels)}")
        for group in mod.get("requires_any") or []:
            if group and any(req_id in enabled_ids for req_id in group) and not any(
                req_id in enabled_ids and enabled_indices.get(req_id, 0) < enabled_indices.get(module_id, 0)
                for req_id in group
            ):
                req_labels = [
                    (modules.get(req_id) or {}).get("name") or req_id
                    for req_id in group
                ]
                errors.append(f"Module {label} must run after one of: {' / '.join(req_labels)}")
    if stype == "buy":
        for module_id in BUY_REQUIRED_MODULES:
            if module_id not in enabled_ids:
                label = (modules.get(module_id) or {}).get("name") or module_id
                errors.append(f"购入策略必须包含并启用 {label}")
    if stype == "sell":
        has_pause = "action.pause_auto_sell" in enabled_ids
        has_list = "action.steam_list" in enabled_ids
        if not has_pause and not has_list:
            errors.append("出售策略必须包含 Steam 上架或暂停自动出售动作")
        if has_list:
            for module_id in SELL_LIST_REQUIRED_MODULES:
                if module_id not in enabled_ids:
                    label = (modules.get(module_id) or {}).get("name") or module_id
                    errors.append(f"出售上架策略必须包含并启用 {label}")
            if not any(module_id in enabled_ids for module_id in SELL_PRICING_CORE_MODULES):
                labels = [
                    (modules.get(module_id) or {}).get("name") or module_id
                    for module_id in SELL_PRICING_CORE_MODULES
                ]
                errors.append(f"出售上架策略必须包含并启用一个定价模块：{' / '.join(labels)}")
    return errors


def _system_strategy_with_submitted_params(raw_strategy: dict) -> Dict[str, Any]:
    sid = str((raw_strategy or {}).get("id") or "").strip()
    base = get_strategy(sid)
    if not base or base.get("origin") != "system":
        raise StrategyError("系统策略不存在")
    incoming_steps = raw_strategy.get("steps") if isinstance(raw_strategy, dict) else None
    if not isinstance(incoming_steps, list):
        raise StrategyError("系统策略参数缺少 steps")
    base_steps = base.get("steps") or []
    if len(incoming_steps) != len(base_steps):
        raise StrategyError("系统策略只允许修改参数，不能改变模块结构")
    out = copy.deepcopy(base)
    for idx, base_step in enumerate(base_steps):
        incoming = incoming_steps[idx] if isinstance(incoming_steps[idx], dict) else {}
        if incoming.get("module_id") != base_step.get("module_id"):
            raise StrategyError("系统策略只允许修改参数，不能改变模块顺序")
        if bool(incoming.get("enabled", True)) != bool(base_step.get("enabled", True)):
            raise StrategyError("系统策略只允许修改参数，不能改变模块启用状态")
        params = incoming.get("params") if isinstance(incoming.get("params"), dict) else {}
        out["steps"][idx]["params"] = _sanitize_value(params)
    return out


def _save_system_strategy_params(raw_strategy: dict) -> Dict[str, Any]:
    strategy = _system_strategy_with_submitted_params(raw_strategy)
    errors = validate_strategy(strategy, for_activation=False)
    if errors:
        raise StrategyError("；".join(errors))
    cfg = load_app_config_validated()
    old_strategies = copy.deepcopy(cfg.get("strategies") or {})
    old_sell_strategy = (cfg.get("pipeline") or {}).get("sell_strategy")
    updated = apply_strategy_to_config(cfg, strategy["strategy_type"], strategy_override=strategy)
    updated.pop("_strategy_runtime", None)
    updated["strategies"] = old_strategies
    if strategy["strategy_type"] == "sell":
        if old_sell_strategy is None:
            updated.setdefault("pipeline", {}).pop("sell_strategy", None)
        else:
            updated.setdefault("pipeline", {})["sell_strategy"] = old_sell_strategy
    save_app_config_validated(updated)
    fresh_cfg = load_app_config_validated()
    return _hydrate_strategy_params(get_strategy(strategy["id"]) or strategy, fresh_cfg)


def save_strategy(raw_strategy: dict) -> Dict[str, Any]:
    if str((raw_strategy or {}).get("id") or "").startswith("system."):
        return _save_system_strategy_params(raw_strategy)
    strategy = normalize_strategy(raw_strategy)
    errors = validate_strategy(strategy, for_activation=False)
    if errors:
        raise StrategyError("；".join(errors))
    store = _strategy_store()
    strategies = [s for s in store.get("strategies", []) if s.get("id") != strategy["id"]]
    strategies.append(strategy)
    store["strategies"] = strategies
    _save_strategy_store(store)
    return copy.deepcopy(strategy)


def delete_strategy(strategy_id: str) -> None:
    if strategy_id.startswith("system."):
        raise StrategyError("系统策略不可删除")
    store = _strategy_store()
    before = len(store.get("strategies", []))
    store["strategies"] = [s for s in store.get("strategies", []) if s.get("id") != strategy_id]
    if len(store["strategies"]) == before:
        raise StrategyError("策略不存在")
    cfg = load_app_config_validated()
    active = get_active_strategy_ids(cfg)
    scfg = dict(cfg.get("strategies") or {})
    changed = False
    if active.get("buy") == strategy_id:
        scfg["active_buy_strategy_id"] = "system.buy.default"
        changed = True
    if active.get("sell") == strategy_id:
        scfg["active_sell_strategy_id"] = _legacy_sell_id(cfg)
        changed = True
    _save_strategy_store(store)
    if changed:
        cfg["strategies"] = scfg
        save_app_config_validated(cfg)


def activate_strategy(strategy_id: str, *, risk_confirmed: bool) -> Dict[str, Any]:
    if not risk_confirmed:
        raise StrategyError("启用策略前必须确认风险")
    strategy = get_strategy(strategy_id)
    if not strategy:
        raise StrategyError("策略不存在")
    errors = validate_strategy(strategy, for_activation=True)
    if errors:
        raise StrategyError("；".join(errors))
    cfg = load_app_config_validated()
    scfg = dict(cfg.get("strategies") or {})
    if strategy["strategy_type"] == "buy":
        scfg["active_buy_strategy_id"] = strategy_id
    else:
        scfg["active_sell_strategy_id"] = strategy_id
        legacy = SELL_SYSTEM_TO_LEGACY.get(strategy_id)
        if legacy is not None:
            cfg.setdefault("pipeline", {})["sell_strategy"] = legacy
    cfg["strategies"] = scfg
    save_app_config_validated(cfg)
    return {"ok": True, "active": get_active_strategy_ids(cfg)}


def import_strategy(raw_strategy: dict) -> Dict[str, Any]:
    strategy = normalize_strategy(raw_strategy, imported=True)
    errors = validate_strategy(strategy, for_activation=False)
    if errors:
        raise StrategyError("；".join(errors))
    return save_strategy(strategy)


def export_strategy(strategy_id: str) -> Dict[str, Any]:
    strategy = get_strategy(strategy_id)
    if not strategy:
        raise StrategyError("策略不存在")
    out = copy.deepcopy(strategy)
    out.pop("created_at", None)
    out.pop("updated_at", None)
    return _sanitize_value(out)




def import_user_module(raw_manifest: dict) -> Dict[str, Any]:
    data = _sanitize_value(raw_manifest or {})
    mid = str(data.get("id") or "").strip()
    if not mid:
        raise StrategyError("Module manifest is missing id")
    if mid in BUILTIN_MODULES:
        raise StrategyError("Cannot override builtin modules")
    strategy_types = data.get("strategy_types")
    if not isinstance(strategy_types, list):
        mtype = data.get("strategy_type") or data.get("type")
        strategy_types = [mtype] if mtype else []
    strategy_types = [t for t in strategy_types if t in STRATEGY_TYPES]
    if not strategy_types:
        raise StrategyError("Module must declare strategy_types: buy or sell")

    module_kind = _normalize_user_module_kind(data)
    effect = str(data.get("effect") or "guard").strip().lower()
    if effect not in USER_MODULE_EFFECTS:
        effect = "guard"
    fail_status = str(data.get("fail_status") or "reject").strip().lower()
    if fail_status not in USER_MODULE_FAIL_STATUSES:
        fail_status = "reject"
    uses_modules = _clean_module_id_list(data.get("uses_modules"))
    requires = sorted(set(_clean_module_id_list(data.get("requires")) + uses_modules))
    stages = _clean_stage_list(strategy_types, data.get("stage"), data.get("stages"))
    conditions = _clean_conditions(data.get("conditions"))
    if module_kind == "declarative":
        if effect in {"guard", "filter"} and not conditions:
            raise StrategyError("Declarative guard/filter modules need at least one condition")
        if not stages:
            raise StrategyError("Declarative module stage is not supported for its strategy type")

    manifest = {
        "id": mid,
        "name": str(data.get("name") or mid),
        "category": str(data.get("category") or "custom"),
        "strategy_types": strategy_types,
        "description": str(data.get("description") or ""),
        "params_schema": data.get("params_schema") if isinstance(data.get("params_schema"), dict) else {},
        "max_instances": _normalize_max_instances(data.get("max_instances")),
        "conflicts": _clean_module_id_list(data.get("conflicts")),
        "requires": requires,
        "requires_any": _clean_requires_any(data.get("requires_any")),
        "uses_modules": uses_modules,
        "module_kind": module_kind,
        "effect": effect,
        "logic": _clean_logic(data.get("logic")),
        "conditions": conditions,
        "fail_status": fail_status,
        "message": str(data.get("message") or ""),
        "stages": stages,
        "capabilities": ["read_context", "read_module_outputs"] if module_kind == "declarative" else [],
        "origin": "user",
        "enabled": module_kind == "declarative",
        "executable": False,
        "imported_at": time.time(),
    }
    store = _module_store()
    store["modules"] = [m for m in store.get("modules", []) if m.get("id") != mid]
    store["modules"].append(manifest)
    _save_module_store(store)
    return copy.deepcopy(manifest)


def _enabled_steps(strategy: dict) -> List[dict]:
    return [s for s in strategy.get("steps") or [] if s.get("enabled") is not False]


def _current_param_values(config: dict) -> Dict[str, Dict[str, Any]]:
    pipe = config.get("pipeline") or {}
    stability = config.get("stability") or {}
    return {
        "buy.steamdt_top_n": {"iflow_top_n": pipe.get("iflow_top_n")},
        "buy.exclude_keywords": {"exclude_keywords": pipe.get("exclude_keywords")},
        "guard.max_discount": {"max_discount": pipe.get("max_discount")},
        "guard.sell_pressure": {
            "sell_pressure_orders_n": pipe.get("sell_pressure_orders_n"),
            "sell_pressure_threshold": pipe.get("sell_pressure_threshold"),
        },
        "guard.history_stability": {
            "days": stability.get("days"),
            "cv_threshold": stability.get("cv_threshold"),
            "r2_threshold": stability.get("r2_threshold"),
            "min_daily_trades": stability.get("min_daily_trades"),
            "price_percentile_ceil": stability.get("price_percentile_ceil"),
            "r2_rising_threshold": stability.get("r2_rising_threshold"),
            "slope_pct_ceil": stability.get("slope_pct_ceil"),
            "ma_deviation_ceil": stability.get("ma_deviation_ceil"),
            "last_price_ma30_ceil": stability.get("last_price_ma30_ceil"),
            "slope_stable_floor": stability.get("slope_stable_floor"),
            "price_percentile_ceil_rising": stability.get("price_percentile_ceil_rising"),
            "use_vwap": stability.get("use_vwap"),
        },
        "guard.history_data_window": {
            "days": stability.get("days"),
            "min_daily_trades": stability.get("min_daily_trades"),
            "use_vwap": stability.get("use_vwap"),
        },
        "guard.volatility_cv": {"cv_threshold": stability.get("cv_threshold")},
        "guard.trend_quality": {
            "r2_threshold": stability.get("r2_threshold"),
            "r2_rising_threshold": stability.get("r2_rising_threshold"),
            "slope_pct_ceil": stability.get("slope_pct_ceil"),
            "slope_stable_floor": stability.get("slope_stable_floor"),
        },
        "guard.price_position": {
            "price_percentile_ceil": stability.get("price_percentile_ceil"),
            "price_percentile_ceil_rising": stability.get("price_percentile_ceil_rising"),
            "ma_deviation_ceil": stability.get("ma_deviation_ceil"),
            "last_price_ma30_ceil": stability.get("last_price_ma30_ceil"),
        },
        "guard.safe_purchase_limit": {
            "safe_purchase_hard_qty_cap": pipe.get("safe_purchase_hard_qty_cap"),
            "safe_purchase_liquidity_ratio": pipe.get("safe_purchase_liquidity_ratio"),
            "safe_purchase_low_price_threshold": pipe.get("safe_purchase_low_price_threshold"),
            "safe_purchase_low_price_penalty": pipe.get("safe_purchase_low_price_penalty"),
            "safe_purchase_low_price_hard_cap": pipe.get("safe_purchase_low_price_hard_cap"),
        },
        "guard.purchase_hard_cap": {
            "safe_purchase_hard_qty_cap": pipe.get("safe_purchase_hard_qty_cap"),
        },
        "guard.purchase_liquidity_cap": {
            "safe_purchase_liquidity_ratio": pipe.get("safe_purchase_liquidity_ratio"),
        },
        "guard.low_price_purchase_guard": {
            "safe_purchase_low_price_threshold": pipe.get("safe_purchase_low_price_threshold"),
            "safe_purchase_low_price_penalty": pipe.get("safe_purchase_low_price_penalty"),
            "safe_purchase_low_price_hard_cap": pipe.get("safe_purchase_low_price_hard_cap"),
        },
        "guard.held_same_item_guard": {},
        "guard.target_balance": {"target_balance": pipe.get("target_balance")},
        "guard.max_listings_per_item": {"max_listings_per_item": pipe.get("max_listings_per_item")},
        "pricing.steam_wall_gap": {
            "sell_price_wall_volume": pipe.get("sell_price_wall_volume"),
            "sell_price_max_ignore_volume": pipe.get("sell_price_max_ignore_volume"),
            "sell_price_offset": pipe.get("sell_price_offset"),
        },
        "pricing.steam_wall_price": {
            "sell_price_wall_volume": pipe.get("sell_price_wall_volume"),
            "sell_price_max_ignore_volume": pipe.get("sell_price_max_ignore_volume"),
        },
        "pricing.price_offset": {"sell_price_offset": pipe.get("sell_price_offset")},
        "guard.rising_trend_wait": {"sell_trend_days": pipe.get("sell_trend_days")},
        "guard.profit_ratio": {"profit_ratio_multiplier": pipe.get("profit_ratio_multiplier", 1.05)},
    }


def _hydrate_strategy_params(strategy: dict, config: dict) -> dict:
    out = copy.deepcopy(strategy)
    current = _current_param_values(config)
    modules = _module_by_id()
    for step in out.get("steps") or []:
        module_id = step.get("module_id")
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        hydrated = {}
        schema = (modules.get(module_id) or {}).get("params_schema") or {}
        for key, meta in schema.items():
            val = params.get(key)
            if val is None:
                val = (current.get(module_id) or {}).get(key)
            if val is None:
                val = meta.get("default") if isinstance(meta, dict) else None
            if val is not None:
                hydrated[key] = val
        hydrated.update({k: v for k, v in params.items() if v is not None})
        step["params"] = hydrated
    return out


def _step_params(strategy: dict, module_id: str) -> dict:
    for s in strategy.get("steps") or []:
        if s.get("module_id") == module_id and s.get("enabled") is not False:
            return s.get("params") if isinstance(s.get("params"), dict) else {}
    return {}


def _enabled_module_ids(strategy: dict) -> set:
    return {s.get("module_id") for s in _enabled_steps(strategy)}


def _derive_sell_strategy_number(strategy: dict) -> int:
    enabled = _enabled_module_ids(strategy)
    if "action.pause_auto_sell" in enabled or "action.steam_list" not in enabled:
        return 4
    if "guard.profit_ratio" in enabled:
        return 3
    if "guard.rising_trend_wait" in enabled:
        return 2
    return 1


def apply_strategy_to_config(config: dict, strategy_type: str, strategy_override: Optional[dict] = None) -> dict:
    cfg = copy.deepcopy(config)
    if strategy_override is not None:
        strategy = copy.deepcopy(strategy_override)
        active_id = strategy.get("id") or get_active_strategy_ids(cfg).get(strategy_type)
    else:
        active_id = get_active_strategy_ids(cfg).get(strategy_type)
        strategy = get_strategy(active_id)
    if not strategy or strategy.get("strategy_type") != strategy_type:
        return cfg
    strategy = _hydrate_strategy_params(strategy, cfg)
    enabled_ids = _enabled_module_ids(strategy)
    params_by_module = {
        s.get("module_id"): (s.get("params") if isinstance(s.get("params"), dict) else {})
        for s in strategy.get("steps") or []
    }
    modules = _module_by_id()
    enabled_steps = copy.deepcopy(_enabled_steps(strategy))
    runtime_modules = {
        module_id: copy.deepcopy(modules.get(module_id) or {})
        for module_id in enabled_ids
        if modules.get(module_id)
    }
    runtime = cfg.setdefault("_strategy_runtime", {})
    runtime[strategy_type] = {
        "active_strategy_id": active_id,
        "enabled_modules": sorted(enabled_ids),
        "params": copy.deepcopy(params_by_module),
        "steps": enabled_steps,
        "modules": runtime_modules,
    }
    pipe = cfg.setdefault("pipeline", {})
    stability = cfg.setdefault("stability", {})
    if strategy_type == "buy":
        if "buy.steamdt_top_n" in enabled_ids:
            params = _step_params(strategy, "buy.steamdt_top_n")
            if "iflow_top_n" in params:
                pipe["iflow_top_n"] = int(params.get("iflow_top_n") or 0)
        else:
            pipe["iflow_top_n"] = 0
        if "buy.exclude_keywords" in enabled_ids:
            params = _step_params(strategy, "buy.exclude_keywords")
            if isinstance(params.get("exclude_keywords"), list):
                pipe["exclude_keywords"] = [str(x) for x in params.get("exclude_keywords") if str(x).strip()]
        else:
            pipe["exclude_keywords"] = []
        if "guard.max_discount" in enabled_ids:
            params = _step_params(strategy, "guard.max_discount")
            if "max_discount" in params:
                pipe["max_discount"] = float(params.get("max_discount"))
        else:
            pipe["max_discount"] = None
        if "guard.sell_pressure" in enabled_ids:
            params = _step_params(strategy, "guard.sell_pressure")
            if "sell_pressure_orders_n" in params:
                pipe["sell_pressure_orders_n"] = int(params.get("sell_pressure_orders_n") or 5)
            if "sell_pressure_threshold" in params:
                pipe["sell_pressure_threshold"] = float(params.get("sell_pressure_threshold") or 0)
        else:
            pipe["sell_pressure_threshold"] = None
        if "guard.history_stability" in enabled_ids:
            for key, val in _step_params(strategy, "guard.history_stability").items():
                if key in stability:
                    stability[key] = val
        for module_id in (
            "guard.history_data_window",
            "guard.volatility_cv",
            "guard.trend_quality",
            "guard.price_position",
        ):
            if module_id in enabled_ids:
                for key, val in _step_params(strategy, module_id).items():
                    if key in stability:
                        stability[key] = val
        for module_id in (
            "guard.safe_purchase_limit",
            "guard.purchase_hard_cap",
            "guard.purchase_liquidity_cap",
            "guard.low_price_purchase_guard",
        ):
            if module_id in enabled_ids:
                for key, val in _step_params(strategy, module_id).items():
                    pipe[key] = val
        if "guard.target_balance" in enabled_ids:
            params = _step_params(strategy, "guard.target_balance")
            if "target_balance" in params:
                pipe["target_balance"] = float(params.get("target_balance") or pipe.get("target_balance", 100))
    else:
        pipe["sell_strategy"] = _derive_sell_strategy_number(strategy)
        if "guard.max_listings_per_item" in enabled_ids:
            params = _step_params(strategy, "guard.max_listings_per_item")
            if "max_listings_per_item" in params:
                pipe["max_listings_per_item"] = int(params.get("max_listings_per_item") or 5)
        if "pricing.steam_wall_gap" in enabled_ids:
            for key, val in _step_params(strategy, "pricing.steam_wall_gap").items():
                pipe[key] = val
        if "pricing.steam_wall_price" in enabled_ids:
            for key, val in _step_params(strategy, "pricing.steam_wall_price").items():
                pipe[key] = val
            if "pricing.price_offset" not in enabled_ids and "pricing.steam_wall_gap" not in enabled_ids:
                pipe["sell_price_offset"] = 0
        if "pricing.price_offset" in enabled_ids:
            params = _step_params(strategy, "pricing.price_offset")
            if "sell_price_offset" in params:
                pipe["sell_price_offset"] = float(params.get("sell_price_offset") or 0)
        if "guard.rising_trend_wait" in enabled_ids:
            params = _step_params(strategy, "guard.rising_trend_wait")
            if "sell_trend_days" in params:
                pipe["sell_trend_days"] = int(params.get("sell_trend_days") or 7)
        if "guard.profit_ratio" in enabled_ids:
            params = _step_params(strategy, "guard.profit_ratio")
            if "profit_ratio_multiplier" in params:
                pipe["profit_ratio_multiplier"] = float(params.get("profit_ratio_multiplier") or 1.05)
    return cfg


def is_strategy_module_enabled(config: dict, strategy_type: str, module_id: str, default: bool = True) -> bool:
    runtime = ((config or {}).get("_strategy_runtime") or {}).get(strategy_type)
    if not runtime:
        return default
    return module_id in set(runtime.get("enabled_modules") or [])


def get_strategy_module_params(config: dict, strategy_type: str, module_id: str) -> dict:
    runtime = ((config or {}).get("_strategy_runtime") or {}).get(strategy_type) or {}
    params = (runtime.get("params") or {}).get(module_id)
    return params if isinstance(params, dict) else {}


_MISSING = object()


def _available_data_catalog() -> Dict[str, Any]:
    return {
        "context_fields": copy.deepcopy(STRATEGY_CONTEXT_FIELDS),
        "module_outputs": copy.deepcopy(MODULE_DATA_OUTPUTS),
        "operators": sorted(CONDITION_OPERATORS),
        "stages": {k: sorted(v) for k, v in USER_MODULE_STAGES.items()},
    }


def _get_nested(value: Any, parts: List[str]) -> Any:
    current = value
    for part in parts:
        if current is _MISSING:
            return _MISSING
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (TypeError, ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return current


def _resolve_output_path(outputs: dict, path: str) -> Any:
    remainder = path[len("outputs."):]
    matches = [
        module_id for module_id in (outputs or {}).keys()
        if remainder == module_id or remainder.startswith(f"{module_id}.")
    ]
    if not matches:
        return _MISSING
    module_id = max(matches, key=len)
    rest = remainder[len(module_id):].lstrip(".")
    return _get_nested(outputs.get(module_id), rest.split(".") if rest else [])


def _resolve_path(path: Any, data: dict) -> Any:
    if not isinstance(path, str):
        return _MISSING
    path = path.strip()
    if not path:
        return _MISSING
    if path.startswith("outputs."):
        return _resolve_output_path(data.get("outputs") or {}, path)
    parts = path.split(".")
    root = parts[0]
    if root not in data:
        return _MISSING
    return _get_nested(data.get(root), parts[1:])


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is _MISSING or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compare_values(left: Any, op: str, right: Any = _MISSING) -> bool:
    if op == "exists":
        return left is not _MISSING and left is not None
    if op == "missing":
        return left is _MISSING or left is None
    if left is _MISSING:
        return False
    if op in {"gt", "gte", "lt", "lte", "between"}:
        left_num = _as_number(left)
        if left_num is None:
            return False
        if op == "between":
            if not isinstance(right, list) or len(right) != 2:
                return False
            low = _as_number(right[0])
            high = _as_number(right[1])
            return low is not None and high is not None and low <= left_num <= high
        right_num = _as_number(right)
        if right_num is None:
            return False
        if op == "gt":
            return left_num > right_num
        if op == "gte":
            return left_num >= right_num
        if op == "lt":
            return left_num < right_num
        return left_num <= right_num
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "contains":
        return str(right) in str(left) if not isinstance(left, (list, tuple, set)) else right in left
    if op == "not_contains":
        return not _compare_values(left, "contains", right)
    if op == "in":
        return isinstance(right, (list, tuple, set)) and left in right
    if op == "not_in":
        return not _compare_values(left, "in", right)
    return False


def _evaluate_condition(condition: dict, data: dict) -> Dict[str, Any]:
    left_path = condition.get("left")
    op = str(condition.get("op") or "eq").lower()
    left_value = _resolve_path(left_path, data)
    if "right_path" in condition:
        right_value = _resolve_path(condition.get("right_path"), data)
        right_repr = condition.get("right_path")
    else:
        right_value = condition.get("value", _MISSING)
        right_repr = right_value
    ok = _compare_values(left_value, op, right_value)
    return {
        "left": left_path,
        "op": op,
        "right": None if right_value is _MISSING else right_repr,
        "left_value": None if left_value is _MISSING else left_value,
        "passed": bool(ok),
        "label": condition.get("label") or "",
    }


def _evaluate_declarative_module(mod: dict, params: dict, context: Optional[dict] = None, outputs: Optional[dict] = None) -> Dict[str, Any]:
    data = copy.deepcopy(context or {})
    data.setdefault("params", params or {})
    data.setdefault("outputs", outputs or {})
    conditions = mod.get("conditions") or []
    checks = [_evaluate_condition(cond, data) for cond in conditions]
    logic = _clean_logic(mod.get("logic"))
    passed = any(c["passed"] for c in checks) if logic == "any" else all(c["passed"] for c in checks)
    if not checks:
        passed = True
    status = "pass" if passed else str(mod.get("fail_status") or "reject").lower()
    if status not in USER_MODULE_FAIL_STATUSES:
        status = "reject"
    message = str(mod.get("message") or "").strip()
    if not message:
        message = "Declarative conditions passed" if passed else "Declarative conditions did not pass"
    return {
        "status": status,
        "reason": message,
        "output": {
            "logic": logic,
            "passed": passed,
            "passed_conditions": sum(1 for c in checks if c["passed"]),
            "total_conditions": len(checks),
            "conditions": checks,
        },
    }


def _module_stage_matches(mod: dict, strategy_type: str, stage: str) -> bool:
    stages = mod.get("stages")
    if not stages:
        stages = sorted(USER_MODULE_STAGES.get(strategy_type) or [])
    return "*" in stages or stage in stages


def evaluate_strategy_runtime_modules(
    config: dict,
    strategy_type: str,
    stage: str,
    *,
    context: Optional[dict] = None,
    outputs: Optional[dict] = None,
) -> tuple:
    runtime = ((config or {}).get("_strategy_runtime") or {}).get(strategy_type) or {}
    steps = runtime.get("steps") or []
    modules = runtime.get("modules") or _module_by_id()
    results = []
    for idx, step in enumerate(steps, start=1):
        if step.get("enabled") is False:
            continue
        module_id = step.get("module_id")
        mod = modules.get(module_id) or {}
        if not _is_declarative_user_module(mod):
            continue
        if not _module_stage_matches(mod, strategy_type, stage):
            continue
        result = _evaluate_declarative_module(
            mod,
            step.get("params") if isinstance(step.get("params"), dict) else {},
            context=context,
            outputs=outputs,
        )
        result.update({
            "index": idx,
            "module_id": module_id,
            "module_name": mod.get("name") or module_id,
            "stage": stage,
        })
        results.append(result)
        if result.get("status") in {"reject", "wait", "error"}:
            return results, result
    return results, None


def _sample_strategy_context(strategy_type: str) -> Dict[str, Any]:
    if strategy_type == "sell":
        return {
            "item": {"name": "Sample Item", "assetid": "123", "market_hash_name": "Sample Item"},
            "buy_record": {"price": 8.0, "market_price": 12.0},
            "listing": {"list_price": 12.8, "display_price": 12.8},
            "config": {"pipeline": {}, "stability": {}},
        }
    return {
        "item": {
            "name": "Sample Item",
            "goods_id": 1001,
            "min_price": 9.8,
            "daily_volume": 80,
            "ratio": 0.82,
            "steam_market_name": "Sample Item",
        },
        "config": {"pipeline": {}, "stability": {}},
    }


def _simulate_builtin_step(module_id: str, step: dict, outputs: dict) -> Dict[str, Any]:
    if module_id == "action.pause_auto_sell":
        status = "wait"
        reason = "Simulation: auto sell would pause here"
    elif str(module_id).startswith("action."):
        status = "action"
        reason = "模拟跳过真实交易 / Simulation skipped the real trading action"
    else:
        status = "pass"
        reason = "Simulation passed with sample market data"
    output = copy.deepcopy(MODULE_SAMPLE_OUTPUTS.get(module_id) or {})
    if output:
        outputs[module_id] = output
    return {"status": status, "reason": reason, "output": output}


def get_strategy_payload() -> Dict[str, Any]:
    cfg = load_app_config_validated()
    active = get_active_strategy_ids(cfg)
    modules = []
    for mid, mod in _module_by_id().items():
        item = copy.deepcopy(mod)
        item.setdefault("builtin", mid in BUILTIN_MODULES)
        item.setdefault("origin", "builtin" if mid in BUILTIN_MODULES else "user")
        item.setdefault("enabled", mid in BUILTIN_MODULES)
        modules.append(item)
    return {
        "strategies": [_hydrate_strategy_params(s, cfg) for s in list_strategies()],
        "modules": sorted(modules, key=lambda m: (m.get("origin") != "builtin", m.get("category", ""), m.get("id", ""))),
        "active": active,
        "limits": copy.deepcopy(STRATEGY_STEP_LIMITS),
        "legacy_sell_strategy": int((cfg.get("pipeline") or {}).get("sell_strategy", 4) or 4),
        "available_data": _available_data_catalog(),
    }




def simulate_strategy(raw: dict) -> Dict[str, Any]:
    strategy = get_strategy(raw.get("strategy_id") or "") if raw.get("strategy_id") else None
    if strategy is None:
        strategy = normalize_strategy(raw.get("strategy") or raw)
    errors = validate_strategy(strategy, for_activation=False)
    if errors:
        return {"ok": False, "errors": errors, "results": []}
    modules = _module_by_id()
    context = _sample_strategy_context(strategy.get("strategy_type"))
    outputs: Dict[str, Any] = {}
    results = []
    for idx, step in enumerate(strategy.get("steps") or [], start=1):
        module_id = step.get("module_id")
        mod = modules.get(module_id)
        output: Dict[str, Any] = {}
        if step.get("enabled") is False:
            status = "skip"
            reason = "Module is disabled"
        elif not mod:
            status = "error"
            reason = "Unknown module"
        elif mod.get("origin") == "user":
            if _is_declarative_user_module(mod):
                evaluated = _evaluate_declarative_module(
                    mod,
                    step.get("params") if isinstance(step.get("params"), dict) else {},
                    context=context,
                    outputs=outputs,
                )
                status = evaluated["status"]
                reason = evaluated["reason"]
                output = evaluated.get("output") or {}
                outputs[module_id] = copy.deepcopy(output)
            else:
                status = "error"
                reason = "User code modules are registered only and are not executed"
        else:
            evaluated = _simulate_builtin_step(module_id, step, outputs)
            status = evaluated["status"]
            reason = evaluated["reason"]
            output = evaluated.get("output") or {}
        results.append({
            "index": idx,
            "module_id": module_id,
            "module_name": (mod or {}).get("name", module_id),
            "status": status,
            "reason": reason,
            "output_keys": sorted(output.keys()) if isinstance(output, dict) else [],
            "output": output,
        })
    return {"ok": True, "strategy": strategy, "results": results, "available_data": _available_data_catalog()}
