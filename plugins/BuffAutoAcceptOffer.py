import datetime
import pyjson5 as json
import sys
import time
import os

import apprise
import requests
from apprise.AppriseAsset import AppriseAsset

from utils.static import (
    APPRISE_ASSET_FOLDER,
    BUFF_ACCOUNT_DEV_FILE_PATH,
    BUFF_COOKIES_FILE_PATH,
    SELL_ORDER_HISTORY_DEV_FILE_PATH,
    SHOP_LISTING_DEV_FILE_PATH,
    STEAM_TRADE_DEV_FILE_PATH,
    SUPPORT_GAME_TYPES,
    MESSAGE_NOTIFICATION_DEV_FILE_PATH,
)
from utils.tools import get_encoding
from utils.logger import handle_caught_exception


def format_str(text: str, trade, order_info):
    for good in trade["goods_infos"]:
        good_item = trade["goods_infos"][good]
        # buff_price = float(order_info[trade['tradeofferid']]['price'])
        buff_price = 'UNKNOWN'
        created_at_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade["created_at"]))
        text = text.format(
            item_name=good_item["name"],
            steam_price=good_item["steam_price"],
            steam_price_cny=good_item["steam_price_cny"],
            buyer_name=trade["bot_name"],
            buyer_avatar=trade["bot_avatar"],
            order_time=created_at_time_str,
            game=good_item["game"],
            good_icon=good_item["original_icon_url"],
            buff_price=buff_price
        )
    return text


