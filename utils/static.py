import os

APPRISE_ASSET_FOLDER = 'Apprise'
LOGS_FOLDER = 'logs'
CONFIG_FOLDER = 'config'
CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, 'config.json')
BUFF_COOKIES_FILE_PATH = os.path.join(CONFIG_FOLDER, 'buff_cookies.txt')
UU_TOKEN_FILE_PATH = os.path.join(CONFIG_FOLDER, 'uu_token.txt')
STEAM_ACCOUNT_INFO_FILE_PATH = os.path.join(CONFIG_FOLDER, 'steam_account_info.txt')
SESSION_FOLDER = 'session'
STEAM_SESSION_PATH = os.path.join(SESSION_FOLDER, 'steam_session.pkl')
DEV_FILE_FOLDER = 'dev'
BUFF_ACCOUNT_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, 'buff_account.json')
MESSAGE_NOTIFICATION_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, 'message_notification.json')
STEAM_TRADE_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, 'steam_trade.json')
SELL_ORDER_HISTORY_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, 'sell_order_history.json')
SHOP_LISTING_DEV_FILE_PATH = os.path.join(DEV_FILE_FOLDER, 'shop_listing.json')
EXAMPLE_CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, 'config.example.json')