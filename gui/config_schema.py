"""Steamauto GUI 的 config.json5 结构化字段元数据（schema）。

来源：utils/static.py 中 DEFAULT_CONFIG_JSON 的字段与中文注释，逐条整理。
每个字段含：group(分组) / label(名称) / key(点号路径) / type(类型) /
default(默认值) / options(可填值说明) / help(作用说明)。

表格编辑器据此渲染、读取与保存 config.json5。
"""
import json5


def _a(x):
    """array 默认值标记（默认值里的列表需要避免被后续修改污染）。"""
    return list(x)


CONFIG_SCHEMA = [
    # ============ Steam 基础 ============
    {"group": "Steam 基础", "label": "关闭 SSL 验证", "key": "steam_login_ignore_ssl_error", "type": "bool",
     "default": False, "options": "true / false", "help": "登录 Steam 时是否关闭 SSL 证书验证。正常情况下不建议关闭。"},
    {"group": "Steam 基础", "label": "本地加速", "key": "steam_local_accelerate", "type": "bool",
     "default": False, "options": "true / false", "help": "是否开启本地加速（非 100% 可用）。开启此功能必须同时关闭 SSL 验证。"},
    {"group": "Steam 基础", "label": "手动指定代理", "key": "use_proxies", "type": "bool",
     "default": False, "options": "true / false", "help": "是否手动指定 Steam 代理（该功能只代理 Steam，需配合下方 proxies）。"},
    {"group": "Steam 基础", "label": "HTTP 代理地址", "key": "proxies.http", "type": "string",
     "default": "http://127.0.0.1:7890", "options": "URL 字符串", "help": "HTTP 代理地址，仅当 use_proxies 为 true 时生效。"},
    {"group": "Steam 基础", "label": "HTTPS 代理地址", "key": "proxies.https", "type": "string",
     "default": "http://127.0.0.1:7890", "options": "URL 字符串", "help": "HTTPS 代理地址，一般与 HTTP 相同。"},

    # ============ 通知服务 ============
    {"group": "通知服务", "label": "通知器列表", "key": "notify_service.notifiers", "type": "array",
     "default": [], "options": "Apprise 格式地址数组", "help": "通知服务器列表（Apprise 格式），支持 Telegram/钉钉/飞书/WxPusher/Server酱等；为空不发送。"},
    {"group": "通知服务", "label": "自定义标题", "key": "notify_service.custom_title", "type": "string",
     "default": "", "options": "字符串，留空用默认", "help": "自定义通知标题，为空则使用默认标题。"},
    {"group": "通知服务", "label": "包含 Steam 账号信息", "key": "notify_service.include_steam_info", "type": "bool",
     "default": True, "options": "true / false", "help": "通知中是否包含 Steam 账号信息。"},
    {"group": "通知服务", "label": "屏蔽词", "key": "notify_service.blacklist_words", "type": "array",
     "default": [], "options": "字符串数组", "help": "通知内容包含这些词时不会发送。"},

    # ============ BUFF 自动发货 ============
    {"group": "BUFF 自动发货", "label": "启用", "key": "buff_auto_accept_offer.enable", "type": "bool",
     "default": True, "options": "true / false", "help": "是否启用 BUFF 自动发货报价功能。"},
    {"group": "BUFF 自动发货", "label": "轮询间隔", "key": "buff_auto_accept_offer.interval", "type": "int",
     "default": 300, "options": "整数，单位：秒", "help": "每次检查是否有新报价的间隔（秒）。"},
    {"group": "BUFF 自动发货", "label": "dota2 支持", "key": "buff_auto_accept_offer.dota2_support", "type": "bool",
     "default": False, "options": "true / false", "help": "是否开启 dota2 支持。"},
    {"group": "BUFF 自动发货", "label": "使用代理", "key": "buff_auto_accept_offer.use_proxies", "type": "bool",
     "default": False, "options": "true / false", "help": "是否使用全局代理设置中的代理连接 BUFF。"},

    # ============ 悠悠有品 自动发货 ============
    {"group": "悠悠有品 · 自动发货", "label": "启用", "key": "uu_auto_accept_offer.enable", "type": "bool",
     "default": False, "options": "true / false", "help": "悠悠有品自动发货功能是否启用。"},
    {"group": "悠悠有品 · 自动发货", "label": "轮询间隔", "key": "uu_auto_accept_offer.interval", "type": "int",
     "default": 300, "options": "整数，单位：秒", "help": "每次检查新报价的间隔（秒）。"},
    {"group": "悠悠有品 · 自动发货", "label": "使用代理", "key": "uu_auto_accept_offer.use_proxies", "type": "bool",
     "default": False, "options": "true / false", "help": "是否使用全局代理连接悠悠有品。"},

    # ============ 悠悠有品 租赁自动上架 ============
    {"group": "悠悠有品 · 租赁上架", "label": "启用", "key": "uu_auto_lease_item.enable", "type": "bool",
     "default": False, "options": "true / false", "help": "悠悠有品租赁自动上架功能是否启用。"},
    {"group": "悠悠有品 · 租赁上架", "label": "最长租赁天数", "key": "uu_auto_lease_item.lease_max_days", "type": "int",
     "default": 60, "options": "整数，单位：天", "help": "最长租赁时间（天）。"},
    {"group": "悠悠有品 · 租赁上架", "label": "最低上架价格", "key": "uu_auto_lease_item.filter_price", "type": "int",
     "default": 100, "options": "整数，单位：元", "help": "价格低于此值的物品不会上架。"},
    {"group": "悠悠有品 · 租赁上架", "label": "定时运行时间", "key": "uu_auto_lease_item.run_time", "type": "string",
     "default": "17:30", "options": "HH:MM 格式", "help": "自动上架租赁每天定时运行时间。"},
    {"group": "悠悠有品 · 租赁上架", "label": "改价间隔", "key": "uu_auto_lease_item.interval", "type": "int",
     "default": 31, "options": "整数，单位：分钟", "help": "已上架租赁物品定时改价的轮询间隔（分钟）。"},
    {"group": "悠悠有品 · 租赁上架", "label": "不出租物品", "key": "uu_auto_lease_item.filter_name", "type": "array",
     "default": [], "options": "字符串数组", "help": "不出租的物品名字列表（名字可不写全，但要写对）。"},
    {"group": "悠悠有品 · 租赁上架", "label": "固定比例定价", "key": "uu_auto_lease_item.enable_fix_lease_ratio", "type": "bool",
     "default": False, "options": "true / false", "help": "是否按现价固定比例设置出租价格。"},
    {"group": "悠悠有品 · 租赁上架", "label": "出租价格比例", "key": "uu_auto_lease_item.fix_lease_ratio", "type": "float",
     "default": 0.001, "options": "小数，如 0.001", "help": "出租价格比例。如现价 1000 元、比例 0.001 → 1 元。"},
    {"group": "悠悠有品 · 租赁上架", "label": "赔付方式", "key": "uu_auto_lease_item.compensation_type", "type": "int",
     "default": 7, "options": "0(非会员) / 7(v1)", "help": "赔付方式：0 表示非会员，7 表示 v1。"},

    # ============ 悠悠有品 出售自动上架 ============
    {"group": "悠悠有品 · 出售上架", "label": "启用", "key": "uu_auto_sell_item.enable", "type": "bool",
     "default": False, "options": "true / false", "help": "悠悠有品出售自动上架功能是否启用。"},
    {"group": "悠悠有品 · 出售上架", "label": "止盈定价", "key": "uu_auto_sell_item.take_profile", "type": "bool",
     "default": False, "options": "true / false", "help": "是否按照止盈率设置定价。"},
    {"group": "悠悠有品 · 出售上架", "label": "止盈率", "key": "uu_auto_sell_item.take_profile_ratio", "type": "float",
     "default": 0.1, "options": "小数，如 0.1", "help": "止盈率（配合止盈定价使用）。"},
    {"group": "悠悠有品 · 出售上架", "label": "定时运行时间", "key": "uu_auto_sell_item.run_time", "type": "string",
     "default": "15:30", "options": "HH:MM 格式", "help": "自动上架每天定时运行时间。"},
    {"group": "悠悠有品 · 出售上架", "label": "改价请求间隔", "key": "uu_auto_sell_item.sell_interval", "type": "int",
     "default": 20, "options": "整数，单位：分钟", "help": "每隔多久重新请求市场并改价一次（分钟）。"},
    {"group": "悠悠有品 · 出售上架", "label": "最高上架价格", "key": "uu_auto_sell_item.max_on_sale_price", "type": "int",
     "default": 1000, "options": "整数，0 不限制", "help": "价格高于此值的物品不会上架，设为 0 则不限制。"},
    {"group": "悠悠有品 · 出售上架", "label": "改价轮询间隔", "key": "uu_auto_sell_item.interval", "type": "int",
     "default": 51, "options": "整数，单位：分钟", "help": "已上架物品定时改价的轮询间隔（分钟）。"},
    {"group": "悠悠有品 · 出售上架", "label": "出售物品", "key": "uu_auto_sell_item.name", "type": "array",
     "default": [], "options": "字符串数组", "help": "出售的物品名字列表（名字可不写全，但要写对）。"},
    {"group": "悠悠有品 · 出售上架", "label": "排除物品", "key": "uu_auto_sell_item.blacklist_words", "type": "array",
     "default": [], "options": "字符串数组", "help": "不出售也不参与改价的物品名字列表（优先级高于出售列表）。"},
    {"group": "悠悠有品 · 出售上架", "label": "自动压价", "key": "uu_auto_sell_item.use_price_adjustment", "type": "bool",
     "default": True, "options": "true / false", "help": "是否开启自动压价（-0.01）功能。"},
    {"group": "悠悠有品 · 出售上架", "label": "压价阈值", "key": "uu_auto_sell_item.price_adjustment_threshold", "type": "float",
     "default": 1.0, "options": "小数，单位：元", "help": "价格高于此值才会自动压价。"},

    # ============ Steam 自动收礼 ============
    {"group": "Steam 自动收礼", "label": "启用", "key": "steam_auto_accept_offer.enable", "type": "bool",
     "default": False, "options": "true / false", "help": "是否自动接受 Steam 礼物报价（无需支出库存物品的报价）。"},
    {"group": "Steam 自动收礼", "label": "轮询间隔", "key": "steam_auto_accept_offer.interval", "type": "int",
     "default": 300, "options": "整数，单位：秒", "help": "每次检查报价列表的间隔（秒）。"},

    # ============ ECOSteam ============
    {"group": "ECOSteam", "label": "启用", "key": "ecosteam.enable", "type": "bool",
     "default": False, "options": "true / false", "help": "ECOSteam 插件是否启用。私钥放在 config/rsakey.txt。"},
    {"group": "ECOSteam", "label": "partnerId", "key": "ecosteam.partnerId", "type": "string",
     "default": "", "options": "字符串，必填", "help": "ECO 开放平台身份 ID，用于登录 ECOSteam 平台。"},
    {"group": "ECOSteam", "label": "自动发货间隔", "key": "ecosteam.auto_accept_offer.interval", "type": "int",
     "default": 30, "options": "整数，单位：秒", "help": "ECO 自动发货检查间隔（秒）。"},
    {"group": "ECOSteam", "label": "同步出售启用", "key": "ecosteam.auto_sync_sell_shelf.enable", "type": "bool",
     "default": False, "options": "true / false", "help": "是否自动同步各平台的上架商品（与主平台一致）。"},
    {"group": "ECOSteam", "label": "同步出售主平台", "key": "ecosteam.auto_sync_sell_shelf.main_platform", "type": "string",
     "default": "eco", "options": "buff / uu / eco", "help": "主平台（不会被改动），按价格比例同步到其他平台。"},
    {"group": "ECOSteam", "label": "同步出售启用平台", "key": "ecosteam.auto_sync_sell_shelf.enabled_platforms", "type": "array",
     "default": ["uu"], "options": "数组，可选 buff/uu", "help": "可填入多个平台，如 [\"buff\",\"uu\"]。ECO 已强制开启。"},
    {"group": "ECOSteam", "label": "同步出售比例 eco", "key": "ecosteam.auto_sync_sell_shelf.ratio.eco", "type": "float",
     "default": 1, "options": "小数", "help": "ECO 平台上架价格比例。"},
    {"group": "ECOSteam", "label": "同步出售比例 uu", "key": "ecosteam.auto_sync_sell_shelf.ratio.uu", "type": "float",
     "default": 1, "options": "小数", "help": "悠悠有品平台上架价格比例。"},
    {"group": "ECOSteam", "label": "同步出售比例 buff", "key": "ecosteam.auto_sync_sell_shelf.ratio.buff", "type": "float",
     "default": 1, "options": "小数", "help": "BUFF 平台上架价格比例。"},
    {"group": "ECOSteam", "label": "同步租赁启用", "key": "ecosteam.auto_sync_lease_shelf.enable", "type": "bool",
     "default": False, "options": "true / false", "help": "是否与悠悠有品平台同步租赁商品。"},
    {"group": "ECOSteam", "label": "同步租赁主平台", "key": "ecosteam.auto_sync_lease_shelf.main_platform", "type": "string",
     "default": "eco", "options": "uu / eco", "help": "租赁同步主平台（不会被改动）。"},
    {"group": "ECOSteam", "label": "同步租赁比例 eco", "key": "ecosteam.auto_sync_lease_shelf.ratio.eco", "type": "float",
     "default": 1, "options": "小数", "help": "ECO 平台租赁价格比例。"},
    {"group": "ECOSteam", "label": "同步租赁比例 uu", "key": "ecosteam.auto_sync_lease_shelf.ratio.uu", "type": "float",
     "default": 1, "options": "小数", "help": "悠悠有品平台租赁价格比例。"},
    {"group": "ECOSteam", "label": "同步间隔", "key": "ecosteam.sync_interval", "type": "int",
     "default": 60, "options": "整数，单位：秒", "help": "同步间隔（秒）。不建议太长，否则可能封禁。"},
    {"group": "ECOSteam", "label": "每秒最大请求数", "key": "ecosteam.qps", "type": "int",
     "default": 10, "options": "整数；白名单大会员可设 30", "help": "每秒最大请求数。不确定请保持默认 10。"},

    # ============ C5 自动发货 ============
    {"group": "C5 自动发货", "label": "启用", "key": "c5_auto_accept_offer.enable", "type": "bool",
     "default": False, "options": "true / false", "help": "C5 自动发货功能是否启用。"},
    {"group": "C5 自动发货", "label": "轮询间隔", "key": "c5_auto_accept_offer.interval", "type": "int",
     "default": 30, "options": "整数，单位：秒", "help": "每次检查新报价的间隔（秒）。"},
    {"group": "C5 自动发货", "label": "AppKey", "key": "c5_auto_accept_offer.app_key", "type": "string",
     "default": "", "options": "字符串", "help": "C5Game 的 AppKey，在 c5game.com 开放平台申请。"},

    # ============ 日志与通用 ============
    {"group": "日志与通用", "label": "日志等级", "key": "log_level", "type": "string",
     "default": "debug", "options": "debug / info / warning / error", "help": "写入硬盘的日志等级。"},
    {"group": "日志与通用", "label": "日志保留天数", "key": "log_retention_days", "type": "int",
     "default": 7, "options": "整数，单位：天", "help": "本地日志保留天数，超过自动删除。"},
    {"group": "日志与通用", "label": "出错即停", "key": "no_pause", "type": "bool",
     "default": False, "options": "true / false", "help": "设为 true 后，程序出现错误直接停止（不再等待按键）。不确定请保持 false。"},
    {"group": "日志与通用", "label": "插件白名单", "key": "plugin_whitelist", "type": "array",
     "default": [], "options": "字符串数组", "help": "白名单内的本地插件与程序自带版本不同时不会被覆盖。"},
    {"group": "日志与通用", "label": "源码自动更新", "key": "source_code_auto_update", "type": "bool",
     "default": False, "options": "true / false", "help": "源码运行时是否自动更新程序。"},
]


