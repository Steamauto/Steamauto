# Apprise\pushplus.py

import requests
from apprise import logger
from apprise.decorators import notify


class PushPlusNotifier:
    @notify(on="pushplus", name="pushplus通知插件")
    def send_notification(self, body, title, notify_type, *args, **kwargs):
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
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()
            json_response = resp.json()
            if json_response.get('code') == 200:
                logger.info('pushplus通知发送成功\n')
                return True
            else:
                logger.error(f"pushplus通知发送失败, return code = {json_response.get('code')}")
                return False
        except requests.exceptions.HTTPError as http_err:
            logger.error(f'HTTP错误发生: {http_err}')
        except requests.exceptions.Timeout as timeout_err:
            logger.error(f'超时错误: {timeout_err}')
        except requests.exceptions.RequestException as req_err:
            logger.error(f'请求异常: {req_err}')
        except Exception as e:
            logger.error('pushplus通知插件发送失败！')
            logger.error(str(e))
        return False
