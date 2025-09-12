import os
import sys

from utils.build_info import info

is_latest_version = False
no_pause = False

CURRENT_VERSION = "5.7.2"

VERSION_FILE = "version.json"
LOGS_FOLDER = "logs"
CONFIG_FOLDER = "config"
PLUGIN_FOLDER = "plugins"
CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, "config.json5")
BUFF_COOKIES_FILE_PATH = os.path.join(CONFIG_FOLDER, "buff_cookies.txt")
UU_TOKEN_FILE_PATH = os.path.join(CONFIG_FOLDER, "uu_token.txt")
STEAM_ACCOUNT_INFO_FILE_PATH = os.path.join(CONFIG_FOLDER, "steam_account_info.json5")
STEAM_INVENTORY_FILE_PATH = os.path.join(CONFIG_FOLDER, "steam_inventory.json5")
SESSION_FOLDER = "session"
os.makedirs(SESSION_FOLDER, exist_ok=True)
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

DEFAULT_CONFIG_JSON = r"""
{
  // 登录Steam时是否开启SSL验证，正常情况下不建议关闭SSL验证
  "steam_login_ignore_ssl_error": false,
  
  // 是否开启本地加速功能
  // 本地加速功能并非100%可用, 若开启后仍然无法正常连接Steam属于正常情况, 最优解决方案是使用海外服务器
  // 请注意：开启此功能必须关闭Steam登录SSL验证，即steam_login_ignore_ssl_error必须设置为true
  "steam_local_accelerate": false,

  // 关于代理功能的说明：
  // 如果你使用了Clash或v2RayN或ShadowSocksR等代理软件，需要在此配置文件内额外配置。
  // 是否手动指定Steam代理(该功能只会代理Steam)
  "use_proxies": false,

  // 本地代理地址。代理设置只会应用于Steam。使用前需要确保use_proxies已经设置为true
  // http和https代理地址一般情况下是相同的，如果你不懂这是什么，请保持相同
  // 以Clash Verge为例，进入设置，点击端口设置，把下面的7890改成混合代理(或HTTP(S))的端口号
  "proxies": {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890"
  },
  
  "notify_service": {
    // 通知器 使用Apprise格式 支持Telegram、钉钉、飞书、WxPusher、Server酱等
    // 配置详见https://github.com/caronc/apprise/wiki
    "notifiers": [],
    // 自定义标题，为空则使用默认标题
    "custom_title": "",
    // 是否包含Steam账号信息
    "include_steam_info": true,
    // 通知器屏蔽词 如果通知内容包含以下词语则不会发送通知
    "blacklist_words": [
      "黑名单词语1",
      "黑名单词语2"
    ]
  },

  // BUFF 自动发货插件配置
  "buff_auto_accept_offer": {
    // 是否启用BUFF自动发货报价功能
    "enable": true,
    // 每次检查是否有新报价的间隔（轮询间隔），单位为秒
    "interval": 300,
    // 是否开启dota2支持
    "dota2_support": false
  },
  // BUFF 自动备注购买价格插件配置
  "buff_auto_comment": {
    // 是否启用BUFF自动备注购买价格功能
    "enable": false
  },
  // BUFF 自动计算利润插件配置
  "buff_profit_report": {
    // 是否启用BUFF自动计算利润功能
    "enable": false,
    // 通知服务器列表，使用Apprise格式，详见https://github.com/caronc/apprise/
    "servers": [
    ],
    // 每日发送报告时间, 24小时制
    "send_report_time": "20:30"
  },
  // BUFF 自动上架插件配置
  "buff_auto_on_sale": {
    // 是否启用BUFF自动以最低价上架所有库存
    "enable": false,
    // 每次检查库存强制刷新BUFF库存, 若为否, 刷新不一定会加载最新库存
    "force_refresh": true,
    // 使用磨损区间最低价上架, 若为否, 则使用类型最低价上架
    // 注意: 该功能会导致增加更多的请求, 请谨慎开启
    "use_range_price": false,
    // 黑名单时间, 为小时, int格式, 空为不启用黑名单, 当前小时如果等于黑名单时间, 则不会自动上架
    "blacklist_time": [],
    // 白名单时间, 为小时, int格式, 空为不启用白名单, 当前小时如果不等于白名单时间, 则不会自动上架
    "whitelist_time": [],
    // 随机上架几率, 为整数, 1~100, 100为100%上架, 1为1%上架, 0为不上架
    "random_chance": 100,
    // 商品上架描述, 为字符串, 为空则不填写描述
    "description": "",
    // 检查库存间隔时间
    "interval": 1800,
    // 每个请求间隔时间 (秒) - 用于防止被BUFF封禁
    "sleep_seconds_to_prevent_buff_ban": 10,
    // 供应求购相关配置
    "buy_order": {
      // 是否供应求购订单
      "enable": true,
      // 是否只供应给开启自动收货的求购订单
      "only_auto_accept": true,
      // 支持收款方式 支付宝 微信
      "supported_payment_method": ["支付宝"],
      // 低于多少金额的商品直接塞求购
      "min_price": 5
    },
    // 上架通知配置(如不需要可直接删除)
    "on_sale_notification": {
        // 上架通知标题
        "title": "游戏 {game} 成功上架 {sold_count} 件饰品",
        // 上架通知内容
        "body": "上架详情:\n{item_list}"
    },
    // 出现验证码通知配置(如不需要可直接删除)
    "captcha_notification": {
        // 出现验证码通知标题
        "title": "上架饰品时出现验证码",
        // 出现验证码通知内容
        "body": "使用session={session}并使用浏览器打开以下链接并完成验证:\n{captcha_url}"
    },
    // 通知服务器列表，使用Apprise格式，详见https://github.com/caronc/apprise/
    "servers": [
    ]
  },
  // 悠悠有品自动发货插件配置
  "uu_auto_accept_offer": {
    // 悠悠有品自动发货功能是否启用，默认为false
    "enable": false,
    // 每次检查是否有新报价的间隔（轮询间隔），单位为秒
    "interval": 300
  },
  // 悠悠有品租赁自动上架配置
  "uu_auto_lease_item": {
    // 悠悠有品租赁自动上架功能是否启用，默认为false
    "enable": false,
    // 最长租赁时间，默认60天
    "lease_max_days": 60,
    // 价格低于 filter_price 的物品不会上架，默认100
    "filter_price": 100,
    // 自动上架租赁每天定时运行时间
    "run_time": "17:30",
    // 已上架租赁的物品可以定时修改价格（防止很长时间没出租出去，转租的也可以修改）。设置的轮询间隔，单位为分钟
    "interval": 31,
    // 不出租的物品名字列表，示例：["物品A", "物品B"]（名字可以不写全，但是要写对，比如M4A1印花集）
    "filter_name": ["物品A", "物品B"],
    // 按现价固定比例设置价格
    "enable_fix_lease_ratio": false,
    // 出租价格比例, 如现价1000元, 0.001比例，出租价格为1000 * 0.001 = 1元 (不会低于正常计算的出租价格)
    "fix_lease_ratio": 0.001,
    // 赔付方式: 0(非会员), 7(v1), 其余的不知道
    "compensation_type": 7
  },
  // 悠悠有品出售自动上架配置
  "uu_auto_sell_item": {
    // 悠悠有品出售自动上架功能是否启用，默认为false
    "enable": false,
    // 按照止盈率设置定价
    "take_profile": false,
    // 止盈率
    "take_profile_ratio": 0.1,
    // 自动上架每天定时运行时间
    "run_time": "15:30",
    // 每隔多长时间重新请求市场并改价一次（单位：分钟）
    "sell_interval": 20,
    // 价格高于 max_on_sale_price 的物品不会上架，设置为0则不限制
    "max_on_sale_price": 1000,
    // 已上架的物品可以定时修改价格。设置的轮询间隔，单位为分钟
    "interval": 51,
    // 出售的物品名字列表，示例：["物品A", "物品B"]（名字可以不写全，但是要写对）
    "name": [
      "AK",
      "A1"
    ],
    // 不出售也不参与改价的物品名字列表，示例：["物品A", "物品B"]（优先级高于出售的物品名字列表）
    "blacklist_words": [
      "黑名单词语1",
      "黑名单词语2"
    ],
    "use_price_adjustment": true, // 是否开启自动压价（-0.01）的功能
    "price_adjustment_threshold": 1.0 // 价格高于此值才会自动压价
  },
  // Steam 自动接受礼物报价插件配置
  "steam_auto_accept_offer": {
    // 是否开启自动接受Steam礼物报价（无需支出任何Steam库存中的物品的报价）
    "enable": false,
    // 每次检查报价列表的间隔（轮询间隔），单位为秒
    "interval": 300
  },
  // ECOSteam.cn 插件配置
  // 请提前接入开放平台 私钥请放置在config目录下的rsakey.txt文件中
  "ecosteam": {
    "enable": false,
    "partnerId": "", // 必填！用于登录ECOsteam平台
    "auto_accept_offer": {
      "interval": 30
    },
    "auto_sync_sell_shelf": { // 自动同步各平台的上架商品, 与主平台一致
      "enable": false,
      "main_platform": "eco", // 主平台。主平台的上架信息不会被程序改动，按照价格比例自动同步到其他平台。可选值为"buff"/"uu"/"eco"，不可重复
      "enabled_platforms": ["uu"], // 可以填入多个平台，如["buff", "uu"]，可选值为"buff"或"uu"，不可重复。ECO平台已经强制开启，无需手动填写
      "ratio": { // 各平台上架价格的比例
        "eco": 1,
        "uu": 1,
        "buff": 1
      }
    },
    "auto_sync_lease_shelf": { // 与悠悠有品平台同步租赁商品
      "enable": false,
      "main_platform": "eco", // 主平台。主平台的上架信息不会被程序改动，按照价格比例自动同步到其他平台。可选值为""uu"/"eco"，不可重复
      "ratio": { // 各平台租赁价格的比例
        "eco": 1,
        "uu": 1
      }
    },
    "sync_interval": 60, // 同步间隔时间，单位为秒。不建议设置太长，否则可能会导致同步不及时，导致平台账号被封禁
    "qps": 10 //每秒最大请求数。如果你是白名单大会员，建议设置为30。如果你不知道这是什么，请保持默认值。
  },
  "c5_auto_accept_offer": { // C5自动发货插件配置
    "enable": false, // 是否启用C5自动发货功能
    "interval": 30, // 每次检查是否有新报价的间隔（轮询间隔），单位为秒
    "app_key": "" // C5Game的AppKey 在 https://www.c5game.com/user/user/open-api 申请
  },
  // 存储在硬盘的日志等级，可选值为"debug"/"info"/"warning"/"error"
  "log_level": "debug",
  // 本地日志保留天数
  "log_retention_days": 7,
  // 填写为true后，程序在出现错误后就会直接停止运行。如果你不知道你在做什么，请不要将它设置为true
  "no_pause": false,
  // 本地插件白名单 当以下本地插件与程序附带不一样时，将不会被覆盖
  "plugin_whitelist": [],
  // 源码运行时自动更新程序
  "source_code_auto_update": false
}
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
    50: "已在其他地方登录",
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
    101: "需要验证码",
    102: "GSLT拒绝",
    103: "GSLT所有者拒绝",
    104: "无效物品类型",
    105: "IP封禁",
    106: "GSLT已过期",
    107: "资金不足",
    108: "待处理事务过多",
    109: "未找到站点许可证",
    110: "WG网络发送超出限制",
    111: "账户未添加好友",
    112: "有限用户账户",
    113: "无法移除物品",
    114: "账户已删除",
    115: "现有用户取消许可证",
    116: "社区冷却中",
    117: "未指定启动器",
    118: "必须同意用户协议",
    119: "启动器已迁移",
    120: "Steam领域不匹配",
    121: "无效签名",
    122: "解析失败",
    123: "无验证手机",
}
