import os

global no_pause
no_pause = False

VERSION_FILE = "version.json"
APPRISE_ASSET_FOLDER = "Apprise"
LOGS_FOLDER = "logs"
CONFIG_FOLDER = "config"
CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, "config.json5")
BUFF_COOKIES_FILE_PATH = os.path.join(CONFIG_FOLDER, "buff_cookies.txt")
UU_TOKEN_FILE_PATH = os.path.join(CONFIG_FOLDER, "uu_token.txt")
STEAM_ACCOUNT_INFO_FILE_PATH = os.path.join(CONFIG_FOLDER, "steam_account_info.json5")
SESSION_FOLDER = "session"
DEV_FILE_FOLDER = "dev"
BUFF_ACCOUNT_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, "buff_account.json")
MESSAGE_NOTIFICATION_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, "message_notification.json")
STEAM_TRADE_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, "steam_trade.json")
SELL_ORDER_HISTORY_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, "sell_order_history.json")
SHOP_LISTING_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, "shop_listing.json")
TO_DELIVER_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, "to_deliver_{game}.json")
SUPPORT_GAME_TYPES = [{"game": "csgo", "app_id": 730}, {"game": "dota2", "app_id": 570}]
UU_ARG_FILE_PATH = "uu.txt"

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

  // 是否使用Steam代理功能(该功能只会代理Steam)
  "use_proxies": false,

  // 本地代理地址, 使用前需要确保use_proxies已经设置为true
  // 这里以clash为例，clash默认监听7890端口，如果你使用的是其他代理软件，请自行修改端口
  "proxies": {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890"
  },
  
  // 填写为true后，程序在出现错误后就会直接停止运行。如果你不知道你在做什么，请不要将它设置为true
  "no_pause": false,

  // BUFF 自动发货插件配置
  "buff_auto_accept_offer": {
    // 是否启用BUFF自动发货报价功能
    "enable": true,
    // 每次检查是否有新报价的间隔（轮询间隔），单位为秒
    "interval": 300,
    // 是否开启出售保护(自动发货前检查其他卖家最低价，若低于保护价格则不会自动接受报价s)
    "sell_protection": false,
    // 出售保护价格，若其他卖家最低价低于此价格，则不会进行出售保护
    "protection_price": 30,
    // 出售价格保护比例，若出售价格低于此比例乘以其他卖家最低价格，则不会自动接受报价
    "protection_price_percentage": 0.9,
    // 出售通知配置(如不需要可直接删除)
    "sell_notification": {
      // 出售通知标题
      "title": "成功出售{game}饰品: {item_name} * {sold_count}",
      // 出售通知内容
      "body": "![good_icon]({good_icon})\n游戏: {game}\n饰品: {item_name}\n出售单价: {buff_price} RMB\nSteam单价(参考): {steam_price} USD\nSteam单价(参考): {steam_price_cny} RMB\n![buyer_avatar]({buyer_avatar})\n买家: {buyer_name}\n订单时间: {order_time}"
    },
    // 出售保护通知配置(如不需要可直接删除)
    "protection_notification": {
      // 出售保护通知标题（如不需要可直接删除）
      "title": "{game}饰品: {item_name} 未自动接受报价, 价格与市场最低价相差过大",
      // 出售保护通知内容（如不需要可直接删除）
      "body": "请自行至BUFF确认报价!"
    },
    // 报价与BUFF出售商品不匹配通知配置(如不需要可直接删除)
    "item_mismatch_notification": {
      // 报价与BUFF出售商品不匹配通知标题
      "title": "BUFF出售饰品与Steam报价饰品不匹配",
      // 报价与BUFF出售商品不匹配通知内容
      "body": "请自行至BUFF确认报价!(Offer: {offer_id})"
    },
    // BUFF Cookies失效通知配置
    "buff_cookie_expired_notification": {
      // BUFF Cookies失效通知标题（如不需要可直接删除）
      "title": "BUFF Cookie已过期, 请重新登录",
      // BUFF Cookies失效通知内容（如不需要可直接删除）
      "body": "BUFF Cookie已过期, 请重新登录"
    },
    // 二维码登录BUFF通知配置
    "buff_login_notification": {
      // 二维码登录BUFF通知标题（如不需要可直接删除）
      "title": "请扫描二维码登录BUFF"
    },
    // 通知服务器列表，使用Apprise格式，详见https://github.com/caronc/apprise/
    "servers": [
    ]
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
    // 上架通知配置(如不需要可直接删除)
    "on_sale_notification": {
        // 上架通知标题
        "title": "游戏 {game} 成功上架 {sold_count} 件饰品",
        // 上架通知内容
        "body": "上架详情:\n{item_list}"
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
    "interval": 300,
    // 是否主动开启主动报价自动令牌确认(该功能有安全风险，请知晓风险后开启)
    "auto_confirm": false
  },
  // Steam 自动接受礼物报价插件配置
  "steam_auto_accept_offer": {
    // 是否开启自动接受Steam礼物报价（无需支出任何Steam库存中的物品的报价）
    "enable": false,
    // 每次检查报价列表的间隔（轮询间隔），单位为秒
    "interval": 300
  },
  // 是否开启开发者模式，具体功能请查看代码，非开发者请勿开启！开启后无法正常使用！
  "development_mode": false
}
"""


def set_no_pause(no_pause_):
    global no_pause
    no_pause = no_pause_


def get_no_pause():
    global no_pause
    return no_pause
