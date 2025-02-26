import apprise
import json5

from utils.logger import PluginLogger, handle_caught_exception
from utils.static import CONFIG_FILE_PATH

logger = PluginLogger('通知服务')
config = {}
try:
    with open(CONFIG_FILE_PATH, 'r') as file:
        config = json5.load(file)
    config = config.get('notify_service', {})
    if config == {}:
        logger.warning('未配置通知服务，通知功能将不可用，请在配置文件中配置通知服务')
    elif config.get('notifiers'):
        logger.info(f'已配置{len(config.get("notifiers"))}个通知服务')
except Exception as e:
    logger.warning('通知服务异常，请检查配置文件是否正确配置')
    handle_caught_exception(e)
    pass


def send_notification(message, title=''):
    if config.get('notifiers', False):
        for black in config.get('blacklist_words', []):
            if black in message or black in title:
                logger.debug(f'消息中包含黑名单词: {black}，已被过滤')
                return
        for notifier in config.get('notifiers', []):
            try:
                if config.get('custom_title'):
                    title = config.get('custom_title')
                    message = f'{title}\n{message}'
                else:
                    title = title if title else 'Steamauto 通知'
                apobj = apprise.Apprise()
                apobj.add(notifier)
                apobj.notify(title=title, body=message) # type: ignore
            except Exception as e:
                handle_caught_exception(e)
                logger.error(f'发送通知失败: {str(e)}')
