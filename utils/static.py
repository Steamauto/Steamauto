import os
import sys

from utils.build_info import info

is_latest_version = False
no_pause = False

CURRENT_VERSION = "5.5.9"

VERSION_FILE = "version.json"
LOGS_FOLDER = "logs"
CONFIG_FOLDER = "config"
PLUGIN_FOLDER = "plugins"
# Keep original JSON config path for potential reference or other parts of system if any
DEFAULT_JSON_CONFIG_PATH_FOR_REFERENCE = os.path.join(CONFIG_FOLDER, "config.json5") 
CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, "config.toml") # New default is TOML

BUFF_COOKIES_FILE_PATH = os.path.join(CONFIG_FOLDER, "buff_cookies.txt")
UU_TOKEN_FILE_PATH = os.path.join(CONFIG_FOLDER, "uu_token.txt")
STEAM_ACCOUNT_INFO_FILE_PATH = os.path.join(CONFIG_FOLDER, "steam_account_info.json5")
STEAM_INVENTORY_FILE_PATH = os.path.join(CONFIG_FOLDER, "steam_inventory.json5")
SESSION_FOLDER = "session"
SUPPORT_GAME_TYPES = [{"game": "csgo", "app_id": 730}, {"game": "dota2", "app_id": 570}]
UU_ARG_FILE_PATH = "uu.txt"
ECOSTEAM_RSAKEY_FILE = os.path.join(CONFIG_FOLDER, "rsakey.txt")
BUILD_INFO = info
if BUILD_INFO == "正在使用源码运行":
    if hasattr(sys, "_MEIPASS"):
        BUILD_INFO = "非官方二进制构建运行"
STEAM_ACCOUNT_NAME = "暂未登录"
STEAM_64_ID = "暂未登录"
INTERNAL_PLUGINS = [
    "buff_auto_accept_offer",
    "buff_auto_comment",
    "buff_profit_report",
    "buff_auto_on_sale",
    "uu_auto_accept_offer",
    "uu_auto_lease_item",
    "uu_auto_sell_item",
    "steam_auto_accept_offer",
    "ecosteam",
    "c5_auto_accept_offer",
]

DEFAULT_STEAM_ACCOUNT_JSON = """
{

  // 新版Steamauto已经无需手动填写API_KEY、steamid、buff_cookies.txt(均可自动获取)，视频教程暂未更新，请悉知！！！
  // 新版Steamauto已经无需手动填写API_KEY、steamid、buff_cookies.txt(均可自动获取)，视频教程暂未更新，请悉知！！！
  // 新版Steamauto已经无需手动填写API_KEY、steamid、buff_cookies.txt(均可自动获取)，视频教程暂未更新，请悉知！！！

  // Steam 令牌参数（用于身份验证）
  "shared_secret": "",

  // Steam 令牌参数（用于身份验证）
  "identity_secret": "",

  // Steam 登录时填写的用户名
  "steam_username": "",

  // Steam 登录时填写的密码
  "steam_password": ""
}
"""

