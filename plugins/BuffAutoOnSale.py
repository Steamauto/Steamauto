import datetime
import os
import random
import time

import pyjson5 as json
import requests
from apprise.AppriseAsset import AppriseAsset
from requests.exceptions import ProxyError
from steampy.exceptions import InvalidCredentials

from utils.logger import handle_caught_exception
from utils.static import APPRISE_ASSET_FOLDER, BUFF_ACCOUNT_DEV_FILE_PATH, BUFF_COOKIES_FILE_PATH, SUPPORT_GAME_TYPES
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


class BuffAutoOnSale:
    buff_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
    }

    def __init__(self, logger, steam_client, steam_client_mutex, config):
        self.logger = logger
        self.steam_client = steam_client
        self.config = config
        self.steam_client_mutex = steam_client_mutex
        self.development_mode = self.config["development_mode"]
        AppriseAsset(plugin_paths=[os.path.join(os.path.dirname(__file__), "..", APPRISE_ASSET_FOLDER)])
        self.session = requests.session()
        self.lowest_price_cache = {}

    def init(self) -> bool:
        if not os.path.exists(BUFF_COOKIES_FILE_PATH):
            with open(BUFF_COOKIES_FILE_PATH, "w", encoding="utf-8") as f:
                f.write("session=")
            return True
        return False

    def check_buff_account_state(self, dev=False):
        if dev and os.path.exists(BUFF_ACCOUNT_DEV_FILE_PATH):
            self.logger.info("[BuffAutoOnSale] 开发模式, 使用本地账号")
            with open(BUFF_ACCOUNT_DEV_FILE_PATH, "r", encoding=get_encoding(BUFF_ACCOUNT_DEV_FILE_PATH)) as f:
                buff_account_data = json.load(f)
            return buff_account_data["data"]["nickname"]
        else:
            response_json = self.session.get("https://buff.163.com/account/api/user/info", headers=self.buff_headers).json()
            if dev:
                self.logger.info("开发者模式, 保存账户信息到本地")
                with open(BUFF_ACCOUNT_DEV_FILE_PATH, "w", encoding=get_encoding(BUFF_ACCOUNT_DEV_FILE_PATH)) as f:
                    json.dump(response_json, f, indent=4)
            if response_json["code"] == "OK":
                if "data" in response_json:
                    if "nickname" in response_json["data"]:
                        return response_json["data"]["nickname"]
            self.logger.error("[BuffAutoOnSale] BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试! ")

    def get_buff_inventory(self, page_num=1, page_size=60, sort_by="time.desc", state="all", force=0, force_wear=0,
                           game="csgo", app_id=730):
        url = "https://buff.163.com/api/market/steam_inventory"
        params = {
            "page_num": page_num,
            "page_size": page_size,
            "sort_by": sort_by,
            "state": state,
            "force": force,
            "force_wear": force_wear,
            "game": game,
            "appid": app_id
        }
        response_json = self.session.get(url, headers=self.buff_headers, params=params).json()
        if response_json["code"] == "OK":
            return response_json["data"]
        else:
            self.logger.error(response_json)
            self.logger.error("[BuffAutoOnSale] 获取BUFF库存失败, 请检查buff_cookies.txt或稍后再试! ")
            return {}

    def put_item_on_sale(self, items, price, description="", game="csgo", app_id=730):
        url = "https://buff.163.com/api/market/sell_order/create/manual_plus"
        assets = []
        for item in items:
            sell_price = price
            if sell_price == -1:
                sell_price = self.get_lowest_price(item["goods_id"], game, app_id)
            if sell_price != -1:
                sell_price = sell_price - 0.01
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
        data = {"appid": str(app_id), "game": game, "assets": assets}
        csrf_token = self.session.cookies.get("csrf_token")
        headers = {
            "User-Agent": self.buff_headers["User-Agent"],
            "X-CSRFToken": csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json",
            "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
        }
        response_json = self.session.post(url, json=data, headers=headers).json()
        if response_json["code"] == "OK":
            return response_json["data"]
        else:
            self.logger.error(response_json)
            self.logger.error("[BuffAutoOnSale] 上架BUFF商品失败, 请检查buff_cookies.txt或稍后再试! ")
            return {}

    def get_lowest_price(self, goods_id, game="csgo", app_id=730):
        if goods_id in self.lowest_price_cache:
            if self.lowest_price_cache[goods_id]["cache_time"] >= datetime.datetime.now() - datetime.timedelta(hours=1):
                lowest_price = self.lowest_price_cache[goods_id]["lowest_price"]
                return lowest_price
        self.logger.info("[BuffAutoOnSale] 获取BUFF商品最低价")
        self.logger.info("[BuffAutoOnSale] 休眠5秒, 防止请求过快被封IP")
        time.sleep(5)
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
            lowest_price = float(response_json["data"]["items"][0]["price"])
            self.lowest_price_cache[goods_id] = {"lowest_price": lowest_price, "cache_time": datetime.datetime.now()}
            return lowest_price
        else:
            self.logger.error(response_json)
            self.logger.error("[BuffAutoOnSale] 获取BUFF商品最低价失败, 请检查buff_cookies.txt或稍后再试! ")
            return -1

    def exec(self):
        self.logger.info("[BuffAutoOnSale] BUFF自动上架插件已启动, 休眠120秒, 与自动接收报价插件错开运行时间")
        time.sleep(120)
        try:
            self.logger.info("[BuffAutoOnSale] 正在准备登录至BUFF...")
            with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
                self.session.cookies["session"] = f.read().replace("session=", "").replace("\n", "").split(";")[0]
            self.logger.info("[BuffAutoOnSale] 已检测到cookies, 尝试登录")
            self.logger.info("[BuffAutoOnSale] 已经登录至BUFF 用户名: " +
                             self.check_buff_account_state(dev=self.development_mode))
        except TypeError as e:
            handle_caught_exception(e)
            self.logger.error("[BuffAutoOnSale] BUFF账户登录检查失败, 请检查buff_cookies.txt或稍后再试! ")
            return
        sleep_interval = int(self.config["buff_auto_on_sale"]["interval"])
        black_list_time = []
        if 'blacklist_time' in self.config["buff_auto_on_sale"]:
            black_list_time = self.config["buff_auto_on_sale"]["blacklist_time"]
        white_list_time = []
        if 'whitelist_time' in self.config["buff_auto_on_sale"]:
            white_list_time = self.config["buff_auto_on_sale"]["whitelist_time"]
        random_chance = 100
        if 'random_chance' in self.config["buff_auto_on_sale"]:
            random_chance = self.config["buff_auto_on_sale"]["random_chance"] * 100
        force_refresh = 0
        if 'force_refresh' in self.config["buff_auto_on_sale"] and self.config["buff_auto_on_sale"]["force_refresh"]:
            force_refresh = 1
        description = ''
        if 'description' in self.config["buff_auto_on_sale"]:
            description = self.config["buff_auto_on_sale"]["description"]
        while True:
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
            try:
                while True:
                    items_count_this_loop = 0
                    for game in SUPPORT_GAME_TYPES:
                        self.logger.info("[BuffAutoOnSale] 正在检查 " + game["game"] + " 库存...")
                        inventory_json = self.get_buff_inventory(
                            state="cansell", sort_by="price.desc", game=game["game"], app_id=game["app_id"],
                            force=force_refresh
                        )
                        items = inventory_json["items"]
                        items_count_this_loop += len(items)
                        if len(items) != 0:
                            self.logger.info(
                                "[BuffAutoOnSale] 检查到 " + game["game"] + " 库存有 " + str(len(items)) + " 件可出售商品, 正在上架..."
                            )
                            items_to_sell = []
                            for item in items:
                                item["asset_info"]["market_hash_name"] = item["market_hash_name"]
                                items_to_sell.append(item["asset_info"])
                            self.put_item_on_sale(items=items_to_sell, price=-1, description=description)
                            self.logger.info("[BuffAutoOnSale] BUFF商品上架成功! ")
                        else:
                            self.logger.info("[BuffAutoOnSale] 检查到 " + game["game"] + " 库存为空, 跳过上架")
                        self.logger.info("[BuffAutoOnSale] 休眠30秒, 防止请求过快被封IP")
                        time.sleep(30)
                    if items_count_this_loop == 0:
                        self.logger.info("[BuffAutoOnSale] 库存为空, 本批次上架结束!")
                        break
            except ProxyError:
                self.logger.error('[BuffAutoOnSale] 代理异常, 本软件可不需要代理或任何VPN')
                self.logger.error('[BuffAutoOnSale] 可以尝试关闭代理或VPN后重启软件')
            except (ConnectionError, ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError):
                self.logger.error('[BuffAutoOnSale] 网络异常, 请检查网络连接')
                self.logger.error('[BuffAutoOnSale] 这个错误可能是由于代理或VPN引起的, 本软件可无需代理或任何VPN')
                self.logger.error('[BuffAutoOnSale] 如果你正在使用代理或VPN, 请尝试关闭后重启软件')
                self.logger.error('[BuffAutoOnSale] 如果你没有使用代理或VPN, 请检查网络连接')
            except InvalidCredentials as e:
                self.logger.error('[BuffAutoOnSale] mafile有问题, 请检查mafile是否正确(尤其是identity_secret)')
                self.logger.error(str(e))
            except Exception as e:
                self.logger.error("[BuffAutoOnSale] BUFF商品上架失败, 错误信息: " + str(e), exc_info=True)
            self.logger.info("[BuffAutoOnSale] 休眠" + str(sleep_interval) + "秒")
            time.sleep(sleep_interval)
