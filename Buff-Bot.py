import logging
import os
import shutil
import sys
import json

import apprise
from apprise.AppriseAsset import *
from apprise.decorators import notify
from steampy.client import SteamClient
from requests.exceptions import SSLError, ConnectTimeout
import requests
import time
from libs import FileUtils
from colorama import Fore
import pickle

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27',
}


def checkaccountstate(dev=False):
    if dev and os.path.exists('dev/buff_account.json'):
        logger.info('开发模式，使用本地账号')
        return json.loads(FileUtils.readfile('dev/buff_account.json'))['data']['nickname']
    else:
        response_json = requests.get('https://buff.163.com/account/api/user/info', headers=headers).json()
        if response_json['code'] == 'OK':
            if 'data' in response_json:
                if 'nickname' in response_json['data']:
                    return response_json['data']['nickname']
        logger.error('BUFF账户登录状态失效，请检查cookies.txt！')
        logger.info('点击任何键继续...')
        input()
        sys.exit()


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

    # Returning True/False is a way to relay your status back to Apprise.
    # Returning nothing (None by default) is always interpreted as a Success


def format_str(text: str, trade):
    for good in trade['goods_infos']:
        good_item = trade['goods_infos'][good]
        created_at_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade['created_at']))
        text = text.format(item_name=good_item['name'], steam_price=good_item['steam_price'],
                           steam_price_cny=good_item['steam_price_cny'], buyer_name=trade['bot_name'],
                           buyer_avatar=trade['bot_avatar'], order_time=created_at_time_str, game=good_item['game'],
                           good_icon=good_item['original_icon_url'])
    return text


