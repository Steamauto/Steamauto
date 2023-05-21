import os

config = {"no_pause": False}

APPRISE_ASSET_FOLDER = "Apprise"
LOGS_FOLDER = "logs"
CONFIG_FOLDER = "config"
CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, "config.json")
BUFF_COOKIES_FILE_PATH = os.path.join(CONFIG_FOLDER, "buff_cookies.txt")
UU_TOKEN_FILE_PATH = os.path.join(CONFIG_FOLDER, "uu_token.txt")
STEAM_ACCOUNT_INFO_FILE_PATH = os.path.join(CONFIG_FOLDER, "steam_account_info.json")
SESSION_FOLDER = "session"
STEAM_SESSION_PATH = os.path.join(SESSION_FOLDER, "steam_session.pkl")
DEV_FILE_FOLDER = "dev"
BUFF_ACCOUNT_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, "buff_account.json")
MESSAGE_NOTIFICATION_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, "message_notification.json")
STEAM_TRADE_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, "steam_trade.json")
SELL_ORDER_HISTORY_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, "sell_order_history.json")
SHOP_LISTING_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, "shop_listing.json")
EXAMPLE_CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, "config.example.json")
SUPPORT_GAME_TYPES = [{"game": "csgo", "app_id": 730}, {"game": "dota2", "app_id": 570}]

DEFAULT_STEAM_ACCOUNT_JSON = """
{
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
}
"""