class BuffAutoAcceptOffer:
    buff_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
    }

    def __init__(self, logger, steam_client, config):
        self.logger = logger
        self.steam_client = steam_client
        self.config = config
        self.development_mode = self.config["development_mode"]
        self.asset = AppriseAsset(plugin_paths=[os.path.join(os.path.dirname(__file__), "..", APPRISE_ASSET_FOLDER)])
        self.lowest_on_sale_price_cache = {}

    def init(self) -> bool:
        if not os.path.exists(BUFF_COOKIES_FILE_PATH):
            with open(BUFF_COOKIES_FILE_PATH, "w", encoding="utf-8") as f:
                f.write("session=")
            return True
        return False

    def check_buff_account_state(self, dev=False):
        if dev and os.path.exists(BUFF_ACCOUNT_DEV_FILE_PATH):
            self.logger.info("[BuffAutoAcceptOffer] 开发模式, 使用本地账号")
            with open(BUFF_ACCOUNT_DEV_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
                buff_account_data = json.load(f)
            return buff_account_data["data"]["nickname"]
        else:
            response_json = requests.get("https://buff.163.com/account/api/user/info", headers=self.buff_headers).json()
            if dev:
                self.logger.info("开发者模式, 保存账户信息到本地")
                with open(BUFF_ACCOUNT_DEV_FILE_PATH, "w", encoding=get_encoding(BUFF_ACCOUNT_DEV_FILE_PATH)) as f:
                    json.dump(response_json, f, indent=4)
            if response_json["code"] == "OK":
                if "data" in response_json:
                    if "nickname" in response_json["data"]:
                        return response_json["data"]["nickname"]
            self.logger.error("[BuffAutoAcceptOffer] BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试! ")
            return ""

    def should_accept_offer(self, trade, order_info):
        sell_protection = self.config["buff_auto_accept_offer"]["sell_protection"]
        protection_price_percentage = self.config["buff_auto_accept_offer"]["protection_price_percentage"]
        protection_price = self.config["buff_auto_accept_offer"]["protection_price"]
        if sell_protection:
            self.logger.info("[BuffAutoAcceptOffer] 正在检查交易金额...")
            goods_id = str(list(trade["goods_infos"].keys())[0])
            price = float(order_info[trade["tradeofferid"]]["price"])
            other_lowest_price = -1
            if goods_id in self.lowest_on_sale_price_cache and self.lowest_on_sale_price_cache[goods_id][
                "cache_time"
            ] >= datetime.datetime.now() - datetime.timedelta(hours=1):
                other_lowest_price = self.lowest_on_sale_price_cache[goods_id]["price"]
                self.logger.info("[BuffAutoAcceptOffer] 从缓存中获取最低价格: " + str(other_lowest_price))
            if other_lowest_price == -1:
                # 只检查第一个物品的价格, 多个物品为批量购买, 理论上批量上架的价格应该是一样的
                if trade["tradeofferid"] not in order_info:
                    if self.development_mode and os.path.exists(SELL_ORDER_HISTORY_DEV_FILE_PATH):
                        self.logger.info("[BuffAutoAcceptOffer] 开发者模式已开启, 使用本地数据")
                        with open(
                            SELL_ORDER_HISTORY_DEV_FILE_PATH, "r", encoding=get_encoding(SELL_ORDER_HISTORY_DEV_FILE_PATH)
                        ) as f:
                            resp_json = json.load(f)
                    else:
                        time.sleep(5)
                        sell_order_history_url = (
                            "https://buff.163.com/api/market/sell_order" "/history" "?appid=" + str(trade["appid"]) + "&mode=1 "
                        )
                        resp = requests.get(sell_order_history_url, headers=self.buff_headers)
                        resp_json = resp.json()
                        if self.development_mode:
                            self.logger.info("[BuffAutoAcceptOffer] 开发者模式, 保存交易历史信息到本地")
                            with open(
                                SELL_ORDER_HISTORY_DEV_FILE_PATH, "w", encoding=get_encoding(SELL_ORDER_HISTORY_DEV_FILE_PATH)
                            ) as f:
                                json.dump(resp_json, f)
                    if resp_json["code"] == "OK":
                        for sell_item in resp_json["data"]["items"]:
                            if "tradeofferid" in sell_item and sell_item["tradeofferid"]:
                                order_info[sell_item["tradeofferid"]] = sell_item
                if trade["tradeofferid"] not in order_info:
                    self.logger.error("[BuffAutoAcceptOffer] 无法获取交易金额, 跳过此交易报价")
                    return False
                if self.development_mode and os.path.exists(SHOP_LISTING_DEV_FILE_PATH):
                    self.logger.info("[BuffAutoAcceptOffer] 开发者模式已开启, 使用本地价格数据")
                    with open(SHOP_LISTING_DEV_FILE_PATH, "r", encoding=get_encoding(SHOP_LISTING_DEV_FILE_PATH)) as f:
                        resp_json = json.load(f)
                else:
                    time.sleep(5)
                    shop_listing_url = (
                        "https://buff.163.com/api/market/goods/sell_order?game="
                        + trade["game"]
                        + "&goods_id="
                        + goods_id
                        + "&page_num=1&sort_by=default&mode=&allow_tradable_cooldown=1"
                    )
                    resp = requests.get(shop_listing_url, headers=self.buff_headers)
                    resp_json = resp.json()
                    if self.development_mode:
                        self.logger.info("[BuffAutoAcceptOffer] 开发者模式, 保存价格信息到本地")
                        with open(SHOP_LISTING_DEV_FILE_PATH, "w", encoding=get_encoding(SHOP_LISTING_DEV_FILE_PATH)) as f:
                            json.dump(resp_json, f)
                other_lowest_price = float(resp_json["data"]["items"][0]["price"])
                self.lowest_on_sale_price_cache[goods_id] = {"price": other_lowest_price, "cache_time": datetime.datetime.now()}
            if price < other_lowest_price * protection_price_percentage and other_lowest_price > protection_price:
                self.logger.error("[BuffAutoAcceptOffer] 交易金额过低, 跳过此交易报价")
                if "protection_notification" in self.config["buff_auto_accept_offer"]:
                    apprise_obj = apprise.Apprise(asset=self.asset)
                    for server in self.config["buff_auto_accept_offer"]["servers"]:
                        apprise_obj.add(server)
                    apprise_obj.notify(
                        title=format_str(self.config["buff_auto_accept_offer"]["protection_notification"]["title"],
                                         trade, order_info),
                        body=format_str(self.config["buff_auto_accept_offer"]["protection_notification"]["body"],
                                        trade, order_info),
                    )
                return False
        return True

    def exec(self):
        self.logger.info("[BuffAutoAcceptOffer] BUFF自动接受报价插件已启动.请稍候...")
        time.sleep(5)
        self.logger.info("[BuffAutoAcceptOffer] 正在准备登录至BUFF...")
        with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
            self.buff_headers["Cookie"] = f.read()
        self.logger.info("[BuffAutoAcceptOffer] 已检测到cookies, 尝试登录")
        user_name = self.check_buff_account_state(dev=self.development_mode)
        if not user_name:
            self.logger.error("[BuffAutoAcceptOffer] 由于登录失败,插件自动退出... ")
            sys.exit(1)
        self.logger.info("[BuffAutoAcceptOffer] 已经登录至BUFF 用户名: " + user_name)
        ignored_offer = []
        order_info = {}
        interval = self.config["buff_auto_accept_offer"]["interval"]
        while True:
            try:
                self.logger.info("[BuffAutoAcceptOffer] 正在进行BUFF待发货/待收货饰品检查...")
                username = self.check_buff_account_state()
                if username == "":
                    self.logger.error("[BuffAutoAcceptOffer] BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试! ")
                    if "buff_cookie_expired_notification" in self.config["buff_auto_accept_offer"]:
                        apprise_obj = apprise.Apprise()
                        for server in self.config["buff_auto_accept_offer"]["servers"]:
                            apprise_obj.add(server)
                        apprise_obj.notify(
                            self.config["buff_auto_accept_offer"]["buff_cookie_expired_notification"]["title"],
                            self.config["buff_auto_accept_offer"]["buff_cookie_expired_notification"]["body"],
                        )
                    return
                if self.development_mode and os.path.exists(MESSAGE_NOTIFICATION_DEV_FILE_PATH):
                    self.logger.info("[BuffAutoAcceptOffer] 开发者模式已开启, 使用本地消息通知文件")
                    with open(
                        MESSAGE_NOTIFICATION_DEV_FILE_PATH, "r", encoding=get_encoding(MESSAGE_NOTIFICATION_DEV_FILE_PATH)
                    ) as f:
                        message_notification = json.load(f)
                        to_deliver_order = message_notification["data"]["to_deliver_order"]
                else:
                    response_json = requests.get(
                        "https://buff.163.com/api/message/notification", headers=self.buff_headers
                    ).json()
                    if self.development_mode:
                        self.logger.info("[BuffAutoAcceptOffer] 开发者模式, 保存发货信息到本地")
                        with open(
                            MESSAGE_NOTIFICATION_DEV_FILE_PATH, "w", encoding=get_encoding(MESSAGE_NOTIFICATION_DEV_FILE_PATH)
                        ) as f:
                            json.dump(response_json, f)
                    to_deliver_order = response_json["data"]["to_deliver_order"]
                try:
                    if int(to_deliver_order["csgo"]) != 0 or int(to_deliver_order["dota2"]) != 0:
                        self.logger.info(
                            "[BuffAutoAcceptOffer] 检测到"
                            + str(int(to_deliver_order["csgo"]) + int(to_deliver_order["dota2"]))
                            + "个待发货请求! "
                        )
                        self.logger.info("[BuffAutoAcceptOffer] CSGO待发货: " + str(int(to_deliver_order["csgo"])) + "个")
                        self.logger.info("[BuffAutoAcceptOffer] DOTA2待发货: " + str(int(to_deliver_order["dota2"])) + "个")
                except TypeError as e:
                    handle_caught_exception(e)
                    self.logger.error("[BuffAutoAcceptOffer] Buff接口返回数据异常! 请检查网络连接或稍后再试! ")
                if self.development_mode and os.path.exists(STEAM_TRADE_DEV_FILE_PATH):
                    self.logger.info("[BuffAutoAcceptOffer] 开发者模式已开启, 使用本地待发货文件")
                    with open(STEAM_TRADE_DEV_FILE_PATH, "r", encoding=get_encoding(STEAM_TRADE_DEV_FILE_PATH)) as f:
                        trades = json.load(f)["data"]
                else:
                    response_json = requests.get(
                        "https://buff.163.com/api/market/steam_trade", headers=self.buff_headers
                    ).json()
                    if self.development_mode:
                        self.logger.info("[BuffAutoAcceptOffer] 开发者模式, 保存待发货信息到本地")
                        with open(STEAM_TRADE_DEV_FILE_PATH, "w", encoding=get_encoding(STEAM_TRADE_DEV_FILE_PATH)) as f:
                            json.dump(response_json, f)
                    trades = response_json["data"]
                trade_offer_to_confirm = []
                for game in SUPPORT_GAME_TYPES:
                    response_json = requests.get(
                        "https://buff.163.com/api/market/sell_order/to_deliver?game="
                        + game["game"]
                        + "&appid="
                        + str(game["app_id"]),
                        headers=self.buff_headers,
                    ).json()
                    trade_supply = response_json["data"]["items"]
                    for trade_offer in trade_supply:
                        trade_offer_to_confirm.append(trade_offer["tradeofferid"])
                    self.logger.info("[BuffAutoAcceptOffer] 为了避免访问接口过于频繁，休眠5秒...")
                    time.sleep(5)
                self.logger.info("[BuffAutoAcceptOffer] 查找到 " + str(len(trades)) + " 个待处理的BUFF未发货订单! ")
                self.logger.info("[BuffAutoAcceptOffer] 查找到 " + str(len(trade_offer_to_confirm)) + " 个待处理的BUFF待确认供应订单! ")
                try:
                    if len(trades) != 0:
                        i = 0
                        for trade in trades:
                            i += 1
                            offer_id = trade["tradeofferid"]
                            while offer_id in trade_offer_to_confirm:
                                trade_offer_to_confirm.remove(offer_id)
                                # offer_id会同时在2个接口中出现, 移除重复的offer_id
                            self.logger.info("[BuffAutoAcceptOffer] 正在处理第 " + str(i) + " 个交易报价 报价ID" + str(offer_id))
                            if offer_id not in ignored_offer:
                                try:
                                    if not self.should_accept_offer(trade, order_info):
                                        continue
                                    self.logger.info("[BuffAutoAcceptOffer] 正在接受报价...")
                                    if self.development_mode:
                                        self.logger.info("[BuffAutoAcceptOffer] 开发者模式已开启, 跳过接受报价")
                                    else:
                                        offer = self.steam_client.get_trade_offer(offer_id)
                                        self.steam_client.accept_trade_offer(offer_id)
                                    ignored_offer.append(offer_id)
                                    self.logger.info("[BuffAutoAcceptOffer] 接受完成! 已经将此交易报价加入忽略名单! ")
                                    if "sell_notification" in self.config["buff_auto_accept_offer"]:
                                        apprise_obj = apprise.Apprise()
                                        for server in self.config["buff_auto_accept_offer"]["servers"]:
                                            apprise_obj.add(server)
                                        apprise_obj.notify(
                                            title=format_str(
                                                self.config["buff_auto_accept_offer"]["sell_notification"]["title"],
                                                trade, order_info
                                            ),
                                            body=format_str(
                                                self.config["buff_auto_accept_offer"]["sell_notification"]["body"],
                                                trade, order_info
                                            ),
                                        )
                                    if trades.index(trade) != len(trades) - 1:
                                        self.logger.info("[BuffAutoAcceptOffer] 为了避免频繁访问Steam接口, 等待5秒后继续...")
                                        time.sleep(5)
                                except Exception as e:
                                    self.logger.error(e, exc_info=True)
                                    self.logger.info("[BuffAutoAcceptOffer] 出现错误, 稍后再试! ")
                            else:
                                self.logger.info("[BuffAutoAcceptOffer] 该报价已经被处理过, 跳过.")
                    for trade_offer_id in trade_offer_to_confirm:
                        if trade_offer_id not in ignored_offer:
                            offer = self.steam_client.get_trade_offer(trade_offer_id)
                            if offer["response"]["offer"]["trade_offer_state"] == 9:
                                self.steam_client._confirm_transaction(trade_offer_id)
                                ignored_offer.append(trade_offer_id)
                                self.logger.info("[BuffAutoAcceptOffer] 令牌完成! ( " + trade_offer_id + " ) 已经将此交易报价加入忽略名单!")
                            else:
                                self.logger.info(
                                    "[BuffAutoAcceptOffer] 令牌未完成! ( "
                                    + trade_offer_id
                                    + " ), 报价状态异常 ("
                                    + str(offer["response"]["offer"]["trade_offer_state"])
                                    + " )"
                                )
                            if trade_offer_to_confirm.index(trade_offer_id) != len(trade_offer_to_confirm) - 1:
                                self.logger.info("[BuffAutoAcceptOffer] 为了避免频繁访问Steam接口, 等待5秒后继续...")
                                time.sleep(5)
                        else:
                            self.logger.info("[BuffAutoAcceptOffer] 该报价已经被处理过, 跳过.")
                except Exception as e:
                    self.logger.error(e, exc_info=True)
                    self.logger.info("[BuffAutoAcceptOffer] 出现错误, 稍后再试! ")
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("[BuffAutoAcceptOffer] 出现未知错误, 稍后再试! ")
            self.logger.info("[BuffAutoAcceptOffer] 将在{0}秒后再次检查待发货订单信息! ".format(str(interval)))
            time.sleep(interval)
