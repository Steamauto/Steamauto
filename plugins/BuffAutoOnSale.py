import datetime
import os
import pickle
import random
import time

import apprise
import json5
import requests
from apprise import AppriseAsset
from bs4 import BeautifulSoup

from utils.BuffApiCrypt import BuffApiCrypt
from utils.buff_helper import get_valid_session_for_buff
from utils.logger import handle_caught_exception
from utils.static import BUFF_COOKIES_FILE_PATH, SESSION_FOLDER, SUPPORT_GAME_TYPES
from utils.tools import get_encoding


def format_str(text: str, trade):
    for good in trade["goods_infos"]:
        good_item = trade["goods_infos"][good]
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
        )
    return text


def merge_buy_orders(response_data: dict):
    orders = response_data["items"]
    user_info = response_data["user_infos"]
    for order in orders:
        order["user"] = user_info[order["user_id"]]
        del order["user_id"]
        pay_method = order["pay_method"]
        if pay_method == 43:
            order["supported_pay_method"] = ["支付宝", "微信"]
        elif pay_method == 3:
            order["supported_pay_method"] = ["支付宝"]
        elif pay_method == 1:
            order["supported_pay_method"] = ["微信"]
        else:
            order["supported_pay_method"] = []
    return orders


class BuffAutoOnSale:
    buff_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
    }

    def __init__(self, logger, steam_client, steam_client_mutex, config):
        self.logger = logger
        self.steam_client = steam_client
        self.config = config
        self.steam_client_mutex = steam_client_mutex
        self.asset = AppriseAsset()
        self.session = requests.session()
        self.lowest_price_cache = {}
        self.unfinish_supply_order_list = []  # 等待buff发起报价, 之后进行确认报价的订单, [{order_id, create_time}]

    def init(self) -> bool:
        if get_valid_session_for_buff(self.steam_client, self.logger) == "":
            return True
        return False

    def check_buff_account_state(self):
        response_json = self.session.get("https://buff.163.com/account/api/user/info", headers=self.buff_headers).json()
        if response_json["code"] == "OK":
            if "data" in response_json:
                if "nickname" in response_json["data"]:
                    return response_json["data"]["nickname"]
        self.logger.error("[BuffAutoOnSale] BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试! ")

    def get_buff_inventory(self, page_num=1, page_size=60, sort_by="time.desc", state="all", force=0, force_wear=0, game="csgo", app_id=730):
        url = "https://buff.163.com/api/market/steam_inventory"
        params = {"page_num": page_num, "page_size": page_size, "sort_by": sort_by, "state": state, "force": force, "force_wear": force_wear, "game": game, "appid": app_id}
        response_json = self.session.get(url, headers=self.buff_headers, params=params).json()
        if response_json["code"] == "OK":
            return response_json["data"]
        else:
            self.logger.error(response_json)
            self.logger.error("[BuffAutoOnSale] 获取BUFF库存失败, 请检查buff_cookies.txt或稍后再试! ")
            return {}

    def put_item_on_sale(self, items, price, description="", game="csgo", app_id=730, use_range_price=False):
        if game != "csgo" and use_range_price:
            self.logger.warning("[BuffAutoOnSale] 仅支持CSGO使用磨损区间最低价上架, 已自动关闭磨损区间最低价上架")
            use_range_price = False
        wear_ranges = [
            {"min": 0, "max": 0.01},
            {"min": 0.01, "max": 0.02},
            {"min": 0.02, "max": 0.03},
            {"min": 0.03, "max": 0.04},
            {"min": 0.04, "max": 0.07},
            {"min": 0.07, "max": 0.08},
            {"min": 0.08, "max": 0.09},
            {"min": 0.09, "max": 0.10},
            {"min": 0.10, "max": 0.11},
            {"min": 0.11, "max": 0.15},
            {"min": 0.15, "max": 0.18},
            {"min": 0.18, "max": 0.21},
            {"min": 0.21, "max": 0.24},
            {"min": 0.24, "max": 0.27},
            {"min": 0.27, "max": 0.38},
            {"min": 0.38, "max": 0.39},
            {"min": 0.39, "max": 0.40},
            {"min": 0.40, "max": 0.41},
            {"min": 0.41, "max": 0.42},
            {"min": 0.42, "max": 0.45},
            {"min": 0.45, "max": 0.50},
            {"min": 0.50, "max": 0.63},
            {"min": 0.63, "max": 0.76},
            {"min": 0.76, "max": 0.9},
            {"min": 0.9, "max": 1},
        ]
        sleep_seconds_to_prevent_buff_ban = 10
        if "sleep_seconds_to_prevent_buff_ban" in self.config["buff_auto_on_sale"]:
            sleep_seconds_to_prevent_buff_ban = self.config["buff_auto_on_sale"]["sleep_seconds_to_prevent_buff_ban"]
        supply_buy_orders = False
        only_auto_accept = True
        supported_payment_method = ["支付宝"]
        min_price = 0
        if "buy_order" in self.config["buff_auto_on_sale"] and self.config["buff_auto_on_sale"]["buy_order"]["enable"]:
            supply_buy_orders = True
            if "only_auto_accept" in self.config["buff_auto_on_sale"]["buy_order"]:
                only_auto_accept = self.config["buff_auto_on_sale"]["buy_order"]["only_auto_accept"]
            if "supported_payment_method" in self.config["buff_auto_on_sale"]["buy_order"]:
                supported_payment_method = self.config["buff_auto_on_sale"]["buy_order"]["supported_payment_method"]
            if "min_price" in self.config["buff_auto_on_sale"]["buy_order"]:
                min_price = self.config["buff_auto_on_sale"]["buy_order"]["min_price"]
        url = "https://buff.163.com/api/market/sell_order/create/manual_plus"
        assets = []
        for item in items:
            has_requested_refresh = False
            refresh_count = 0
            self.logger.info("[BuffAutoOnSale] 正在解析 " + item["market_hash_name"])
            min_paint_wear = 0
            max_paint_wear = 1.0
            paint_wear = -1
            if use_range_price:
                done = False
                while not done:
                    has_wear = False
                    wear_keywords = ["(Factory New)", "(Minimal Wear)", "(Field-Tested)", "(Well-Worn)", "(Battle-Scarred)"]
                    for wear_keyword in wear_keywords:
                        if wear_keyword in item["market_hash_name"]:
                            has_wear = True
                            break
                    if not has_wear:
                        self.logger.info("[BuffAutoOnSale] 商品无磨损, 使用同类型最低价上架")
                        done = True
                        break
                    self.logger.info("[BuffAutoOnSale] 正在获取磨损区间...")
                    self.logger.info("[BuffAutoOnSale] 为了避免被封IP, 休眠" + str(sleep_seconds_to_prevent_buff_ban) + "秒")
                    time.sleep(sleep_seconds_to_prevent_buff_ban)
                    asset = {
                        "assetid": item["assetid"],
                        "classid": item["classid"],
                        "instanceid": item["instanceid"],
                        "contextid": item["contextid"],
                        "market_hash_name": item["market_hash_name"],
                        "price": "",
                        "income": "",
                        "has_market_min_price": False,
                        "game": game,
                        "goods_id": item["goods_id"],
                    }
                    data = {"game": game, "assets": [asset]}
                    self.session.get("https://buff.163.com/api/market/steam_trade", headers=self.buff_headers)
                    csrf_token = self.session.cookies.get("csrf_token", domain="buff.163.com")
                    headers = {
                        "User-Agent": self.buff_headers["User-Agent"],
                        "X-CSRFToken": csrf_token,
                        "X-Requested-With": "XMLHttpRequest",
                        "Content-Type": "application/json",
                        "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
                    }
                    preview_url = "https://buff.163.com/market/sell_order/preview/manual_plus"
                    response_json = self.session.post(preview_url, json=data, headers=headers).json()
                    if "data" not in response_json:
                        self.logger.error(response_json)
                        self.logger.error("[BuffAutoOnSale] 获取磨损区间失败, 使用同类型最低价上架")
                        done = True
                        break
                    response_data = response_json["data"]
                    bs = BeautifulSoup(response_data, "html.parser")
                    paint_wear_p = bs.find("p", {"class": "paint-wear"})
                    bs_span = bs.find("span", {"class": "custom-currency"})
                    try:
                        suggested_price = int(bs.find("span", {"class": "custom-currency"}).attrs.get("data-price"))
                    except Exception:
                        suggested_price = -1
                    if suggested_price != -1 and suggested_price < 10:
                        self.logger.info("[BuffAutoOnSale] 商品价格低于10, 使用同类型最低价上架")
                        done = True
                        break
                    if paint_wear_p is not None:
                        paint_wear = paint_wear_p.text.replace("磨损:", "").replace(" ", "").replace("\n", "")
                        paint_wear = float(paint_wear)
                        for wear_range in wear_ranges:
                            if wear_range["min"] <= paint_wear < wear_range["max"]:
                                min_paint_wear = wear_range["min"]
                                max_paint_wear = wear_range["max"]
                                done = True
                                break
                        if not done:
                            self.logger.error("[BuffAutoOnSale] 代码出现错误, 无法解析磨损: " + paint_wear)
                            self.logger.error("[BuffAutoOnSale] 使用同类型最低价上架")
                            done = True
                            break
                    else:
                        if not has_requested_refresh:
                            has_requested_refresh = True
                            self.logger.info("[BuffAutoOnSale] 商品未解析过, 开始请求解析...")
                            self.logger.info("[BuffAutoOnSale] 为了避免被封IP, 休眠" + str(sleep_seconds_to_prevent_buff_ban) + "秒")
                            time.sleep(sleep_seconds_to_prevent_buff_ban)
                            post_url = "https://buff.163.com/api/market/csgo_asset/change_state_cs2"
                            data = {"assetid": item["assetid"], "contextid": item["contextid"]}
                            self.session.get("https://buff.163.com/api/market/steam_trade", headers=self.buff_headers)
                            csrf_token = self.session.cookies.get("csrf_token", domain="buff.163.com")
                            headers = {
                                "User-Agent": self.buff_headers["User-Agent"],
                                "X-CSRFToken": csrf_token,
                                "X-Requested-With": "XMLHttpRequest",
                                "Content-Type": "application/json",
                                "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
                            }
                            response_json = self.session.post(post_url, json=data, headers=headers).json()
                            if response_json["code"] == "OK":
                                self.logger.info("[BuffAutoOnSale] 成功请求解析")
                                continue
                            else:
                                self.logger.error(response_json)
                                self.logger.error("[BuffAutoOnSale] 请求解析失败, 使用同类型最低价上架")
                                done = True
                        else:
                            refresh_count += 1
                            if refresh_count >= 5:
                                self.logger.error("[BuffAutoOnSale] 商品解析失败, 使用同类型最低价上架")
                                done = True
                                break
                            self.logger.error("[BuffAutoOnSale] 商品尚未解析完成...")
                            continue
                    self.logger.info("[BuffAutoOnSale] 使用磨损区间最低价上架, 磨损区间: " + str(min_paint_wear) + " - " + str(max_paint_wear))
            sell_price = price
            if sell_price == -1:
                sell_price = self.get_lowest_sell_price(item["goods_id"], game, app_id, min_paint_wear, max_paint_wear)
            if supply_buy_orders:
                highest_buy_order = self.get_highest_buy_order(
                    item["goods_id"], game, app_id, paint_wear=paint_wear, require_auto_accept=only_auto_accept, supported_payment_methods=supported_payment_method
                )
                if sell_price <= min_price or sell_price <= float(highest_buy_order.get("price", -1)):
                    # 直接供应给最高报价
                    self.logger.info("[BuffAutoOnSale] 商品 " + item["market_hash_name"] + " 将供应给最高报价 " + str(highest_buy_order["price"]))
                    success = self.supply_item_to_buy_order(item, highest_buy_order, game, app_id)
                    if success:
                        if "on_sale_notification" in self.config["buff_auto_on_sale"]:
                            item_list = item["market_hash_name"] + " : " + highest_buy_order["price"] + "\n"
                            apprise_obj = apprise.Apprise(asset=self.asset)
                            for server in self.config["buff_auto_on_sale"]["servers"]:
                                apprise_obj.add(server)
                            apprise_obj.notify(
                                title=self.config["buff_auto_on_sale"]["on_sale_notification"]["title"].format(game=game, sold_count=len(assets)),
                                body=self.config["buff_auto_on_sale"]["on_sale_notification"]["body"].format(game=game, sold_count=len(assets), item_list=item_list),
                            )
                        continue
            if sell_price != -1:
                sell_price = sell_price - 0.01
                if sell_price < 0.02:
                    sell_price = 0.02
                self.logger.info("[BuffAutoOnSale] 商品 " + item["market_hash_name"] + " 将使用价格 " + str(sell_price) + " 进行上架")
                assets.append(
                    {
                        "appid": str(app_id),
                        "assetid": item["assetid"],
                        "classid": item["classid"],
                        "instanceid": item["instanceid"],
                        "contextid": item["contextid"],
                        "market_hash_name": item["market_hash_name"],
                        "price": sell_price,
                        "income": sell_price,
                        "desc": description,
                    }
                )
        if len(assets) == 0:
            return {}
        data = {"appid": str(app_id), "game": game, "assets": assets}
        self.session.get("https://buff.163.com/api/market/steam_trade", headers=self.buff_headers)
        csrf_token = self.session.cookies.get("csrf_token", domain="buff.163.com")
        headers = {
            "User-Agent": self.buff_headers["User-Agent"],
            "X-CSRFToken": csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json",
            "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
        }
        response_json = self.session.post(url, json=data, headers=headers).json()
        if response_json["code"] == "OK":
            if "on_sale_notification" in self.config["buff_auto_on_sale"]:
                item_list = ""
                for asset in assets:
                    item_list += asset["market_hash_name"] + " : " + str(asset["price"]) + "\n"
                apprise_obj = apprise.Apprise(asset=self.asset)
                for server in self.config["buff_auto_on_sale"]["servers"]:
                    apprise_obj.add(server)
                apprise_obj.notify(
                    title=self.config["buff_auto_on_sale"]["on_sale_notification"]["title"].format(game=game, sold_count=len(assets)),
                    body=self.config["buff_auto_on_sale"]["on_sale_notification"]["body"].format(game=game, sold_count=len(assets), item_list=item_list),
                )
            return response_json["data"]
        else:
            self.logger.error(response_json)
            self.logger.error("[BuffAutoOnSale] 上架BUFF商品失败, 请检查buff_cookies.txt或稍后再试! ")
            return {}

    def get_highest_buy_order(self, goods_id, game="csgo", app_id=730, paint_wear=-1, require_auto_accept=True, supported_payment_methods=None):
        sleep_seconds_to_prevent_buff_ban = 10
        if "sleep_seconds_to_prevent_buff_ban" in self.config["buff_auto_on_sale"]:
            sleep_seconds_to_prevent_buff_ban = self.config["buff_auto_on_sale"]["sleep_seconds_to_prevent_buff_ban"]
        if supported_payment_methods is None:
            supported_payment_methods = ["支付宝", "微信"]
        url = "https://buff.163.com/api/market/goods/buy_order?goods_id=" + str(goods_id) + "&page_num=1&page_size=20&same_goods=false&game=" + game + "&appid=" + str(app_id)
        self.logger.info("[BuffAutoOnSale] 为了避免被封IP, 休眠" + str(sleep_seconds_to_prevent_buff_ban) + "秒")
        time.sleep(sleep_seconds_to_prevent_buff_ban)
        self.logger.info("[BuffAutoOnSale] 正在获取BUFF商品最高求购")
        response = self.session.get(url, headers=self.buff_headers).json()
        if response["code"] != "OK":
            return {}
        buy_orders = merge_buy_orders(response["data"])
        if len(buy_orders) == 0:
            return {}
        for order in buy_orders:
            if require_auto_accept and not order["user"]["is_auto_accept"]:
                continue
            payment_method_supported = False
            for supported_payment_method in supported_payment_methods:
                if supported_payment_method in order["supported_pay_method"]:
                    payment_method_supported = True
                    break
            if not payment_method_supported:
                continue
            if len(order["specific"]) != 0:
                match_specific = True
                for specific in order["specific"]:
                    if specific["type"] == "paintwear":
                        if paint_wear == -1:
                            match_specific = False
                            break
                        min_paint_wear = specific["values"][0]
                        max_paint_wear = specific["values"][1]
                        if not (min_paint_wear <= paint_wear < max_paint_wear):
                            match_specific = False
                            break
                    if specific["type"] == "unlock_style":  # 对模板有要求, 不进行判断直接当不符合
                        match_specific = False
                        break
                if not match_specific:
                    continue
            return order
        return {}

    def get_lowest_sell_price(self, goods_id, game="csgo", app_id=730, min_paint_wear=0, max_paint_wear=1.0):
        sleep_seconds_to_prevent_buff_ban = 10
        if "sleep_seconds_to_prevent_buff_ban" in self.config["buff_auto_on_sale"]:
            sleep_seconds_to_prevent_buff_ban = self.config["buff_auto_on_sale"]["sleep_seconds_to_prevent_buff_ban"]
        goods_key = str(goods_id) + "," + str(min_paint_wear) + "," + str(max_paint_wear)
        if goods_key in self.lowest_price_cache:
            if self.lowest_price_cache[goods_key]["cache_time"] >= datetime.datetime.now() - datetime.timedelta(hours=1):
                lowest_price = self.lowest_price_cache[goods_key]["lowest_price"]
                return lowest_price
        self.logger.info("[BuffAutoOnSale] 获取BUFF商品最低价")
        self.logger.info("[BuffAutoOnSale] 为了避免被封IP, 休眠" + str(sleep_seconds_to_prevent_buff_ban) + "秒")
        time.sleep(sleep_seconds_to_prevent_buff_ban)
        url = (
            "https://buff.163.com/api/market/goods/sell_order?goods_id="
            + str(goods_id)
            + "&page_num=1&page_size=24&allow_tradable_cooldown=1&sort_by=default&game="
            + game
            + "&appid="
            + str(app_id)
            + "&min_paintwear="
            + str(min_paint_wear)
            + "&max_paintwear="
            + str(max_paint_wear)
        )
        if min_paint_wear == 0 and max_paint_wear == 1.0:
            url = (
                "https://buff.163.com/api/market/goods/sell_order?goods_id="
                + str(goods_id)
                + "&page_num=1&page_size=24&allow_tradable_cooldown=1&sort_by=default&game="
                + game
                + "&appid="
                + str(app_id)
            )
        response_json = self.session.get(url, headers=self.buff_headers).json()
        if response_json["code"] == "OK":
            if len(response_json["data"]["items"]) == 0:  # 无商品
                if min_paint_wear != 0 or max_paint_wear != 1.0:
                    self.logger.info("[BuffAutoOnSale] 无商品, 重试使用同类型最低价上架")
                    return self.get_lowest_sell_price(goods_id, game, app_id, 0, 1.0)
                else:
                    self.logger.info("[BuffAutoOnSale] 无商品")
                    return -1
            lowest_price = float(response_json["data"]["items"][0]["price"])
            self.lowest_price_cache[goods_key] = {"lowest_price": lowest_price, "cache_time": datetime.datetime.now()}
            return lowest_price
        else:
            if response_json["code"] == "Captcha Validate Required":
                captcha_url = response_json["confirm_entry"]["entry"]["url"]
                session = self.session.cookies.get("session", domain="buff.163.com")
                self.logger.error("[BuffAutoOnSale] 需要验证码, 请使用session " + session + " 打开以下链接, 并完成验证")
                self.logger.error("[BuffAutoOnSale] " + captcha_url)
                if "captcha_notification" in self.config["buff_auto_on_sale"]:
                    apprise_obj = apprise.Apprise(asset=self.asset)
                    for server in self.config["buff_auto_on_sale"]["servers"]:
                        apprise_obj.add(server)
                    apprise_obj.notify(
                        title=self.config["buff_auto_on_sale"]["captcha_notification"]["title"],
                        body=self.config["buff_auto_on_sale"]["captcha_notification"]["body"].format(captcha_url=captcha_url, session=session),
                    )
                return -1
            # 访问频率过高，报错处理
            elif response_json["code"] == "System Error":
                self.logger.error(response_json["error"])
                time.sleep(5)
                return -1
            else:
                self.logger.error(response_json)
                self.logger.error("[BuffAutoOnSale] 获取BUFF商品最低价失败, 请检查buff_cookies.txt或稍后再试! ")
                return -1

    def exec(self):
        self.logger.info("[BuffAutoOnSale] BUFF自动上架插件已启动, 休眠30秒, 与自动接收报价插件错开运行时间")
        # time.sleep(30)
        try:
            self.logger.info("[BuffAutoOnSale] 正在准备登录至BUFF...")
            with open(BUFF_COOKIES_FILE_PATH.format(steam_username=self.steam_client.username), "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH.format(steam_username=self.steam_client.username))) as f:
                self.session.cookies["session"] = f.read().replace("session=", "").replace("\n", "").split(";")[0]
            self.logger.info("[BuffAutoOnSale] 已检测到cookies, 尝试登录")
            self.logger.info("[BuffAutoOnSale] 已经登录至BUFF 用户名: " + self.check_buff_account_state())
        except TypeError as e:
            handle_caught_exception(e, known=True)
            self.logger.error("[BuffAutoOnSale] BUFF账户登录检查失败, 请检查buff_cookies.txt或稍后再试! ")
            return
        sleep_interval = int(self.config["buff_auto_on_sale"]["interval"])
        black_list_time = []
        if "blacklist_time" in self.config["buff_auto_on_sale"]:
            black_list_time = self.config["buff_auto_on_sale"]["blacklist_time"]
        white_list_time = []
        if "whitelist_time" in self.config["buff_auto_on_sale"]:
            white_list_time = self.config["buff_auto_on_sale"]["whitelist_time"]
        random_chance = 100
        if "random_chance" in self.config["buff_auto_on_sale"]:
            random_chance = self.config["buff_auto_on_sale"]["random_chance"] * 100
        force_refresh = 0
        if "force_refresh" in self.config["buff_auto_on_sale"] and self.config["buff_auto_on_sale"]["force_refresh"]:
            force_refresh = 1
        description = ""
        if "description" in self.config["buff_auto_on_sale"]:
            description = self.config["buff_auto_on_sale"]["description"]
        use_range_price = False
        if "use_range_price" in self.config["buff_auto_on_sale"]:
            use_range_price = self.config["buff_auto_on_sale"]["use_range_price"]
        while True:
            try:
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("[BuffAutoOnSale] Steam会话已过期, 正在重新登录...")
                        self.steam_client._session.cookies.clear()
                        self.steam_client.login(self.steam_client.username, self.steam_client._password, json5.dumps(self.steam_client.steam_guard))
                        self.logger.info("[BuffAutoOnSale] Steam会话已更新")
                        steam_session_path = os.path.join(SESSION_FOLDER, self.steam_client.username.lower() + ".pkl")
                        with open(steam_session_path, "wb") as f:
                            pickle.dump(self.steam_client.session, f)
                now = datetime.datetime.now()
                if now.hour in black_list_time:
                    self.logger.info("[BuffAutoOnSale] 现在时间在黑名单时间内, 休眠" + str(sleep_interval) + "秒")
                    time.sleep(sleep_interval)
                    continue
                if len(white_list_time) != 0 and now.hour not in white_list_time:
                    self.logger.info("[BuffAutoOnSale] 现在时间不在白名单时间内, 休眠" + str(sleep_interval) + "秒")
                    time.sleep(sleep_interval)
                    continue
                if random.randint(1, 100) > random_chance:
                    self.logger.info("[BuffAutoOnSale] 未命中随机概率, 休眠" + str(sleep_interval) + "秒")
                    time.sleep(sleep_interval)
                    continue
            except Exception as e:
                handle_caught_exception(e, "[BuffAutoOnSale]", known=True)
                self.logger.info("[BuffAutoOnSale] 休眠" + str(sleep_interval) + "秒")
                time.sleep(sleep_interval)
                continue
            try:
                while True:
                    items_count_this_loop = 0
                    for game in SUPPORT_GAME_TYPES:
                        self.logger.info("[BuffAutoOnSale] 正在检查 " + game["game"] + " 库存...")
                        inventory_json = self.get_buff_inventory(state="cansell", sort_by="price.desc", game=game["game"], app_id=game["app_id"], force=force_refresh)
                        items = inventory_json["items"]
                        items_count_this_loop += len(items)
                        if len(items) != 0:
                            self.logger.info("[BuffAutoOnSale] 检查到 " + game["game"] + " 库存有 " + str(len(items)) + " 件可出售商品, 正在上架...")
                            items_to_sell = []
                            for item in items:
                                item["asset_info"]["market_hash_name"] = item["market_hash_name"]
                                items_to_sell.append(item["asset_info"])
                            # 5个一组上架
                            items_to_sell_group = [items_to_sell[i : i + 5] for i in range(0, len(items_to_sell), 5)]
                            for items_to_sell in items_to_sell_group:
                                self.put_item_on_sale(
                                    items=items_to_sell, price=-1, description=description, game=game["game"], app_id=game["app_id"], use_range_price=use_range_price
                                )
                                if "buy_order" in self.config["buff_auto_on_sale"] and self.config["buff_auto_on_sale"]["buy_order"]["enable"]:
                                    self.confirm_supply_order()
                            self.logger.info("[BuffAutoOnSale] BUFF商品上架成功! ")
                        else:
                            self.logger.info("[BuffAutoOnSale] 检查到 " + game["game"] + " 库存为空, 跳过上架")
                        self.logger.info("[BuffAutoOnSale] 休眠30秒, 防止请求过快被封IP")
                        time.sleep(30)
                    if items_count_this_loop == 0:
                        self.logger.info("[BuffAutoOnSale] 库存为空, 本批次上架结束!")
                        break
            except Exception as e:
                handle_caught_exception(e, "[BuffAutoOnSale]", known=True)
                self.logger.error("[BuffAutoOnSale] BUFF商品上架失败, 错误信息: " + str(e), exc_info=True)
            self.logger.info("[BuffAutoOnSale] 休眠" + str(sleep_interval) + "秒")
            sleep_cnt = int(sleep_interval // 60)
            for _ in range(sleep_cnt):
                time.sleep(60)
                if "buy_order" in self.config["buff_auto_on_sale"] and self.config["buff_auto_on_sale"]["buy_order"]["enable"]:
                    self.confirm_supply_order()

    def supply_item_to_buy_order(self, item, highest_buy_order, game, app_id):
        sleep_seconds_to_prevent_buff_ban = 10
        if "sleep_seconds_to_prevent_buff_ban" in self.config["buff_auto_on_sale"]:
            sleep_seconds_to_prevent_buff_ban = self.config["buff_auto_on_sale"]["sleep_seconds_to_prevent_buff_ban"]
        url = "https://buff.163.com/api/market/goods/supply/manual_plus"
        data = {
            "game": game,
            "buy_order_id": highest_buy_order["id"],
            "buyer_auto_accept": highest_buy_order["user"]["is_auto_accept"],
            "price": float(highest_buy_order["price"]),
            "assets": [item],
        }
        self.logger.info("[BuffAutoOnSale] 为了避免被封IP, 休眠" + str(sleep_seconds_to_prevent_buff_ban) + "秒")
        time.sleep(sleep_seconds_to_prevent_buff_ban)
        self.logger.info("[BuffAutoOnSale] 正在供应商品至最高报价...")
        self.session.get("https://buff.163.com/api/market/steam_trade", headers=self.buff_headers)
        csrf_token = self.session.cookies.get("csrf_token", domain="buff.163.com")
        headers = {
            "User-Agent": self.buff_headers["User-Agent"],
            "X-CSRFToken": csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json",
            "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
        }
        response_json = self.session.post(url, json=data, headers=headers).json()
        if response_json["code"] == "OK":
            self.logger.info("[BuffAutoOnSale] 商品供应成功! ")
            self.logger.info("[BuffAutoOnSale] 正在发起steam报价...")
            order_id = response_json["data"][0]["id"]
            # 格式: key=value; key=value
            steam_cookies_dict = self.steam_client._session.cookies.get_dict("steamcommunity.com")
            steam_cookies = ""
            for key in steam_cookies_dict:
                steam_cookies += key + "=" + steam_cookies_dict[key] + "; "
            api_crypt = BuffApiCrypt()
            encrypted_steam_cookies = api_crypt.encrypt(steam_cookies)
            post_data = {"seller_info": encrypted_steam_cookies, "bill_orders": [order_id]}
            csrf_token = self.session.cookies.get("csrf_token", domain="buff.163.com")
            headers = {
                "User-Agent": self.buff_headers["User-Agent"],
                "X-CSRFToken": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/json",
                "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
            }
            resp_json = self.session.post("https://buff.163.com/api/market/manual_plus/seller_send_offer", json=post_data, headers=headers).json()
            if resp_json["code"] == "OK":
                self.unfinish_supply_order_list.append({"order_id": order_id, "create_time": time.time()})
                self.logger.info("[BuffAutoOnSale] 发起steam报价成功! ")
            return True
        else:
            self.logger.error(response_json)
            self.logger.error("[BuffAutoOnSale] 商品供应失败, 请检查buff_cookies.txt或稍后再试! ")
        return False

    def confirm_supply_order(self):
        """统一处理发起报价成功, 等待令牌确认的订单"""
        unfinish_order_list = []
        error_num = 0  # 超时订单
        unfinish_num = 0  # 等待buff发起报价
        finish_num = 0  # 完成

        self.logger.info("[BuffAutoOnSale] 处理等待发起报价的订单, 共有{}个".format(len(self.unfinish_supply_order_list)))
        for index, order in enumerate(self.unfinish_supply_order_list):
            order_id, create_time = order["order_id"], order["create_time"]
            if time.time() - create_time > 15 * 60:
                error_num += 1
                self.logger.error("[BuffAutoOnSale] BUFF发起steam报价失败, 报价ID: {}".format(order_id))
                continue

            try:
                url = "https://buff.163.com/api/market/bill_order/batch/info?bill_orders=" + order_id
                csrf_token = self.session.cookies.get("csrf_token", domain="buff.163.com")
                headers = {
                    "User-Agent": self.buff_headers["User-Agent"],
                    "X-CSRFToken": csrf_token,
                    "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
                }
                res_json = self.session.get(url, headers=headers).json()
                if res_json["code"] == "OK" and len(res_json["data"]["items"]) > 0 and res_json["data"]["items"][0]["tradeofferid"] is not None:
                    steam_trade_offer_id = res_json["data"]["items"][0]["tradeofferid"]
                    self.logger.info("[BuffAutoOnSale] BUFF发起steam报价成功, 报价ID: " + steam_trade_offer_id)
                    with self.steam_client_mutex:
                        self.steam_client._confirm_transaction(steam_trade_offer_id)
                    finish_num += 1
                    self.logger.info("[BuffAutoOnSale] 确认steam报价成功")
                else:
                    unfinish_order_list.append(order)
                    unfinish_num += 1
                    self.logger.error("[BuffAutoOnSale] BUFF尚未完成发起steam报价, 继续等待...")
            except Exception as e:
                unfinish_num += 1
                unfinish_order_list.append(order)
                self.logger.error("[BuffAutoOnSale] 发起steam报价失败, 错误信息: " + str(e), exc_info=True)
            if index != len(self.unfinish_supply_order_list) - 1:
                time.sleep(5)
        self.unfinish_supply_order_list = unfinish_order_list
        self.logger.info("[BuffAutoOnSale] 本轮求购订单结束, 确认steam报价成功:{}个, 等待确认:{}个, 失败订单:{}个".format(finish_num, unfinish_num, error_num))
        self.logger.info("[BuffAutoOnSale] 等待上架5个货物或1分钟后再次检测待确认steam报价订单")
