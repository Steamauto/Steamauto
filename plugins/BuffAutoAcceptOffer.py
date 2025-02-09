import datetime
import json
import os
import pickle
import time

import apprise
import json5
import requests
from apprise import AppriseAsset

from utils.buff_helper import get_valid_session_for_buff
from utils.logger import PluginLogger, handle_caught_exception
from utils.static import (APPRISE_ASSET_FOLDER, BUFF_ACCOUNT_DEV_FILE_PATH,
                          BUFF_COOKIES_FILE_PATH,
                          MESSAGE_NOTIFICATION_DEV_FILE_PATH,
                          SELL_ORDER_HISTORY_DEV_FILE_PATH, SESSION_FOLDER,
                          SHOP_LISTING_DEV_FILE_PATH,
                          STEAM_TRADE_DEV_FILE_PATH, SUPPORT_GAME_TYPES,
                          TO_DELIVER_DEV_FILE_PATH)
from utils.tools import exit_code, get_encoding


class BuffAutoAcceptOffer:
    buff_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
    }

    def __init__(self, logger, steam_client, steam_client_mutex, config):
        self.logger = PluginLogger("BuffAutoAcceptOffer")
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        self.development_mode = self.config["development_mode"]
        self.asset = AppriseAsset(plugin_paths=[os.path.join(os.path.dirname(__file__), "..", APPRISE_ASSET_FOLDER)])
        self.lowest_on_sale_price_cache = {}
        self.order_info = {}

    def init(self) -> bool:
        if get_valid_session_for_buff(self.steam_client, self.logger) == "":
            return True
        return False

    def require_buyer_send_offer(self):
        url = "https://buff.163.com/account/api/prefer/force_buyer_send_offer"
        data = {"force_buyer_send_offer": "true"}
        resp = requests.get("https://buff.163.com/api/market/steam_trade", headers=self.buff_headers)
        csrf_token = resp.cookies.get_dict()["csrf_token"]
        headers = self.buff_headers.copy()
        headers["X-CSRFToken"] = csrf_token
        headers["Origin"] = "https://buff.163.com"
        headers["Referer"] = "https://buff.163.com/user-center/profile"
        try:
            resp = requests.post(url, headers=headers, json=data)
            if resp.status_code == 200:
                if resp.json()["code"] == "OK":
                    self.logger.info("已开启买家发起交易报价功能")
                else:
                    self.logger.error("开启买家发起交易报价功能失败")
        except:
            self.logger.error("开启买家发起交易报价功能失败")

    def get_order_info(self, trades):
        for trade in trades:
            if trade["tradeofferid"] not in self.order_info:
                if self.development_mode and os.path.exists(SELL_ORDER_HISTORY_DEV_FILE_PATH):
                    self.logger.info("开发者模式已开启, 使用本地数据")
                    with open(
                        SELL_ORDER_HISTORY_DEV_FILE_PATH,
                        "r",
                        encoding=get_encoding(SELL_ORDER_HISTORY_DEV_FILE_PATH),
                    ) as f:
                        resp_json = json.load(f)
                else:
                    time.sleep(5)
                    sell_order_history_url = (
                        "https://buff.163.com/api/market/sell_order"
                        "/history"
                        "?appid=" + str(trade["appid"]) + "&mode=1 "
                    )
                    resp = requests.get(sell_order_history_url, headers=self.buff_headers)
                    resp_json = resp.json()
                    if self.development_mode:
                        self.logger.info("开发者模式, 保存交易历史信息到本地")
                        with open(
                            SELL_ORDER_HISTORY_DEV_FILE_PATH,
                            "w",
                            encoding=get_encoding(SELL_ORDER_HISTORY_DEV_FILE_PATH),
                        ) as f:
                            f.write(json.dumps(resp_json, indent=4))
                if resp_json["code"] == "OK":
                    for sell_item in resp_json["data"]["items"]:
                        if "tradeofferid" in sell_item and sell_item["tradeofferid"]:
                            self.order_info[sell_item["tradeofferid"]] = sell_item

    def get_buff_bind_steamid(self):
        response_json = requests.get("https://buff.163.com/account/api/user/info", headers=self.buff_headers).json()
        if response_json["code"] == "OK":
            return response_json["data"]["steamid"]
        else:
            self.logger.error(response_json)
            self.logger.error("[BuffAutoOnSale] 获取BUFF绑定的SteamID失败, 请检查buff_cookies.txt或稍后再试! ")
            return ""

    def check_buff_account_state(self, dev=False):
        if dev and os.path.exists(BUFF_ACCOUNT_DEV_FILE_PATH):
            self.logger.info("开发模式, 使用本地账号")
            with open(
                BUFF_ACCOUNT_DEV_FILE_PATH,
                "r",
                encoding=get_encoding(BUFF_ACCOUNT_DEV_FILE_PATH),
            ) as f:
                buff_account_data = json5.load(f)
            return buff_account_data["data"]["nickname"]
        else:
            response_json = requests.get("https://buff.163.com/account/api/user/info", headers=self.buff_headers).json()
            if dev:
                self.logger.info("开发者模式, 保存账户信息到本地")
                with open(
                    BUFF_ACCOUNT_DEV_FILE_PATH,
                    "w",
                    encoding=get_encoding(BUFF_ACCOUNT_DEV_FILE_PATH),
                ) as f:
                    f.write(json5.dumps(response_json, indent=4))
            if response_json["code"] == "OK":
                if "data" in response_json:
                    if "nickname" in response_json["data"]:
                        steam_trade_response_json = requests.get(
                            "https://buff.163.com/api/market/steam_trade",
                            headers=self.buff_headers,
                        ).json()
                        if "data" not in steam_trade_response_json or steam_trade_response_json["data"] is None:
                            self.logger.error("BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试! ")
                            return ""
                        return response_json["data"]["nickname"]
            self.logger.error("BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试! ")
            return ""

    def format_str(self, text: str, trade):
        for good in trade["goods_infos"]:
            good_item = trade["goods_infos"][good]
            if trade["tradeofferid"] not in self.order_info:
                buff_price = "Unknown"
            else:
                buff_price = float(self.order_info[trade["tradeofferid"]]["price"])
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
                buff_price=buff_price,
                sold_count=len(trade["items_to_trade"]),
                offer_id=trade["tradeofferid"],
            )
        return text

    def should_accept_offer(self, trade):
        sell_protection = self.config["buff_auto_accept_offer"]["sell_protection"]
        protection_price_percentage = self.config["buff_auto_accept_offer"]["protection_price_percentage"]
        protection_price = self.config["buff_auto_accept_offer"]["protection_price"]
        if sell_protection:
            self.logger.info("正在检查交易金额...")
            goods_id = str(list(trade["goods_infos"].keys())[0])
            price = float(self.order_info[trade["tradeofferid"]]["price"])
            other_lowest_price = -1
            if goods_id in self.lowest_on_sale_price_cache and self.lowest_on_sale_price_cache[goods_id][
                "cache_time"
            ] >= datetime.datetime.now() - datetime.timedelta(hours=1):
                other_lowest_price = self.lowest_on_sale_price_cache[goods_id]["price"]
                self.logger.info("从缓存中获取最低价格: " + str(other_lowest_price))
            if other_lowest_price == -1:
                # 只检查第一个物品的价格, 多个物品为批量购买, 理论上批量上架的价格应该是一样的
                if trade["tradeofferid"] not in self.order_info:
                    self.logger.error("无法获取交易金额, 跳过此交易报价")
                    return False
                if self.development_mode and os.path.exists(SHOP_LISTING_DEV_FILE_PATH):
                    self.logger.info("开发者模式已开启, 使用本地价格数据")
                    with open(
                        SHOP_LISTING_DEV_FILE_PATH,
                        "r",
                        encoding=get_encoding(SHOP_LISTING_DEV_FILE_PATH),
                    ) as f:
                        resp_json = json5.load(f)
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
                        self.logger.info("开发者模式, 保存价格信息到本地")
                        with open(
                            SHOP_LISTING_DEV_FILE_PATH,
                            "w",
                            encoding=get_encoding(SHOP_LISTING_DEV_FILE_PATH),
                        ) as f:
                            f.write(json5.dumps(resp_json, indent=4))
                other_lowest_price = float(resp_json["data"]["items"][0]["price"])
                self.lowest_on_sale_price_cache[goods_id] = {
                    "price": other_lowest_price,
                    "cache_time": datetime.datetime.now(),
                }
            if price < other_lowest_price * protection_price_percentage and other_lowest_price > protection_price:
                self.logger.error("交易金额过低, 跳过此交易报价")
                if "protection_notification" in self.config["buff_auto_accept_offer"]:
                    apprise_obj = apprise.Apprise(asset=self.asset)
                    for server in self.config["buff_auto_accept_offer"]["servers"]:
                        apprise_obj.add(server)
                    if self.order_info == {}:
                        self.get_order_info([trade])
                    apprise_obj.notify(
                        title=self.format_str(
                            self.config["buff_auto_accept_offer"]["protection_notification"]["title"],
                            trade,
                        ),
                        body=self.format_str(
                            self.config["buff_auto_accept_offer"]["protection_notification"]["body"],
                            trade,
                        ),
                    )
                return False
        return True

    def exec(self):
        self.logger.info("BUFF自动接受报价插件已启动.请稍候...")
        self.logger.info("正在准备登录至BUFF...")
        with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
            self.buff_headers["Cookie"] = f.read().replace("\n", "").split(";")[0]
        self.logger.info("已检测到cookies, 尝试登录")
        user_name = self.check_buff_account_state(dev=self.development_mode)
        if not user_name:
            self.logger.error("由于登录失败,插件自动退出")
            exit_code.set(1)
            return 1
        if self.steam_client.get_steam64id_from_cookies() != self.get_buff_bind_steamid():
            self.logger.error("当前登录账号与BUFF绑定的Steam账号不一致! ")
            exit_code.set(1)
            return 1
        self.logger.info("已经登录至BUFF 用户名: " + user_name)
        self.require_buyer_send_offer()
        ignored_offer = []
        interval = self.config["buff_auto_accept_offer"]["interval"]
        while True:
            try:
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("Steam会话已过期, 正在重新登录...")
                        self.steam_client.relogin()
                        self.logger.info("Steam会话已更新")
                        steam_session_path = os.path.join(SESSION_FOLDER, self.steam_client.username.lower() + ".pkl")
                        with open(steam_session_path, "wb") as f:
                            pickle.dump(self.steam_client, f)
                self.logger.info("正在进行BUFF待发货/待收货饰品检查...")
                username = self.check_buff_account_state()
                if username == "":
                    self.logger.info("BUFF账户登录状态失效, 尝试重新登录...")
                    if get_valid_session_for_buff(self.steam_client, self.logger) == "":
                        self.logger.error("BUFF账户登录状态失效, 无法自动重新登录! ")
                        if "buff_cookie_expired_notification" in self.config["buff_auto_accept_offer"]:
                            apprise_obj = apprise.Apprise()
                            for server in self.config["buff_auto_accept_offer"]["servers"]:
                                apprise_obj.add(server)
                            apprise_obj.notify(
                                title=self.config["buff_auto_accept_offer"]["buff_cookie_expired_notification"][
                                    "title"
                                ],
                                body=self.config["buff_auto_accept_offer"]["buff_cookie_expired_notification"]["body"],
                            )
                        return
                if self.development_mode and os.path.exists(MESSAGE_NOTIFICATION_DEV_FILE_PATH):
                    self.logger.info("开发者模式已开启, 使用本地消息通知文件")
                    with open(
                        MESSAGE_NOTIFICATION_DEV_FILE_PATH,
                        "r",
                        encoding=get_encoding(MESSAGE_NOTIFICATION_DEV_FILE_PATH),
                    ) as f:
                        message_notification = json5.load(f)
                        to_deliver_order = message_notification["data"]["to_deliver_order"]
                else:
                    response_json = requests.get(
                        "https://buff.163.com/api/message/notification",
                        headers=self.buff_headers,
                    ).json()
                    if self.development_mode:
                        self.logger.info("开发者模式, 保存发货信息到本地")
                        with open(
                            MESSAGE_NOTIFICATION_DEV_FILE_PATH,
                            "w",
                            encoding=get_encoding(MESSAGE_NOTIFICATION_DEV_FILE_PATH),
                        ) as f:
                            f.write(json5.dumps(response_json, indent=4))
                    to_deliver_order = response_json["data"]["to_deliver_order"]
                try:
                    if ("csgo" in to_deliver_order and int(to_deliver_order["csgo"]) != 0) or (
                        "dota2" in to_deliver_order and int(to_deliver_order["dota2"]) != 0
                    ):
                        self.logger.info(
                            "检测到"
                            + str(
                                (0 if "csgo" not in to_deliver_order else int(to_deliver_order["csgo"]))
                                + (0 if "dota2" not in to_deliver_order else int(to_deliver_order["dota2"]))
                            )
                            + "个待发货请求! "
                        )
                        self.logger.info(
                            "CSGO待发货: "
                            + str((0 if "csgo" not in to_deliver_order else int(to_deliver_order["csgo"])))
                            + "个"
                        )
                        self.logger.info(
                            "DOTA2待发货: "
                            + str(0 if "dota2" not in to_deliver_order else int(to_deliver_order["dota2"]))
                            + "个"
                        )
                except TypeError as e:
                    handle_caught_exception(e, "BuffAutoAcceptOffer", known=True)
                    self.logger.error("Buff接口返回数据异常! 请检查网络连接或稍后再试! ")
                trade_supply = {}
                if self.development_mode and os.path.exists(STEAM_TRADE_DEV_FILE_PATH):
                    self.logger.info("开发者模式已开启, 使用本地待发货文件")
                    with open(
                        STEAM_TRADE_DEV_FILE_PATH,
                        "r",
                        encoding=get_encoding(STEAM_TRADE_DEV_FILE_PATH),
                    ) as f:
                        trades = json5.load(f)["data"]
                else:
                    response_json = requests.get(
                        "https://buff.163.com/api/market/steam_trade",
                        headers=self.buff_headers,
                    ).json()
                    if self.development_mode:
                        self.logger.info("开发者模式, 保存待发货信息到本地")
                        with open(
                            STEAM_TRADE_DEV_FILE_PATH,
                            "w",
                            encoding=get_encoding(STEAM_TRADE_DEV_FILE_PATH),
                        ) as f:
                            f.write(json5.dumps(response_json, indent=4))
                    trades = response_json["data"]
                trade_offer_to_confirm = set()
                for game in SUPPORT_GAME_TYPES:
                    if self.development_mode and os.path.exists(TO_DELIVER_DEV_FILE_PATH.format(game=game["game"])):
                        self.logger.info("开发者模式已开启, 使用本地待确认供应文件")
                        with open(
                            TO_DELIVER_DEV_FILE_PATH.format(game=game["game"]),
                            "r",
                            encoding=get_encoding(TO_DELIVER_DEV_FILE_PATH).format(game=game["game"]),
                        ) as f:
                            trade_supply[game["game"]] = json5.load(f)["data"]["items"]
                            for trade_offer in trade_supply[game["game"]]:
                                trade_offer_to_confirm.add(trade_offer["tradeofferid"])
                    else:
                        response_json = requests.get(
                            "https://buff.163.com/api/market/sell_order/to_deliver?game="
                            + game["game"]
                            + "&appid="
                            + str(game["app_id"]),
                            headers=self.buff_headers,
                        ).json()
                        if self.development_mode:
                            self.logger.info("开发者模式, 保存待确认供应信息到本地")
                            with open(
                                TO_DELIVER_DEV_FILE_PATH.format(game=game["game"]),
                                "w",
                                encoding=get_encoding(TO_DELIVER_DEV_FILE_PATH).format(game=game["game"]),
                            ) as f:
                                f.write(json5.dumps(response_json, indent=4))
                        trade_supply[game["game"]] = response_json["data"]["items"]
                        for trade_offer in trade_supply[game["game"]]:
                            if trade_offer["tradeofferid"] is not None and trade_offer["tradeofferid"] != "":
                                trade_offer_to_confirm.add(trade_offer["tradeofferid"])
                        self.logger.info("为了避免访问接口过于频繁，休眠5秒...")
                        time.sleep(5)
                self.logger.info("查找到 " + str(len(trades)) + " 个待处理的BUFF未发货订单! ")
                self.logger.info(
                    "查找到 " + str(len(trade_offer_to_confirm) - len(trades)) + " 个待处理的BUFF待确认供应订单! "
                )
                for game in trade_supply:
                    for trade in trade_supply[game]:
                        self.order_info[trade["tradeofferid"]] = trade
                try:
                    if len(trades) != 0:
                        i = 0
                        for trade in trades:
                            i += 1
                            offer_id = trade["tradeofferid"]
                            while offer_id in trade_offer_to_confirm:
                                trade_offer_to_confirm.remove(offer_id)
                                # offer_id会同时在2个接口中出现, 移除重复的offer_id
                            self.logger.info("正在处理第 " + str(i) + " 个交易报价 报价ID" + str(offer_id))
                            if offer_id not in ignored_offer:
                                try:
                                    if not self.should_accept_offer(trade):
                                        continue
                                    self.logger.info("正在检查报价物品...")
                                    if not self.development_mode:
                                        with self.steam_client_mutex:
                                            offer = self.steam_client.get_trade_offer(offer_id)
                                        for item in offer["response"]["offer"]["items_to_give"].values():
                                            match = False
                                            for item_in_trade in trade["items_to_trade"]:
                                                for property_to_compare in [
                                                    "appid",
                                                    "classid",
                                                    "contextid",
                                                    "instanceid",
                                                ]:
                                                    if str(item[property_to_compare]) != str(
                                                        item_in_trade[property_to_compare]
                                                    ):
                                                        break
                                                else:
                                                    match = True
                                                    break
                                            if not match:
                                                self.logger.error("报价中的物品不在待发货列表中, 跳过接受报价")
                                                if (
                                                    "item_mismatch_notification"
                                                    in self.config["buff_auto_accept_offer"]
                                                ):
                                                    apprise_obj = apprise.Apprise()
                                                    for server in self.config["buff_auto_accept_offer"]["servers"]:
                                                        apprise_obj.add(server)
                                                    apprise_obj.notify(
                                                        title=self.format_str(
                                                            self.config["buff_auto_accept_offer"][
                                                                "item_mismatch_notification"
                                                            ]["title"],
                                                            trade,
                                                        ),
                                                        body=self.format_str(
                                                            self.config["buff_auto_accept_offer"][
                                                                "item_mismatch_notification"
                                                            ]["body"],
                                                            trade,
                                                        ),
                                                    )
                                                if trades.index(trade) != len(trades) - 1:
                                                    self.logger.info("为了避免频繁访问Steam接口, 等待5秒后继续...")
                                                    time.sleep(5)
                                                continue
                                    else:
                                        self.logger.info("开发者模式已开启, 跳过报价物品检查")
                                    self.logger.info("报价物品检查完成! 正在接受报价...")
                                    if self.development_mode:
                                        self.logger.info("开发者模式已开启, 跳过接受报价")
                                    else:
                                        try:
                                            with self.steam_client_mutex:
                                                self.steam_client.accept_trade_offer(offer_id)
                                            ignored_offer.append(offer_id)
                                        except KeyError as e:
                                            handle_caught_exception(e, "BuffAutoAcceptOffer", known=True)
                                            self.logger.error("Steam网络异常, 暂时无法接受报价, 请稍后再试! ")
                                        except Exception as e:
                                            handle_caught_exception(e, "BuffAutoAcceptOffer", known=True)
                                            self.logger.error("无法接受报价, 请检查网络连接或稍后再试! ")
                                    
                                    self.logger.info("接受完成! 已经将此交易报价加入忽略名单! ")
                                    if "sell_notification" in self.config["buff_auto_accept_offer"]:
                                        apprise_obj = apprise.Apprise()
                                        for server in self.config["buff_auto_accept_offer"]["servers"]:
                                            apprise_obj.add(server)
                                        apprise_obj.notify(
                                            title=self.format_str(
                                                self.config["buff_auto_accept_offer"]["sell_notification"]["title"],
                                                trade,
                                            ),
                                            body=self.format_str(
                                                self.config["buff_auto_accept_offer"]["sell_notification"]["body"],
                                                trade,
                                            ),
                                        )
                                    if trades.index(trade) != len(trades) - 1:
                                        self.logger.info("为了避免频繁访问Steam接口, 等待5秒后继续...")
                                        time.sleep(5)
                                except Exception as e:
                                    self.logger.error(e, exc_info=True)
                                    self.logger.info("出现错误, 稍后再试! ")
                            else:
                                self.logger.info("该报价已经被处理过, 跳过.")
                    for trade_offer_id in trade_offer_to_confirm:
                        if trade_offer_id not in ignored_offer:
                            if self.development_mode:
                                self.logger.info("开发者模式已开启, 跳过令牌确认")
                            else:
                                with self.steam_client_mutex:
                                    offer = self.steam_client.get_trade_offer(trade_offer_id)
                                if "offer" in offer["response"] and "trade_offer_state" in offer["response"]["offer"]:
                                    if offer["response"]["offer"]["trade_offer_state"] == 9:
                                        with self.steam_client_mutex:
                                            self.steam_client._confirm_transaction(trade_offer_id)
                                        ignored_offer.append(trade_offer_id)
                                        self.logger.info(
                                            "令牌完成! ( " + trade_offer_id + " ) 已经将此交易报价加入忽略名单!"
                                        )
                                    else:
                                        self.logger.info(
                                            "令牌未完成! ( "
                                            + trade_offer_id
                                            + " ), 报价状态异常 ("
                                            + str(offer["response"]["offer"]["trade_offer_state"])
                                            + " )"
                                        )
                                else:
                                    self.logger.info(
                                        "令牌未完成! ( "
                                        + (trade_offer_id if trade_offer_id else "None")
                                        + " ), 报价返回异常 ("
                                        + str(offer["response"])
                                        + " )"
                                    )
                            if list(trade_offer_to_confirm).index(trade_offer_id) != len(trade_offer_to_confirm) - 1:
                                self.logger.info("为了避免频繁访问Steam接口, 等待5秒后继续...")
                                time.sleep(5)
                        else:
                            self.logger.info("该报价已经被处理过, 跳过.")
                except Exception as e:
                    handle_caught_exception(e, "BuffAutoAcceptOffer")
                    self.logger.info("出现错误, 稍后再试! ")
            except Exception as e:
                handle_caught_exception(e, "BuffAutoAcceptOffer")
                self.logger.info("出现未知错误, 稍后再试! ")
            self.logger.info("将在{0}秒后再次检查待发货订单信息! ".format(str(interval)))
            time.sleep(interval)
