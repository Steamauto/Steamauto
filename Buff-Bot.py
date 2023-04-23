import datetime
import logging
import os
import shutil
import sys
import json

import apprise
import uuyoupinapi
from apprise.AppriseAsset import *
from apprise.decorators import notify
from steampy.client import SteamClient
from steampy.exceptions import CaptchaRequired
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


def pause():
    if not json.loads(FileUtils.readfile('config/config.json'))['no_pause']:
        logger.info('点击回车键继续...')
        input()


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
        logger.error('BUFF账户登录状态失效，请检查cookies.txt或稍后再试！')


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
    uuyoupin = None
    protection_price = 30
    protection_price_percentage = 0.9
    asset = AppriseAsset(plugin_paths=[__file__])
    logger.info("欢迎使用Buff-Bot Github仓库:https://github.com/jiajiaxd/Buff-Bot")
    logger.info("若您觉得Buff-Bot好用，请给予Star支持，谢谢！")
    logger.info("正在检查更新...")
    try:
        response = requests.get('https://buffbot.jiajiaxd.com/latest', timeout=5)
        data = response.json()
        logger.info(f'最新版本日期：{data["date"]}\n内容：{data["message"]}\n请自行检查是否更新！')
    except requests.exceptions.Timeout:
        logger.info('检查更新超时，跳过检查更新')
    logger.info("正在初始化...")
    first_run = False
    try:
        if not os.path.exists("config/config.json"):
            first_run = True
            shutil.copy("config/config.example.json", "config/config.json")
    except FileNotFoundError:
        logger.error("未检测到config.example.json，请前往GitHub进行下载，并保证文件和程序在同一目录下。")
        pause()
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
        pause()
    config = json.loads(FileUtils.readfile("config/config.json"))
    ignoredoffer = []
    orderinfo = {}
    interval = int(config['interval'])
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
    if config['uu_token'] != "disabled":
        try:
            uuyoupin = uuyoupinapi.UUAccount(config['uu_token'])
            logger.info('悠悠有品登录完成，用户名：' + uuyoupin.get_user_nickname())
        except Exception as e:
            logger.error(
                '悠悠有品登录失败！请检查token是否正确！如果不需要使用悠悠有品，请在配置内将uu_token设置为disabled')
            pause()
            sys.exit()
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
                if json.loads(FileUtils.readfile('config/config.json'))['ignoreSSLError']:
                    logger.warning(Fore.YELLOW + "警告：已经关闭SSL验证，账号可能存在安全问题" + Fore.RESET)
                    client._session.verify = False
                    requests.packages.urllib3.disable_warnings()
                else:
                    client._session.verify = True
                if client.is_session_alive():
                    logger.info("登录成功\n")
                else:
                    relog = True
        if relog:
            try:
                logger.info("正在登录Steam...")
                acc = json.loads(FileUtils.readfile('config/steamaccount.json'))
                client = SteamClient(acc.get('api_key'))
                if json.loads(FileUtils.readfile('config/config.json'))['ignoreSSLError']:
                    logger.warning(Fore.YELLOW + "\n警告：已经关闭SSL验证，账号可能存在安全问题\n" + Fore.RESET)
                    client._session.verify = False
                    requests.packages.urllib3.disable_warnings()
                SteamClient.login(client, acc.get('steam_username'), acc.get('steam_password'),
                                  'config/steamaccount.json')
                with open('steam_session.pkl', 'wb') as f:
                    pickle.dump(client, f)
                logger.info("登录完成！已经自动缓存session.\n")
            except FileNotFoundError:
                logger.error(Fore.RED + '未检测到steamaccount.json，请添加到steamaccount.json后再进行操作！' + Fore.RESET)
                pause()
                sys.exit()
            except (ConnectTimeout, TimeoutError):
                logger.error(Fore.RED + '\n网络错误！请通过修改hosts/使用代理等方法代理Python解决问题。\n'
                                        '注意：使用游戏加速器并不能解决问题。请尝试使用Proxifier及其类似软件代理Python.exe解决。' + Fore.RESET)
                pause()
                sys.exit()
            except SSLError:
                logger.error(Fore.RED + '登录失败。SSL证书验证错误！'
                                        '若您确定网络环境安全，可尝试将config.json中的ignoreSSLError设置为true\n' + Fore.RESET)
                pause()
                sys.exit()
            except CaptchaRequired:
                logger.error(Fore.RED + '登录失败。触发Steam风控，请尝试更换加速器节点。\n' + Fore.RESET)
                pause()
                sys.exit()
    while True:
        try:
            logger.info("正在检查Steam账户登录状态...")
            if not development_mode:
                if not client.is_session_alive():
                    logger.error("Steam登录状态失效！程序退出...")
                    sys.exit()
            logger.info("Steam账户状态正常")
            logger.info("正在进行BUFF待发货/待收货饰品检查...")
            checkaccountstate()
            if development_mode and os.path.exists("dev/message_notification.json"):
                logger.info("开发者模式已开启，使用本地消息通知文件")
                to_deliver_order = json.loads(FileUtils.readfile("dev/message_notification.json")).get('data').get(
                    'to_deliver_order')
            else:
                response = requests.get("https://buff.163.com/api/message/notification", headers=headers)
                to_deliver_order = json.loads(response.text).get('data').get('to_deliver_order')
            try:
                if int(to_deliver_order.get('csgo')) != 0 or int(to_deliver_order.get('dota2')) != 0:
                    logger.info("检测到" + str(
                        int(to_deliver_order.get('csgo')) + int(to_deliver_order.get('dota2'))) + "个待发货请求！")
                    logger.info("CSGO待发货：" + str(int(to_deliver_order.get('csgo'))) + "个")
                    logger.info("DOTA2待发货：" + str(int(to_deliver_order.get('dota2'))) + "个")
            except TypeError:
                logger.error('Buff接口返回数据异常！请检查网络连接或稍后再试！')
            if development_mode and os.path.exists("dev/steam_trade.json"):
                logger.info("开发者模式已开启，使用本地待发货文件")
                trade = json.loads(FileUtils.readfile("dev/steam_trade.json")).get('data')
            else:
                response = requests.get("https://buff.163.com/api/market/steam_trade", headers=headers)
                trade = json.loads(response.text).get('data')
            logger.info("查找到" + str(len(trade)) + "个待处理的BUFF未发货订单！")
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
                                if trade.index(go) != len(trade) - 1:
                                    logger.info("为了避免频繁访问Steam接口，等待5秒后继续...")
                                    time.sleep(5)
                            except Exception as e:
                                logger.error(e, exc_info=True)
                                logger.info("出现错误，稍后再试！")
                        else:
                            logger.info("该报价已经被处理过，跳过.\n")

                if uuyoupin is not None:
                    logger.info("正在检查悠悠有品待发货信息...")
                    uu_wait_deliver_list = uuyoupin.get_wait_deliver_list()
                    len_uu_wait_deliver_list = len(uu_wait_deliver_list)
                    logger.info(str(len_uu_wait_deliver_list) + "个悠悠有品待发货订单")
                    if len(uu_wait_deliver_list) != 0:
                        for item in uu_wait_deliver_list:
                            logger.info(
                                f'正在接受悠悠有品待发货报价，商品名：{item["item_name"]}，报价ID：{item["offer_id"]}')
                            if item["offer_id"] not in ignoredoffer:
                                client.accept_trade_offer(str(item['offer_id']))
                                ignoredoffer.append(item['offer_id'])
                            logger.info("接受完成！已经将此交易报价加入忽略名单！")
                            if uu_wait_deliver_list.index(item) != len_uu_wait_deliver_list - 1:
                                logger.info("为了避免频繁访问Steam接口，等待5秒后继续...")
                                time.sleep(5)
                logger.info("将在{0}秒后再次检查待发货订单信息！\n".format(str(interval)))
            except KeyboardInterrupt:
                logger.info("用户停止，程序退出...")
                sys.exit()
            except Exception as e:
                logger.error(e, exc_info=True)
                logger.info("出现未知错误，稍后再试！")
            time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("用户停止，程序退出...")
            sys.exit()


if __name__ == '__main__':
    logger = logging.getLogger("Buff-Bot")
    logger.setLevel(logging.DEBUG)
    s_handler = logging.StreamHandler()
    s_handler.setLevel(logging.INFO)
    log_formatter = logging.Formatter('[%(asctime)s] - %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    s_handler.setFormatter(log_formatter)
    logger.addHandler(s_handler)
    if not os.path.exists('logs'):
        os.mkdir('logs')
    f_handler = logging.FileHandler(os.path.join('logs', datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S') + '.log')
                                    , encoding='utf-8')
    f_handler.setLevel(logging.DEBUG)
    f_handler.setFormatter(log_formatter)
    logger.addHandler(f_handler)
    main()