# Original DEFAULT_CONFIG_JSON, kept for reference or potential fallback if DEFAULT_CONFIG_TOML processing fails.
DEFAULT_CONFIG_JSON_REFERENCE = r"""
{
  "steam_login_ignore_ssl_error": false,
  "steam_local_accelerate": false,
  "use_proxies": false,
  "proxies": { "http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890" },
  "notify_service": {
    "notifiers": [], "custom_title": "", "include_steam_info": true,
    "blacklist_words": ["黑名单词语1", "黑名单词语2"]
  },
  "buff_auto_accept_offer": { "enable": true, "interval": 300, "dota2_support": false },
  "buff_auto_comment": { "enable": false },
  "buff_profit_report": { "enable": false, "servers": [], "send_report_time": "20:30" },
  "buff_auto_on_sale": {
    "enable": false, "force_refresh": true, "use_range_price": false,
    "blacklist_time": [], "whitelist_time": [], "random_chance": 100,
    "description": "", "interval": 1800, "sleep_seconds_to_prevent_buff_ban": 10,
    "buy_order": { "enable": true, "only_auto_accept": true, "supported_payment_method": ["支付宝"], "min_price": 5 },
    "on_sale_notification": { "title": "游戏 {game} 成功上架 {sold_count} 件饰品", "body": "上架详情:\n{item_list}" },
    "captcha_notification": { "title": "上架饰品时出现验证码", "body": "使用session={session}并使用浏览器打开以下链接并完成验证:\n{captcha_url}" },
    "servers": []
  },
  "uu_auto_accept_offer": { "enable": false, "interval": 300 },
  "uu_auto_lease_item": {
    "enable": false, "lease_max_days": 60, "filter_price": 100, "run_time": "17:30",
    "interval": 31, "filter_name": ["物品A", "物品B"], "enable_fix_lease_ratio": false,
    "fix_lease_ratio": 0.001, "compensation_type": 7
  },
  "uu_auto_sell_item": {
    "enable": false, "take_profile": false, "take_profile_ratio": 0.1, "run_time": "15:30",
    "sell_interval": 20, "max_on_sale_price": 1000, "interval": 51,
    "name": ["AK", "A1"], "blacklist_words": ["黑名单词语1", "黑名单词语2"],
    "use_price_adjustment": true, "price_adjustment_threshold": 1.0
  },
  "steam_auto_accept_offer": { "enable": false, "interval": 300 },
  "ecosteam": {
    "enable": false, "partnerId": "",
    "auto_accept_offer": { "interval": 30 },
    "auto_sync_sell_shelf": {
      "enable": false, "main_platform": "eco", "enabled_platforms": ["uu"],
      "ratio": { "eco": 1, "uu": 1, "buff": 1 }
    },
    "auto_sync_lease_shelf": {
      "enable": false, "main_platform": "eco",
      "ratio": { "eco": 1, "uu": 1 }
    },
    "sync_interval": 60, "qps": 10
  },
  "c5_auto_accept_offer": { "enable": false, "interval": 30, "app_key": "" },
  "log_level": "debug", "log_retention_days": 7, "no_pause": false,
  "plugin_whitelist": [], "source_code_auto_update": false
}
"""

