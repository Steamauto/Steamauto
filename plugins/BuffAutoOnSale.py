import json
import sys
import time

import apprise
import requests
from apprise.AppriseAsset import AppriseAsset
from colorama import Fore

from utils.static import *


def format_str(text: str, trade):
    for good in trade['goods_infos']:
        good_item = trade['goods_infos'][good]
        created_at_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(trade['created_at']))
        text = text.format(item_name=good_item['name'], steam_price=good_item['steam_price'],
                           steam_price_cny=good_item['steam_price_cny'], buyer_name=trade['bot_name'],
                           buyer_avatar=trade['bot_avatar'], order_time=created_at_time_str, game=good_item['game'],
                           good_icon=good_item['original_icon_url'])
    return text


class BuffAutoOnSale:
    buff_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27',
    }

    def __init__(self, logger, steam_client, config):
        self.logger = logger
        self.steam_client = steam_client
        self.config = config
        self.development_mode = self.config['development_mode']
        asset = AppriseAsset(plugin_paths=[os.path.join(os.path.dirname(__file__), '..', APPRISE_ASSET_FOLDER)])
        self.session = requests.session()

    def init(self) -> bool:
        if not os.path.exists(BUFF_COOKIES_FILE_PATH):
            with open(BUFF_COOKIES_FILE_PATH, 'w', encoding='utf-8') as f:
                f.write('session=')
            return True
        return False

    def check_buff_account_state(self, dev=False):
        if dev and os.path.exists(BUFF_ACCOUNT_DEV_FILE_PATH):
            self.logger.info('[BuffAutoAcceptOffer] 开发模式, 使用本地账号')
            with open(BUFF_ACCOUNT_DEV_FILE_PATH, 'r', encoding='utf-8') as f:
                buff_account_data = json.load(f)
            return buff_account_data['data']['nickname']
        else:
            response_json = self.session.get('https://buff.163.com/account/api/user/info', headers=self.buff_headers).\
                json()
            if dev:
                self.logger.info('开发者模式, 保存账户信息到本地')
                with open(BUFF_ACCOUNT_DEV_FILE_PATH, 'w', encoding='utf-8') as f:
                    json.dump(response_json, f, indent=4)
            if response_json['code'] == 'OK':
                if 'data' in response_json:
                    if 'nickname' in response_json['data']:
                        return response_json['data']['nickname']
            self.logger.error('[BuffAutoAcceptOffer] BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试! ')

    def get_buff_inventory(self, page_num=1, page_size=60, sort_by='time.desc', state='all', force=0, force_wear=0,
                           game='csgo', app_id=730):
        url = 'https://buff.163.com/api/market/steam_inventory?page_num=' + str(page_num) + '&page_size=' + \
              str(page_size) + '&sort_by=' + sort_by + '&state=' + state + '&force=' + str(force) + \
              '&force_wear=' + str(force_wear) + '&game=' + str(game) + '&appid=' + str(app_id)
        response_json = self.session.get(url, headers=self.buff_headers).json()
        if response_json['code'] == 'OK':
            return response_json['data']
        else:
            self.logger.error(response_json)
            self.logger.error('[BuffAutoAcceptOffer] 获取BUFF库存失败, 请检查buff_cookies.txt或稍后再试! ')
            return {}

    def put_item_on_sale(self, items, price, description='', game='csgo', app_id=730):
        url = 'https://buff.163.com/api/market/sell_order/create/manual_plus'
        assets = []
        for item in items:
            assets.append({"appid": str(app_id), "assetid": item['assetid'], "classid": item['classid'],
                           "instanceid": item['instanceid'], "contextid": item['contextid'],
                           "market_hash_name": item['market_hash_name'], "price": price, "income": price,
                           "desc": description})
        data = {"appid": str(app_id), "game": game, "assets": assets}
        csrf_token = self.session.cookies.get('csrf_token')
        self.buff_headers['X-CSRFToken'] = csrf_token
        self.buff_headers['X-Requested-With'] = 'XMLHttpRequest'
        self.buff_headers['Content-Type'] = 'application/json'
        print(csrf_token)
        response_json = self.session.post(url, json=data, headers=self.buff_headers).json()
        if response_json['code'] == 'OK':
            return response_json['data']
        else:
            self.logger.error(response_json)
            self.logger.error('[BuffAutoAcceptOffer] 上架BUFF商品失败, 请检查buff_cookies.txt或稍后再试! ')
            return {}


    def exec(self):
        try:
            self.logger.info('[BuffAutoAcceptOffer] 正在准备登录至BUFF...')
            with open(BUFF_COOKIES_FILE_PATH, 'r', encoding='utf-8') as f:
                self.session.cookies['session'] = f.read().replace('session=', '').replace('\n', '')
            self.logger.info('[BuffAutoAcceptOffer] 已检测到cookies, 尝试登录')
            self.logger.info('[BuffAutoAcceptOffer] 已经登录至BUFF 用户名: ' +
                             self.check_buff_account_state(dev=self.development_mode))
        except TypeError:
            self.logger.error('[BuffAutoAcceptOffer] BUFF账户登录检查失败, 请检查buff_cookies.txt或稍后再试! ')
            sys.exit()
        inventory_json = self.get_buff_inventory(state='cansell')
        items = inventory_json['items']
        asset_0 = items[0]
        asset_0['asset_info']['market_hash_name'] = asset_0['market_hash_name']
        print(asset_0)
        self.put_item_on_sale(items=[asset_0['asset_info']], price=50)
        return
        ignored_offer = []
        order_info = {}
        sell_protection = self.config['buff_auto_accept_offer']['sell_protection']
        protection_price_percentage = self.config['buff_auto_accept_offer']['protection_price_percentage']
        protection_price = self.config['buff_auto_accept_offer']['protection_price']
        interval = self.config['buff_auto_accept_offer']['interval']
        while True:
            try:
                self.logger.info('[BuffAutoAcceptOffer] 正在检查Steam账户登录状态...')
                if not self.development_mode:
                    if not self.steam_client.is_session_alive():
                        self.logger.error('[BuffAutoAcceptOffer] Steam登录状态失效! 程序退出...')
                        sys.exit()
                self.logger.info('[BuffAutoAcceptOffer] Steam账户状态正常')
                self.logger.info('[BuffAutoAcceptOffer] 正在进行BUFF待发货/待收货饰品检查...')
                self.check_buff_account_state()
                if self.development_mode and os.path.exists(MESSAGE_NOTIFICATION_DEV_FILE_PATH):
                    self.logger.info('[BuffAutoAcceptOffer] 开发者模式已开启, 使用本地消息通知文件')
                    with open(MESSAGE_NOTIFICATION_DEV_FILE_PATH, 'r', encoding='utf-8') as f:
                        message_notification = json.load(f)
                        to_deliver_order = message_notification['data']['to_deliver_order']
                else:
                    response_json = requests.get('https://buff.163.com/api/message/notification',
                                                 headers=self.buff_headers).json()
                    if self.development_mode:
                        self.logger.info('[BuffAutoAcceptOffer] 开发者模式, 保存发货信息到本地')
                        with open(MESSAGE_NOTIFICATION_DEV_FILE_PATH, 'w', encoding='utf-8') as f:
                            json.dump(response_json, f)
                    to_deliver_order = response_json['data']['to_deliver_order']
                try:
                    if int(to_deliver_order['csgo']) != 0 or int(to_deliver_order['dota2']) != 0:
                        self.logger.info('[BuffAutoAcceptOffer] 检测到' + str(
                            int(to_deliver_order['csgo']) + int(to_deliver_order['dota2'])) + '个待发货请求! ')
                        self.logger.info('[BuffAutoAcceptOffer] CSGO待发货: ' + str(int(to_deliver_order['csgo'])) + '个')
                        self.logger.info('[BuffAutoAcceptOffer] DOTA2待发货: ' + str(int(to_deliver_order['dota2'])) + '个')
                except TypeError:
                    self.logger.error('[BuffAutoAcceptOffer] Buff接口返回数据异常! 请检查网络连接或稍后再试! ')
                if self.development_mode and os.path.exists(STEAM_TRADE_DEV_FILE_PATH):
                    self.logger.info('[BuffAutoAcceptOffer] 开发者模式已开启, 使用本地待发货文件')
                    with open(STEAM_TRADE_DEV_FILE_PATH, 'r', encoding='utf-8') as f:
                        trade = json.load(f)['data']
                else:
                    response_json = requests.get('https://buff.163.com/api/market/steam_trade', headers=self.buff_headers)
                    if self.development_mode:
                        self.logger.info('[BuffAutoAcceptOffer] 开发者模式, 保存待发货信息到本地')
                        with open(STEAM_TRADE_DEV_FILE_PATH, 'w', encoding='utf-8') as f:
                            json.dump(response_json.json(), f)
                    trade = json.loads(response_json.text)['data']
                self.logger.info('[BuffAutoAcceptOffer] 查找到' + str(len(trade)) + '个待处理的BUFF未发货订单! ')
                try:
                    if len(trade) != 0:
                        i = 0
                        for go in trade:
                            i += 1
                            offer_id = go['tradeofferid']
                            self.logger.info('[BuffAutoAcceptOffer] 正在处理第' + str(i) + '个交易报价 报价ID' + str(offer_id))
                            if offer_id not in ignored_offer:
                                try:
                                    if sell_protection:
                                        self.logger.info('[BuffAutoAcceptOffer] 正在检查交易金额...')
                                        # 只检查第一个物品的价格, 多个物品为批量购买, 理论上批量上架的价格应该是一样的
                                        if go['tradeofferid'] not in order_info:
                                            if self.development_mode and os.path.exists(SELL_ORDER_HISTORY_DEV_FILE_PATH):
                                                self.logger.info('[BuffAutoAcceptOffer] 开发者模式已开启, 使用本地数据')
                                                with open(SELL_ORDER_HISTORY_DEV_FILE_PATH, 'r', encoding='utf-8') as f:
                                                    resp_json = json.load(f)
                                            else:
                                                sell_order_history_url = 'https://buff.163.com/api/market/sell_order' \
                                                                         '/history' \
                                                                         '?appid=' + str(go['appid']) + '&mode=1 '
                                                resp = requests.get(sell_order_history_url, headers=self.buff_headers)
                                                resp_json = resp.json()
                                                if self.development_mode:
                                                    self.logger.info('[BuffAutoAcceptOffer] 开发者模式, 保存交易历史信息到本地')
                                                    with open(SELL_ORDER_HISTORY_DEV_FILE_PATH, 'w', encoding='utf-8') as f:
                                                        json.dump(resp_json, f)
                                            if resp_json['code'] == 'OK':
                                                for sell_item in resp_json['data']['items']:
                                                    if 'tradeofferid' in sell_item and sell_item['tradeofferid']:
                                                        order_info[sell_item['tradeofferid']] = sell_item
                                        if go['tradeofferid'] not in order_info:
                                            self.logger.error('[BuffAutoAcceptOffer] 无法获取交易金额, 跳过此交易报价')
                                            continue
                                        price = float(order_info[go['tradeofferid']]['price'])
                                        goods_id = str(list(go['goods_infos'].keys())[0])
                                        if self.development_mode and os.path.exists(SHOP_LISTING_DEV_FILE_PATH):
                                            self.logger.info('[BuffAutoAcceptOffer] 开发者模式已开启, 使用本地价格数据')
                                            with open(SHOP_LISTING_DEV_FILE_PATH, 'r', encoding='utf-8') as f:
                                                resp_json = json.load(f)
                                        else:
                                            shop_listing_url = 'https://buff.163.com/api/market/goods/sell_order?game=' + \
                                                               go['game'] + '&goods_id=' + goods_id + \
                                                               '&page_num=1&sort_by=default&mode=&allow_tradable_cooldown=1'
                                            resp = requests.get(shop_listing_url, headers=self.buff_headers)
                                            resp_json = resp.json()
                                            if self.development_mode:
                                                self.logger.info('[BuffAutoAcceptOffer] 开发者模式, 保存价格信息到本地')
                                                with open(SHOP_LISTING_DEV_FILE_PATH, 'w', encoding='utf-8') as f:
                                                    json.dump(resp_json, f)
                                        other_lowest_price = float(resp_json['data']['items'][0]['price'])
                                        if price < other_lowest_price * protection_price_percentage and \
                                                other_lowest_price > protection_price:
                                            self.logger.error(Fore.RED + '[BuffAutoAcceptOffer] 交易金额过低, 跳过此交易报价' +
                                                              Fore.RESET)
                                            if 'protection_notification' in self.config['buff_auto_accept_offer']:
                                                apprise_obj = apprise.Apprise()
                                                for server in self.config['buff_auto_accept_offer']['servers']:
                                                    apprise_obj.add(server)
                                                apprise_obj.notify(
                                                    title=format_str(self.config['buff_auto_accept_offer']
                                                                     ['protection_notification']['title'], go),
                                                    body=format_str(self.config['buff_auto_accept_offer']
                                                                    ['protection_notification']['body'], go),
                                                )
                                            continue
                                    self.logger.info('[BuffAutoAcceptOffer] 正在接受报价...')
                                    if self.development_mode:
                                        self.logger.info('[BuffAutoAcceptOffer] 开发者模式已开启, 跳过接受报价')
                                    else:
                                        self.steam_client.accept_trade_offer(offer_id)
                                    ignored_offer.append(offer_id)
                                    self.logger.info('[BuffAutoAcceptOffer] 接受完成! 已经将此交易报价加入忽略名单! \n')
                                    if 'sell_notification' in self.config['buff_auto_accept_offer']:
                                        apprise_obj = apprise.Apprise()
                                        for server in self.config['buff_auto_accept_offer']['servers']:
                                            apprise_obj.add(server)
                                        apprise_obj.notify(
                                            title=format_str(self.config['buff_auto_accept_offer']['sell_notification']['title'], go),
                                            body=format_str(self.config['buff_auto_accept_offer']['sell_notification']['body'], go),
                                        )
                                    if trade.index(go) != len(trade) - 1:
                                        self.logger.info('[BuffAutoAcceptOffer] 为了避免频繁访问Steam接口, 等待5秒后继续...')
                                        time.sleep(5)
                                except Exception as e:
                                    self.logger.error(e, exc_info=True)
                                    self.logger.info('[BuffAutoAcceptOffer] 出现错误, 稍后再试! ')
                            else:
                                self.logger.info('[BuffAutoAcceptOffer] 该报价已经被处理过, 跳过.\n')
                except Exception as e:
                    self.logger.error(e, exc_info=True)
                    self.logger.info('[BuffAutoAcceptOffer] 出现错误, 稍后再试! ')
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info('[BuffAutoAcceptOffer] 出现未知错误, 稍后再试! ')
            self.logger.info('[BuffAutoAcceptOffer] 将在{0}秒后再次检查待发货订单信息! \n'.format(str(interval)))
            time.sleep(interval)