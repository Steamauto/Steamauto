import os

config = {"no_pause": False}

VERSION_FILE = "version.json"
APPRISE_ASSET_FOLDER = "Apprise"
LOGS_FOLDER = "logs"
CONFIG_FOLDER = "config"
CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, "config.json")
if not os.path.exists(CONFIG_FILE_PATH):
    CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, "config.json5")
EXAMPLE_CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, "config.example.json5")
if not os.path.exists(EXAMPLE_CONFIG_FILE_PATH):
    EXAMPLE_CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, "config.example.json5")
BUFF_COOKIES_FILE_PATH = os.path.join(CONFIG_FOLDER, "buff_cookies.txt")
UU_TOKEN_FILE_PATH = os.path.join(CONFIG_FOLDER, "uu_token.txt")
STEAM_ACCOUNT_INFO_FILE_PATH = os.path.join(CONFIG_FOLDER, "steam_account_info.json")
if not os.path.exists(STEAM_ACCOUNT_INFO_FILE_PATH):
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
[{
  // Steam 的数字 ID（字符串格式）
  "steamid": "",

  // Steam 令牌参数（用于身份验证）
  "shared_secret": "",

  // Steam 令牌参数（用于身份验证）
  "identity_secret": "",

  // Steam 网页 API 密钥（用于访问 Steam API）
  "api_key": "",

  // Steam 登录时填写的用户名
  "steam_username": "",

  // Steam 登录时填写的密码
  "steam_password": ""
}]
"""

DEFAULT_BUFF_ACCOUNT_JSON = """
{
  // 格式如下，其中 steam_username为steam_account_info.json中steam账号的steam_username参数，buff_cookie 为登录 buff 的 Cookie
  //若有多个可按照以下格式添加，注意使用英文逗号分割，若仅有一个请将多余的样例删除
  //请确保cookie对应的steam_username1在steam_account_info.json已存在
  "steam_username1": "buff_cookie1",
  "steam_username2": "buff_cookie2",
}
"""