DEFAULT_CONFIG_TOML = """
# === General Settings ===
# These settings configure the general behavior of Steamauto.

# Login to Steam with SSL verification enabled.
# It is not recommended to disable SSL verification under normal circumstances.
# Default: false (meaning SSL verification IS enabled)
steam_login_ignore_ssl_error = false

# Enable local acceleration for Steam community and API requests.
# This feature is not guaranteed to work in all network environments.
# If Steam connection issues persist, using an overseas server or a dedicated game accelerator is recommended.
# Note: Enabling this feature (`true`) requires `steam_login_ignore_ssl_error` to also be `true`.
# Default: false
steam_local_accelerate = false

# Enable this if you want to manually specify a proxy for Steam connections.
# This proxy will only be used for Steam-related requests made by Steamauto.
# Default: false
use_proxies = false

# Proxy server addresses. These are only used if `use_proxies` is set to `true`.
# Ensure your proxy server is running and accessible.
# Example: "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
[proxies]
http = "http://127.0.0.1:7890"  # HTTP proxy address
https = "http://127.0.0.1:7890" # HTTPS proxy address. Often the same as HTTP.

# --- Notification Service Settings ---
# Configure notifications for various events using Apprise.
# See Apprise documentation for supported services: https://github.com/caronc/apprise/wiki
[notify_service]
# List of Apprise-compatible notification service URLs.
# Examples:
# - Telegram: "tgram://BOT_TOKEN/CHAT_ID"
# - DingTalk: "dingtalk://ACCESS_TOKEN@WEBHOOK_URL_SECRET" (if secret is used) or "dingtalk://ACCESS_TOKEN"
# - ServerChan: "schan://SERVERCHAN_KEY"
# - WxPusher: "wxpusher://APP_TOKEN@UID"
notifiers = [] # e.g., ["tgram://YOUR_BOT_TOKEN/YOUR_CHAT_ID"]

# Custom title for notifications. If empty, a default title will be used.
custom_title = ""

# Include Steam account information (like username) in notifications.
# Default: true
include_steam_info = true

# List of keywords. If a notification message contains any of these words, it will not be sent.
# Useful for filtering out less important notifications. Case-sensitive.
blacklist_words = ["黑名单词语1", "黑名单词语2"]


# === Plugin Configurations ===
# Settings for individual plugins. Each plugin has an 'enable' key.
# Set 'enable = true' to activate a plugin, 'enable = false' to disable it.

# --- BUFF Auto Accept Offer Plugin ---
# Automatically accepts incoming trade offers on Buff that match certain criteria (e.g., receiving items without giving any).
[buff_auto_accept_offer]
# Enable this plugin.
# Default: true
enable = true
# Interval in seconds to check for new offers on Buff.
# Default: 300 (5 minutes)
interval = 300
# Enable support for Dota 2 items in addition to CS2/CS:GO.
# Default: false
dota2_support = false

# --- BUFF Auto Comment Plugin ---
# Automatically comments on your Buff trade history with purchase prices.
[buff_auto_comment]
# Enable this plugin.
# Default: false
enable = false
# Note: Configuration for page_size was removed as it's not used by the refactored plugin.

# --- BUFF Profit Report Plugin ---
# Generates and sends a profit report based on your Buff buy/sell history.
[buff_profit_report]
# Enable this plugin.
# Default: false
enable = false
# List of Apprise notification service URLs for sending the report.
servers = [] # e.g., ["tgram://YOUR_BOT_TOKEN/YOUR_CHAT_ID"]
# Time of day (24-hour format) to send the daily report.
# Default: "20:30"
send_report_time = "20:30"

# --- BUFF Auto On Sale Plugin ---
# Automatically lists items from your Steam inventory onto Buff at the lowest current market price or based on other criteria.
[buff_auto_on_sale]
# Enable this plugin.
# Default: false
enable = false
# Force refresh Buff inventory on each check. If false, cached inventory might be used.
# Default: true
force_refresh = true
# Use lowest price within a specific wear range instead of the overall lowest price for an item.
# This can lead to more API requests. Currently only supports CS2/CS:GO items.
# Default: false
use_range_price = false
# List of hours (0-23) during which the plugin will NOT operate.
# Example: blacklist_time = [1, 2, 3] # (for 1 AM, 2 AM, 3 AM)
blacklist_time = []
# List of hours (0-23) during which the plugin IS ALLOWED to operate. If empty, all hours (not in blacklist) are allowed.
whitelist_time = []
# Chance (1-100) for the plugin to run its on-sale logic during an interval. 100 means it always runs.
# Default: 100
random_chance = 100
# Custom description to add to items listed on Buff. Leave empty for no description.
description = ""
# Interval in seconds to check inventory and list items.
# Default: 1800 (30 minutes)
interval = 1800
# Interval in seconds between individual API requests to Buff to avoid rate limiting.
# Default: 10
sleep_seconds_to_prevent_buff_ban = 10

# Configuration for supplying items to Buff buy orders.
[buff_auto_on_sale.buy_order]
# Enable supplying to buy orders.
# Default: true
enable = true
# Only supply to buy orders that have auto-accept enabled on Buff.
# Default: true
only_auto_accept = true
# List of payment methods you accept for buy orders. Options: "支付宝", "微信".
# Default: ["支付宝"]
supported_payment_method = ["支付宝"]
# If an item's market price is below this value (in CNY), automatically supply it to the highest buy order
# that meets other criteria, instead of listing it for sale.
# Default: 5.0
min_price = 5.0

# Configuration for notifications when items are listed on sale. (Optional section)
[buff_auto_on_sale.on_sale_notification]
# Title of the notification. Placeholders: {game}, {sold_count}
# Default: "游戏 {game} 成功上架 {sold_count} 件饰品"
title = "游戏 {game} 成功上架 {sold_count} 件饰品"
# Body of the notification. Placeholders: {game}, {sold_count}, {item_list}
# Default: "上架详情:\\n{item_list}"
body = "上架详情:\\n{item_list}"

# Configuration for notifications when a CAPTCHA is required by Buff. (Optional section)
[buff_auto_on_sale.captcha_notification]
# Title of the CAPTCHA notification.
# Default: "上架饰品时出现验证码"
title = "上架饰品时出现验证码"
# Body of the CAPTCHA notification. Placeholders: {captcha_url}, {session}
# Default: "使用session={session}并使用浏览器打开以下链接并完成验证:\\n{captcha_url}"
body = "使用session={session}并使用浏览器打开以下链接并完成验证:\\n{captcha_url}"

# List of Apprise notification service URLs for this plugin's notifications.
# If empty, uses global notify_service.notifiers.
servers = []


# --- UUYouPin Auto Accept Offer Plugin ---
# Automatically accepts incoming trade offers on UUYouPin.
[uu_auto_accept_offer]
# Enable this plugin.
# Default: false
enable = false
# Interval in seconds to check for new offers.
# Default: 300 (5 minutes)
interval = 300
# UUYouPin AppKey for API access. Get this from your UUYouPin account.
# This key is crucial for the plugin to function.
# app_key = "" # User must provide if plugin enabled. Not in default to avoid confusion.

# --- UUYouPin Auto Lease Item Plugin ---
# Automatically lists items from your Steam inventory for lease on UUYouPin.
[uu_auto_lease_item]
# Enable this plugin.
# Default: false
enable = false
# Maximum number of days items can be leased for.
# Default: 60
lease_max_days = 60
# Items with a market price below this value (in CNY) will not be listed for lease.
# Default: 100.0
filter_price = 100.0
# Time of day (24-hour format, e.g., "17:30") when the auto-leasing task runs.
# Default: "17:30"
run_time = "17:30"
# Interval in minutes for periodic price adjustments of already leased items.
# Default: 31
interval = 31
# List of item names (or partial names) to exclude from leasing.
# Example: filter_name = ["Dragon Lore", "Souvenir"]
filter_name = ["物品A", "物品B"]
# Enable fixed lease ratio pricing. If true, lease price is set as a percentage of market price.
# Default: false
enable_fix_lease_ratio = false
# Lease price ratio if enable_fix_lease_ratio is true. E.g., 0.001 means 0.1% of market price per day.
# This price will not be lower than the dynamically calculated price.
# Default: 0.001
fix_lease_ratio = 0.001
# Compensation type for leased items. Refer to UUYouPin documentation for valid values.
# (0: non-member, 7: v1 member, other values might exist)
# Default: 7
compensation_type = 7

# --- UUYouPin Auto Sell Item Plugin ---
# Automatically lists items from your Steam inventory for sale on UUYouPin.
[uu_auto_sell_item]
# Enable this plugin.
# Default: false
enable = false
# Set selling price based on a take-profit ratio from the buy price.
# If false, prices are based on current market lowest, potentially with adjustment.
# Default: false
take_profile = false
# Profit ratio if take_profile is true. E.g., 0.1 means aim for 10% profit over buy price.
# Default: 0.1
take_profile_ratio = 0.1
# Time of day (24-hour format, e.g., "15:30") when the auto-selling task runs.
# Default: "15:30"
run_time = "15:30"
# Interval in minutes for periodic price adjustments of listed items (renamed from sell_interval).
# Default: 51
interval = 51
# Items with a calculated selling price above this value (in CNY) will not be listed. Set to 0 for no limit.
# Default: 1000.0
max_on_sale_price = 1000.0
# List of item names (or partial names) to specifically include for selling.
# Example: name = ["AK-47 | Redline", "AWP | Asiimov"]
name = ["AK", "A1"]
# List of item names (or partial names) to exclude from selling (overrides 'name' list).
# Example: blacklist_words = ["Souvenir", "Sticker"]
blacklist_words = ["黑名单词语1", "黑名单词语2"]
# Enable automatic price adjustment (undercutting by 0.01 CNY).
# Default: true
use_price_adjustment = true
# Only adjust price if the item's current price is above this threshold.
# Default: 1.0
price_adjustment_threshold = 1.0


# --- Steam Auto Accept Offer Plugin ---
# Automatically accepts incoming Steam trade offers that are gifts (i.e., you give nothing).
[steam_auto_accept_offer]
# Enable this plugin.
# Default: false
enable = false
# Interval in seconds to check for new gift offers.
# Default: 300 (5 minutes)
interval = 300

# --- ECOSteam.cn Plugin ---
# Integrates with ECOSteam.cn platform for various functionalities.
# Requires prior setup on ECOSteam open platform and RSA private key in config/rsakey.txt.
[ecosteam]
# Enable this plugin.
# Default: false
enable = false
# Your ECOSteam Partner ID. This is mandatory if the plugin is enabled.
partnerId = "" # Must be filled by user if enable = true

# Interval in seconds for checking and auto-accepting ECOSteam offers.
[ecosteam.auto_accept_offer]
# Default: 30
interval = 30

# Configuration for synchronizing items for sale across multiple platforms.
[ecosteam.auto_sync_sell_shelf]
# Enable sale shelf synchronization.
# Default: false
enable = false
# Main platform ("eco", "buff", or "uu") whose listings will be the source of truth.
# Default: "eco"
main_platform = "eco"
# List of other platforms to sync to. Example: ["uu", "buff"]
# ECOsteam is implicitly included if it's the main_platform or if this plugin is enabled.
enabled_platforms = ["uu"]

# Price ratios for each platform relative to each other.
# Example: If eco=1, buff=0.98, an item at 100 CNY on ECO will be listed at 98 CNY on Buff.
[ecosteam.auto_sync_sell_shelf.ratio]
eco = 1.0
uu = 1.0
buff = 1.0

# Configuration for synchronizing leased items with UUYouPin.
[ecosteam.auto_sync_lease_shelf]
# Enable lease shelf synchronization.
# Default: false
enable = false
# Main platform for leasing ("eco" or "uu").
# Default: "eco"
main_platform = "eco"
# Price ratios for leasing on each platform.
[ecosteam.auto_sync_lease_shelf.ratio]
eco = 1.0
uu = 1.0

# Interval in seconds for running the synchronization tasks.
# Default: 60
sync_interval = 60
# Queries per second limit for ECOSteam API. Adjust based on your API tier.
# Default: 10
qps = 10

# --- C5 Auto Accept Offer Plugin ---
# Automatically accepts incoming trade offers on C5Game.
[c5_auto_accept_offer]
# Enable this plugin.
# Default: false
enable = false
# Interval in seconds to check for new offers on C5Game.
# Default: 30
interval = 30
# Your C5Game AppKey. Obtain this from C5Game user panel -> Open API.
app_key = "" # Must be filled by user if enable = true


# === Advanced Settings ===

# Logging level for file logs. Options: "debug", "info", "warning", "error".
# "debug" is verbose, "info" is standard, "warning" and "error" for issues only.
# Default: "debug"
log_level = "debug"

# Number of days to retain log files in the 'logs' folder.
# Default: 7
log_retention_days = 7

# If true, the program will exit immediately on most errors without pausing.
# If false, it will pause for user input (e.g., "Press Enter to continue").
# Default: false
no_pause = false

# List of plugin names (snake_case, e.g., "buff_auto_on_sale") that should not be
# overwritten by automatic updates if you are running from source code.
# This is useful if you have made local modifications to specific plugins.
# Default: [] (empty list)
plugin_whitelist = []

# Enable automatic update checks when running from source code.
# If a new version is detected on GitHub, it will attempt to download and apply it.
# Default: false
source_code_auto_update = false
"""


