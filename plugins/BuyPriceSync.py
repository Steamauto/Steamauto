import os
import os
import time

import json5
import requests
import schedule

import uuyoupinapi
from utils.buff_helper import get_valid_session_for_buff
from utils.logger import PluginLogger, handle_caught_exception
from utils.static import (BUFF_ACCOUNT_DEV_FILE_PATH,
                          BUFF_COOKIES_FILE_PATH)
from utils.tools import exit_code, get_encoding
from utils.uu_helper import get_valid_token_for_uu


class BuyPriceSync:

    def __init__(self, config, steam_client):
        self.logger = PluginLogger("BuyPriceSync")
        self.uuyoupin = None
        self.buff = None
        self.steam_client = steam_client
        self.config = config
        self.uu_inventory_list = []
        self.buff_buy_price_dict = {}
        self.session = requests.session()
        self.buff_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
        }

    def init(self) -> bool:
        if not get_valid_token_for_uu() or not get_valid_session_for_buff(self.steam_client, self.logger):
            self.logger.error("悠悠有品登录失败！即将关闭程序！")
            exit_code.set(1)
            return True
        return False

    def get_buff_buy_order(self, page_num=1, page_size=20, game="csgo", app_id=730):
        url = "https://buff.163.com/api/market/buy_order/history"
        params = {
            "page_num": page_num,
            "page_size": page_size,
            "game": game,
            "appid": app_id
        }
        response_json = self.session.get(url, headers=self.buff_headers, params=params).json()
        if response_json["code"] == "OK":
            buy_price = {}
            for item in response_json["data"]["items"]:
                buy_price[item["asset_info"]["paintwear"][:15]] = item["price"]
            self.logger.info(f"在buff中获取到 {len(buy_price)} 个订单价格。")
            return buy_price
        else:
            self.logger.error(response_json)
            self.logger.error("获取BUFF购买订单失败, 请检查buff_cookies.txt或稍后再试! ")
            return {}

    def check_buff_account_state(self, dev=False):
        if dev and os.path.exists(BUFF_ACCOUNT_DEV_FILE_PATH):
            self.logger.info("[BuffAutoOnSale] 开发模式, 使用本地账号")
            with open(BUFF_ACCOUNT_DEV_FILE_PATH, "r", encoding=get_encoding(BUFF_ACCOUNT_DEV_FILE_PATH)) as f:
                buff_account_data = json5.load(f)
            return buff_account_data["data"]["nickname"]
        else:
            response_json = self.session.get("https://buff.163.com/account/api/user/info",
                                             headers=self.buff_headers).json()
            if dev:
                self.logger.info("开发者模式, 保存账户信息到本地")
                with open(BUFF_ACCOUNT_DEV_FILE_PATH, "w", encoding=get_encoding(BUFF_ACCOUNT_DEV_FILE_PATH)) as f:
                    json5.dump(response_json, f, indent=4)
            if response_json["code"] == "OK":
                if "data" in response_json:
                    if "nickname" in response_json["data"]:
                        return response_json["data"]["nickname"]
            self.logger.error("[BuffAutoOnSale] BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试! ")

    def auto_sync(self):
        self.logger.info("同步购入价格插件已启动")
        if self.uuyoupin is not None:
            try:
                sync_item_list = []
                self.uuyoupin.send_device_info()
                self.logger.info("正在获取库存...")
                self.uu_inventory_list = self.uuyoupin.get_trend_inventory()

                assert2abrade = {}
                for item in self.uu_inventory_list:
                    assert2abrade[item["steamAssetId"]] = item["abrade"][:15]
                try:
                    for i in range(1, self.config["buy_price_sync"]["count"]):
                        self.buff_buy_price_dict.update(self.get_buff_buy_order(page_num=i))
                        time.sleep(5)
                except TypeError as e:
                    pass
                for i, item in enumerate(self.uu_inventory_list):
                    asset_id = item["steamAssetId"]
                    if item["assetBuyPrice"] == "" and assert2abrade[asset_id] in self.buff_buy_price_dict:
                        asset = {
                            "steamAssetId": asset_id,
                            "marketHashName": item["templateHashName"],
                            "buyPrice": self.buff_buy_price_dict[assert2abrade[asset_id]],
                            "abrade": item["abrade"]
                        }
                        sync_item_list.append(asset)
                self.logger.info(f"可同步的订单 {sync_item_list}")
                if len(sync_item_list) > 0:
                    self.uuyoupin.save_buy_price(sync_item_list)

            except TypeError as e:
                handle_caught_exception(e, "BuyPriceSync")
                self.logger.error("同步购入价格出现错误。")
                exit_code.set(1)
                return 1
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("出现未知错误, 稍后再试! ")
                try:
                    self.uuyoupin.get_user_nickname()
                except KeyError as e:
                    handle_caught_exception(e, "BuyPriceSync")
                    self.logger.error("检测到悠悠有品登录已经失效,请重新登录。")
                    self.logger.error("由于登录失败，插件将自动退出。")
                    exit_code.set(1)
                    return 1

    def exec(self):
        self.logger.info("正在准备登录至UU...")
        self.uuyoupin = uuyoupinapi.UUAccount(get_valid_token_for_uu())
        try:
            self.logger.info("正在准备登录至BUFF...")
            with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
                self.session.cookies["session"] = f.read().replace("session=", "").replace("\n", "").split(";")[0]
            self.logger.info("已检测到cookies, 尝试登录")
            self.logger.info("已经登录至BUFF 用户名: " + self.check_buff_account_state())
        except TypeError as e:
            handle_caught_exception(e)
            self.logger.error("BUFF账户登录检查失败, 请检查buff_cookies.txt或稍后再试! ")
            return

        self.auto_sync()

        run_time = self.config['buy_price_sync']['run_time']
        self.logger.info(f"等待到 {run_time} 开始执行。")

        schedule.every().day.at(f"{run_time}").do(self.auto_sync)

        while True:
            schedule.run_pending()
            time.sleep(1)


if __name__ == "__main__":
    # 调试代码
    with open("config/config.json5", "r", encoding="utf-8") as f:
        my_config = json5.load(f)

    buy_price_sync = BuyPriceSync(my_config, None)
    token = get_valid_token_for_uu()
    if not token:
        buy_price_sync.logger.error("由于登录失败，插件将自动退出")
        exit_code.set(1)
    else:
        buy_price_sync.uuyoupin = uuyoupinapi.UUAccount(token)
    buy_price_sync.exec()
