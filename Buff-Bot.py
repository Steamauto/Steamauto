import logging
import os
import shutil
import sys
import json
import traceback

import apprise
from apprise.AppriseAsset import *
from apprise.decorators import notify
from steampy.client import SteamClient
from lxml import etree
import requests
import time
import FileUtils

logger = logging.getLogger("Buff-Bot")
logger.setLevel(logging.DEBUG)
s_handler = logging.StreamHandler()
s_handler.setLevel(logging.INFO)
s_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(message)s'))
logger.addHandler(s_handler)


def checkaccountstate(headers=None):
    try:
        userid = etree.HTML(requests.get('https://buff.163.com/', headers=headers).text).xpath("/html//strong["
                                                                                               "@id='navbar-user-name"
                                                                                               "']/text()")
        return userid[0]
    except IndexError:
        logger.info('BUFF账户登录状态失效，请检查cookies.txt！')
        logger.info('点击任何键继续...')
        os.system('pause >nul')
        sys.exit()


@notify(on="ftqq", name="Server酱通知插件")
def server_chan_notification_wrapper(body, title, notify_type, *args, **kwargs):
    token = kwargs['meta']['host']
    try:
        resp = requests.get('https://sctapi.ftqq.com/%s.send?title=%s&desp=%s' % (token, title, body))
        if resp.status_code == 200:
            if resp.json()['code'] == 0:
                logger.info('Server酱通知发送成功')
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

    # Returning True/False is a way to relay your status back to Apprise.
    # Returning nothing (None by default) is always interpreted as a Success


def main():
    asset = AppriseAsset(plugin_paths=[__file__])
    os.system("title Buff-Bot 作者：甲甲")
    logger.info("欢迎使用Buff-Bot 作者：甲甲")
    logger.info("正在初始化...")
    first_run = False
    if not os.path.exists("config.json"):
        first_run = True
        shutil.copy("config.json.example", "config.json")
    if not os.path.exists("cookies.txt"):
        first_run = True
        FileUtils.writefile("cookies.txt", "session=")
    if not os.path.exists("steamaccount.json"):
        first_run = True
        FileUtils.writefile("steamaccount.json", json.dumps({"steamid": "", "shared_secret": "",
                                                             "identity_secret": "", "api_key": "",
                                                             "steam_username": "", "steam_password": ""}))
    if first_run:
        logger.info("检测到首次运行，已为您生成配置文件，请按照README提示填写配置文件！")
        logger.info('点击任何键继续...')
        os.system('pause >nul')
    config = json.loads(FileUtils.readfile("config.json"))
    ignoredoffer = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27',
    }
    logger.info("正在准备登录至BUFF...")
    headers['Cookie'] = FileUtils.readfile('cookies.txt')
    logger.info("已检测到cookies，尝试登录")
    logger.info("已经登录至BUFF 用户名：" + checkaccountstate(headers=headers))

    try:
        logger.info("正在登录Steam...")
        acc = json.loads(FileUtils.readfile('steamaccount.json'))
        client = SteamClient(acc.get('api_key'))
        SteamClient.login(client, acc.get('steam_username'), acc.get('steam_password'), 'steamaccount.json')
        logger.info("登录完成！\n")
    except FileNotFoundError:
        logger.info('未检测到steamaccount.json，请添加到steamaccount.json后再进行操作！')
        logger.info('点击任何键继续...')
        os.system('pause >nul')
        sys.exit()

    while True:
        logger.info("正在检查Steam账户登录状态...")
        if not client.is_session_alive():
            logger.info("Steam登录状态失效！程序退出...")
            sys.exit()
        logger.info("Steam账户状态正常")
        logger.info("正在进行待发货/待收货饰品检查...")
        checkaccountstate()
        response = requests.get("https://buff.163.com/api/message/notification", headers=headers)
        to_deliver_order = json.loads(response.text).get('data').get('to_deliver_order')
        to_deliver_count = int(to_deliver_order.get('csgo')) + int(to_deliver_order.get('dota2'))
        if to_deliver_count != 0:
            logger.info("检测到", to_deliver_count, "个待发货请求！")
        response = requests.get("https://buff.163.com/api/market/steam_trade", headers=headers)
        trade = json.loads(response.text).get('data')
        logger.info("查找到", len(trade), "个待处理的交易报价请求！")
        try:
            if len(trade) != 0:
                i = 0
                for go in trade:
                    i += 1
                    offerid = go.get('tradeofferid')
                    logger.info("正在处理第", i, "个交易报价 报价ID", offerid)
                    if offerid not in ignoredoffer:
                        try:
                            logger.info("正在接受报价...")
                            client.accept_trade_offer(offerid)
                            ignoredoffer.append(offerid)
                            logger.info("接受完成！已经将此交易报价加入忽略名单！\n")
                            if 'sell_notification' in config:
                                apprise_obj = apprise.Apprise()
                                for server in config['servers']:
                                    apprise_obj.add(server)
                                # TODO: 优化消息内容, 支持更多消息内容, 需要先看BUFF的API
                                apprise_obj.notify(
                                    title=config['sell_notification']['title'],
                                    body=config['sell_notification']['body'],
                                )
                        except Exception as e:
                            logger.info(traceback.logger.info_exc())
                            logger.info("出现错误，稍后再试！")
                    else:
                        logger.info("该报价已经被处理过，跳过.\n")
                logger.info("暂无BUFF报价请求.将在180秒后再次检查BUFF交易信息！\n")
            else:
                logger.info("暂无BUFF报价请求.将在180秒后再次检查BUFF交易信息！\n")
        except Exception:
            logger.info(traceback.logger.info_exc())
            logger.info("出现错误，稍后再试！")
        time.sleep(180)


if __name__ == '__main__':
    main()