STEAM_ERROR_CODES = {
    1: "成功",
    2: "失败",
    3: "无连接",
    4: "无连接，重试",
    5: "无效密码",
    6: "已在其他地方登录",
    7: "无效协议版本",
    8: "无效参数",
    9: "文件未找到",
    10: "忙碌",
    11: "无效状态",
    12: "无效名称",
    13: "无效电子邮件",
    14: "重复名称",
    15: "访问被拒绝",
    16: "超时",
    17: "已封禁",
    18: "账户未找到",
    19: "无效Steam ID",
    20: "服务不可用",
    21: "未登录",
    22: "待定",
    23: "加密失败",
    24: "权限不足",
    25: "超出限制",
    26: "已吊销",
    27: "已过期",
    28: "已被兑换",
    29: "重复请求",
    30: "已拥有",
    31: "IP未找到",
    32: "持久化失败",
    33: "锁定失败",
    34: "登录会话被替换",
    35: "连接失败",
    36: "握手失败",
    37: "IO失败",
    38: "远程断开连接",
    39: "购物车未找到",
    40: "被阻止",
    41: "被忽略",
    42: "无匹配项",
    43: "账户已禁用",
    44: "服务只读",
    45: "账户未特色",
    46: "管理员操作成功",
    47: "内容版本错误",
    48: "尝试切换CM失败",
    49: "需要密码以踢出会话",
    50: "已在其他地方登录", # Redundant with 6, but present in original
    51: "已暂停",
    52: "已取消",
    53: "数据损坏",
    54: "磁盘已满",
    55: "远程调用失败",
    56: "密码未设置",
    57: "外部帐户已取消链接",
    58: "PSN票证无效",
    59: "外部帐户已链接",
    60: "远程文件冲突",
    61: "密码不合法",
    62: "与上一个值相同",
    63: "账户登录被拒绝",
    64: "无法使用旧密码",
    65: "无效登录验证代码",
    66: "账户登录被拒绝，无邮件",
    67: "硬件不支持IPT",
    68: "IPT初始化错误",
    69: "受家长控制限制",
    70: "Facebook查询错误",
    71: "过期的登录验证代码",
    72: "IP登录限制失败",
    73: "账户被锁定",
    74: "需要验证电子邮件",
    75: "没有匹配的URL",
    76: "响应错误",
    77: "需要重新输入密码",
    78: "值超出范围",
    79: "意外错误",
    80: "已禁用",
    81: "无效CEG提交",
    82: "受限设备",
    83: "地区限制",
    84: "速率限制已超出",
    85: "需要双因素验证登录",
    86: "物品已删除",
    87: "账户登录被限速",
    88: "双因素验证码不匹配, 请检查shared_secret是否正确",
    89: "双因素激活码不匹配",
    90: "关联多个合作伙伴账户",
    91: "未修改",
    92: "无手机设备",
    93: "时间未同步",
    94: "短信验证码失败",
    95: "账户限制超出",
    96: "账户活动限制超出",
    97: "电话活动限制超出",
    98: "退款到钱包",
    99: "电子邮件发送失败",
    100: "未解决",
    101: "需要验证码", # Captcha
    102: "GSLT拒绝", # Game Server Login Token Rejected
    103: "GSLT所有者拒绝", # Owner of GSLT Denied
    104: "无效物品类型",
    105: "IP封禁",
    106: "GSLT已过期",
    107: "资金不足",
    108: "待处理事务过多", # Too many pending transactions
    109: "未找到站点许可证", # No site license found
    110: "WG网络发送超出限制", # WG Network send limit exceeded (likely internal Valve)
    111: "账户未添加好友", # Account not friends
    112: "有限用户账户", # Limited user account
    113: "无法移除物品", # Cannot remove item
    114: "账户已删除",
    115: "现有用户取消许可证", # Existing user cancelled license
    116: "社区冷却中", # Community Cooldown
    117: "未指定启动器", # Launcher not specified
    118: "必须同意用户协议", # Must agree to EULA
    119: "启动器已迁移", # Launcher migrated (e.g. Bethesda to Steam)
    120: "Steam领域不匹配", # Steam realm mismatch
    121: "无效签名",
    122: "解析失败", # Failed to parse
    123: "无验证手机", # No verified phone number
}

# Note: CONFIG_FILE_PATH was changed to point to config.toml.
# The original DEFAULT_CONFIG_JSON is now named DEFAULT_CONFIG_JSON_REFERENCE
# to avoid confusion and is kept for potential fallback or reference.
# The primary default configuration content is now in DEFAULT_CONFIG_TOML.
# DEFAULT_JSON_CONFIG_PATH_FOR_REFERENCE still points to the .json5 path for clarity.
