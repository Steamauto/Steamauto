import requests
from apprise import logger
from apprise.decorators import notify


@notify(on="ftqq", name="Server酱通知插件")
def server_chan_notification_wrapper(body, title, notify_type, *args, **kwargs):
    token = kwargs['meta']['host']
    try:
        resp = requests.get('https://sctapi.ftqq.com/%s.send?title=%s&desp=%s' % (token, title, body))
        if resp.status_code == 200:
            if resp.json()['code'] == 0:
                logger.info('Server酱通知发送成功\n')
                return True
            else:
                logger.error('Server酱通知发送失败, return code = %d' % resp.json()['code'])
                return False
        else:
            logger.error('Server酱通知发送失败, http return code = %s' % resp.status_code)
            return False
    except Exception as e:
        logger.error('Server酱通知插件发送失败！')
        logger.error(e)
        return False

@notify(on="pushplus", name="pushplus通知插件")
def server_chan_notification_wrapper(body, title, notify_type, *args, **kwargs):
    token = kwargs['meta']['host']
    url = 'http://www.pushplus.plus/send'
    headers = {'Content-Type': 'application/json'}
    payload = {
        'token': token,
        'title': title,
        'content': body,
        'template': 'html'
    }
    
    try:
        resp = requests.post(url, json=payload, headers=headers)
        if resp.status_code == 200:
            if resp.json()['code'] == 200:
                logger.info('pushplus通知发送成功\n')
                return True
            else:
                logger.error('pushplus通知发送失败, return code = %d' % resp.json()['code'])
                return False
        else:
            logger.error('pushplus通知发送失败, http return code = %s' % resp.status_code)
            return False
    except Exception as e:
        logger.error('pushplus通知插件发送失败！')
        logger.error(e)
        return False

    # Returning True/False is a way to relay your status back to Apprise.
    # Returning nothing (None by default) is always interpreted as a Success