def _path_exists(config, key):
    parts = key.split(".")
    cur = config
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return False
        cur = cur[p]
    return True


def get_value(config, key):
    parts = key.split(".")
    cur = config
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def set_value(config, key, value):
    parts = key.split(".")
    cur = config
    for p in parts[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def convert_value(field_type, raw):
    """把前端提交的字符串值转换为 schema 类型，失败抛 ValueError。"""
    if field_type == "bool":
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
        raise ValueError("布尔值应为 true 或 false")
    if field_type == "int":
        return int(str(raw).strip())
    if field_type == "float":
        return float(str(raw).strip())
    if field_type == "string":
        return str(raw)
    if field_type == "array":
        if isinstance(raw, list):
            return list(raw)
        parsed = json5.loads(str(raw))
        if not isinstance(parsed, list):
            raise ValueError("应为 JSON 数组")
        return parsed
    return raw


def _deep_merge_defaults(config):
    """以当前 config 为基础，补齐 schema 内缺失字段的默认值（保留 schema 外字段）。"""
    result = config
    for field in CONFIG_SCHEMA:
        if not _path_exists(result, field["key"]):
            set_value(result, field["key"], field["default"])
    return result


def get_table_data(config):
    """生成表格数据：按 group 分组，每行含字段元数据 + 当前值。"""
    groups = {}
    for field in CONFIG_SCHEMA:
        group = field["group"]
        if group not in groups:
            groups[group] = []
        row = dict(field)
        row["value"] = get_value(config, field["key"]) if _path_exists(config, field["key"]) else field["default"]
        # 默认值用统一格式展示
        groups[group].append(row)
    ordered = []
    for field in CONFIG_SCHEMA:
        g = field["group"]
        if g not in ordered:
            ordered.append(g)
    return [{"group": g, "fields": groups[g]} for g in ordered]


def save_from_table(config, values):
    """把前端提交的 {key: value字符串} 应用到 config，返回 (ok, msg)。

    values 里只含用户改动过的字段；其余字段保持原值/默认值。
    """
    result = _deep_merge_defaults(dict(config) if isinstance(config, dict) else {})
    type_map = {f["key"]: f["type"] for f in CONFIG_SCHEMA}
    for key, raw in values.items():
        if key not in type_map:
            continue  # 忽略 schema 外的未知字段
        try:
            value = convert_value(type_map[key], raw)
        except Exception as e:  # noqa: BLE001
            return False, "字段 %s 转换失败：%s" % (key, str(e))
        set_value(result, key, value)
    return True, result