def main():
    client = None
    development_mode = False
    sell_protection = True
    protection_price = 30
    protection_price_percentage = 0.9
    asset = AppriseAsset(plugin_paths=[__file__])
    os.system("title Buff-Bot https://github.com/jiajiaxd/Buff-Bot")

    logger.info("欢迎使用Buff-Bot Github:https://github.com/jiajiaxd/Buff-Bot")
    logger.info("正在初始化...")
    first_run = False
    try:
        if not os.path.exists("config/config.json"):
            first_run = True
            shutil.copy("config/config.example.json", "config/config.json")
    except FileNotFoundError:
        logger.error("未检测到config.example.json，请前往GitHub进行下载，并保证文件和程序在同一目录下。")
        logger.info('点击任何键继续...')
        sys.exit()
    if not os.path.exists("config/cookies.txt"):
        first_run = True
        FileUtils.writefile("config/cookies.txt", "session=")
    if not os.path.exists("config/steamaccount.json"):
        first_run = True
        FileUtils.writefile("config/steamaccount.json", json.dumps({"steamid": "", "shared_secret": "",
                                                                    "identity_secret": "", "api_key": "",
                                                                    "steam_username": "", "steam_password": ""}))
    if first_run:
        logger.info("检测到首次运行，已为您生成配置文件，请按照README提示填写配置文件！")
        logger.info('点击任何键继续...')
        input()
    config = json.loads(FileUtils.readfile("config/config.json"))
    ignoredoffer = []
    orderinfo = {}
    if 'dev' in config and config['dev']:
        development_mode = True
    if development_mode:
        logger.info("开发者模式已开启")
    if 'sell_protection' in config:
        sell_protection = config['sell_protection']
    if 'protection_price' in config:
        protection_price = config['protection_price']
    if 'protection_price_percentage' in config:
        protection_price_percentage = config['protection_price_percentage']
    logger.info("正在准备登录至BUFF...")
    headers['Cookie'] = FileUtils.readfile('config/cookies.txt')
    logger.info("已检测到cookies，尝试登录")
    logger.info("已经登录至BUFF 用户名：" + checkaccountstate(dev=development_mode))

    if development_mode:
        logger.info("开发者模式已开启，跳过Steam登录")
    else:
        relog = False
        if not os.path.exists('steam_session.pkl'):
            logger.info("未检测到steam_session.pkl文件存在")
            relog = True
        else:
            logger.info("检测到缓存的steam_session.pkl文件存在，正在尝试登录")
            with open('steam_session.pkl', 'rb') as f:
                client = pickle.load(f)
                if client.is_session_alive():
                    logger.info("登录成功")
                else:
                    relog = True
        if relog:
            try:
                logger.info("正在登录Steam...")
                acc = json.loads(FileUtils.readfile('config/steamaccount.json'))
                client = SteamClient(acc.get('api_key'))
                SteamClient.login(client, acc.get('steam_username'), acc.get('steam_password'),
                                  'config/steamaccount.json')
                with open('steam_session.pkl', 'wb') as f:
                    pickle.dump(client, f)
                logger.info("登录完成！已经自动缓存session.\n")
            except FileNotFoundError:
                logger.error(Fore.RED + '未检测到steamaccount.json，请添加到steamaccount.json后再进行操作！' + Fore.RESET)
                logger.info('点击任何键退出...')
                input()
                sys.exit()
            except (SSLError, ConnectTimeout, TimeoutError):
                logger.error(Fore.RED + '\n网络错误！请通过修改hosts/使用代理等方法代理Python解决问题。\n'
                                        '注意：使用游戏加速器并不能解决问题。请尝试使用Proxifier及其类似软件代理Python.exe解决。' + Fore.RESET)
                logger.info('点击任何键退出...')
                input()
                sys.exit()
    while True:
        try:
            logger.info("正在检查Steam账户登录状态...")
            if not development_mode:
                if not client.is_session_alive():
                    logger.error("Steam登录状态失效！程序退出...")
                    sys.exit()
            logger.info("Steam账户状态正常")
            logger.info("正在进行待发货/待收货饰品检查...")
            checkaccountstate()
            if development_mode and os.path.exists("dev/message_notification.json"):
                logger.info("开发者模式已开启，使用本地消息通知文件")
                to_deliver_order = json.loads(FileUtils.readfile("dev/message_notification.json")).get('data').get(
                    'to_deliver_order')
            else:
                response = requests.get("https://buff.163.com/api/message/notification", headers=headers)
                to_deliver_order = json.loads(response.text).get('data').get('to_deliver_order')
            if int(to_deliver_order.get('csgo')) != 0 or int(to_deliver_order.get('dota2')) != 0:
                logger.info("检测到" + str(
                    int(to_deliver_order.get('csgo')) + int(to_deliver_order.get('dota2'))) + "个待发货请求！")
                logger.info("CSGO待发货：" + str(int(to_deliver_order.get('csgo'))) + "个")
                logger.info("DOTA2待发货：" + str(int(to_deliver_order.get('dota2'))) + "个")
            if development_mode and os.path.exists("dev/steam_trade.json"):
                logger.info("开发者模式已开启，使用本地待发货文件")
                trade = json.loads(FileUtils.readfile("dev/steam_trade.json")).get('data')
            else:
                response = requests.get("https://buff.163.com/api/market/steam_trade", headers=headers)
                trade = json.loads(response.text).get('data')
            logger.info("查找到" + str(len(trade)) + "个待处理的交易报价请求！")
            try:
                if len(trade) != 0:
                    i = 0
                    for go in trade:
                        i += 1
                        offerid = go.get('tradeofferid')
                        logger.info("正在处理第" + str(i) + "个交易报价 报价ID" + str(offerid))
                        if offerid not in ignoredoffer:
                            try:
                                if sell_protection:
                                    logger.info("正在检查交易金额...")
                                    # 只检查第一个物品的价格, 多个物品为批量购买, 理论上批量上架的价格应该是一样的
                                    if go['tradeofferid'] not in orderinfo:
                                        if development_mode and os.path.exists("dev/sell_order_history.json"):
                                            logger.info("开发者模式已开启，使用本地数据")
                                            resp_json = json.loads(FileUtils.readfile("dev/sell_order_history.json"))
                                        else:
                                            sell_order_history_url = 'https://buff.163.com/api/market/sell_order' \
                                                                     '/history' \
                                                                     '?appid=' + str(go['appid']) + '&mode=1 '
                                            resp = requests.get(sell_order_history_url, headers=headers)
                                            resp_json = resp.json()
                                        if resp_json['code'] == 'OK':
                                            for sell_item in resp_json['data']['items']:
                                                if 'tradeofferid' in sell_item and sell_item['tradeofferid']:
                                                    orderinfo[sell_item['tradeofferid']] = sell_item
                                    if go['tradeofferid'] not in orderinfo:
                                        logger.error("无法获取交易金额，跳过此交易报价")
                                        continue
                                    price = float(orderinfo[go['tradeofferid']]['price'])
                                    goods_id = str(list(go['goods_infos'].keys())[0])
                                    if development_mode and os.path.exists("dev/shop_listing.json"):
                                        logger.info("开发者模式已开启，使用本地价格数据")
                                        resp_json = json.loads(FileUtils.readfile("dev/shop_listing.json"))
                                    else:
                                        shop_listing_url = 'https://buff.163.com/api/market/goods/sell_order?game=' + \
                                                           go['game'] + '&goods_id=' + goods_id + \
                                                           '&page_num=1&sort_by=default&mode=&allow_tradable_cooldown=1'
                                        resp = requests.get(shop_listing_url, headers=headers)
                                        resp_json = resp.json()
                                    other_lowest_price = float(resp_json['data']['items'][0]['price'])
                                    if price < other_lowest_price * protection_price_percentage and \
                                            other_lowest_price > protection_price:
                                        logger.error(Fore.RED + "交易金额过低，跳过此交易报价" + Fore.RESET)
                                        if 'protection_notification' in config:
                                            apprise_obj = apprise.Apprise()
                                            for server in config['servers']:
                                                apprise_obj.add(server)
                                            apprise_obj.notify(
                                                title=format_str(config['protection_notification']['title'], go),
                                                body=format_str(config['protection_notification']['body'], go),
                                            )
                                        continue
                                logger.info("正在接受报价...")
                                if development_mode:
                                    logger.info("开发者模式已开启，跳过接受报价")
                                else:
                                    client.accept_trade_offer(offerid)
                                ignoredoffer.append(offerid)
                                logger.info("接受完成！已经将此交易报价加入忽略名单！\n")
                                if 'sell_notification' in config:
                                    apprise_obj = apprise.Apprise()
                                    for server in config['servers']:
                                        apprise_obj.add(server)
                                    apprise_obj.notify(
                                        title=format_str(config['sell_notification']['title'], go),
                                        body=format_str(config['sell_notification']['body'], go),
                                    )
                            except Exception as e:
                                logger.error(e, exc_info=True)
                                logger.info("出现错误，稍后再试！")
                        else:
                            logger.info("该报价已经被处理过，跳过.\n")
                    logger.info("暂无BUFF报价请求.将在180秒后再次检查BUFF交易信息！\n")
                else:
                    logger.info("暂无BUFF报价请求.将在180秒后再次检查BUFF交易信息！\n")
            except KeyboardInterrupt:
                logger.info("用户停止，程序退出...")
                sys.exit()
            except Exception as e:
                logger.error(e, exc_info=True)
                logger.info("出现错误，稍后再试！")
            time.sleep(180)
        except KeyboardInterrupt:
            logger.info("用户停止，程序退出...")
            sys.exit()


if __name__ == '__main__':
    logger = logging.getLogger("Buff-Bot")
    logger.setLevel(logging.DEBUG)
    s_handler = logging.StreamHandler()
    s_handler.setLevel(logging.INFO)
    s_handler.setFormatter(logging.Formatter('[%(asctime)s] - %(filename)s - %(levelname)s: %(message)s'))
    logger.addHandler(s_handler)
    main()
