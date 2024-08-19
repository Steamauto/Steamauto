import datetime
import time

import json5
import numpy as np
import schedule

import uuyoupinapi
from utils.logger import PluginLogger, handle_caught_exception
from utils.tools import exit_code, is_subsequence
from utils.uu_helper import get_valid_token_for_uu


class UUAutoSellItem:
    def __init__(self, config):
        self.sale_inventory_list = None
        self.logger = PluginLogger("UUAutoSellItem")
        self.uuyoupin = None
        self.config = config
        self.timeSleep = 10.0
        self.inventory_list = []
        self.sale_price_cache = {}
        self.item_buy_price_cache = {}

    def init(self) -> bool:
        self.logger.info("[UUAutoSellItem]")
        token = get_valid_token_for_uu()
        if not token:
            self.logger.error("悠悠有品登录失败！即将关闭程序！")
            exit_code.set(1)
            return True
        return False

    def get_uu_sale_inventory(self):
        rsp = self.uuyoupin.call_api(
            "POST",
            "/api/youpin/bff/new/commodity/v1/commodity/list/sell",
            data={
                "pageIndex": 1,
                "pageSize": 100,
                "whetherMerge": 0
            },
        ).json()
        sale_inventory_list = []
        if rsp["code"] == 0:
            sale_inventory_list = rsp["data"]["commodityInfoList"]
            self.logger.info(f"上架数量 {len(sale_inventory_list)}")
        else:
            self.logger.error(sale_inventory_list)
            self.logger.error("获取UU上架物品失败!")
        self.sale_inventory_list = sale_inventory_list

    def get_market_sale_price(self, item_id, cnt=10):

        if item_id in self.sale_price_cache:
            if datetime.datetime.now() - self.sale_price_cache[item_id]["cache_time"] <= datetime.timedelta(
                    minutes=20):
                commodity_name = self.sale_price_cache[item_id]["commodity_name"]
                sale_price = self.sale_price_cache[item_id]["sale_price"]
                self.logger.info(
                    f"{commodity_name} 使用缓存价格设置, "
                    f"sale_price: {sale_price:.2f}"
                )
                return sale_price

        sale_price_rsp = self.uuyoupin.call_api(
            "POST",
            "/api/homepage/v2/detail/commodity/list/sell",
            data={
                "pageIndex": 1,
                "pageSize": 10,
                "templateId": f"{item_id}"
            },
        ).json()
        if sale_price_rsp["Code"] == 0:
            rsp_list = sale_price_rsp["Data"]["CommodityList"]
            rsp_cnt = len(rsp_list)
            commodity_name = rsp_list[0]["CommodityName"]

            sale_price_list = []
            cnt = min(cnt, rsp_cnt)
            for i in range(cnt):
                if rsp_list[i]["Price"] and i < cnt:
                    sale_price_list.append(
                        float(rsp_list[i]["Price"])
                    )

            sale_price = np.mean(sale_price_list) * 0.99
            sale_price = max(sale_price, sale_price_list[0], 0.01)

            self.logger.info(
                f"{commodity_name}, "
                f"sale_price: {sale_price:.2f}, \n"
                f"sale_price_list: {sale_price_list}"
            )
        else:
            sale_price = 0
            commodity_name = ""
            self.logger.error(
                f"Get Sale Price Failed. "
                f"Response code:{sale_price_rsp['Code']}, body:{sale_price_rsp}"
            )

        sale_price = round(sale_price, 2)

        if sale_price != 0:
            self.sale_price_cache[item_id] = {
                "commodity_name": commodity_name,
                "sale_price": sale_price,
                "cache_time": datetime.datetime.now(),
            }

        return sale_price

    def sell_item(self, items):
        item_infos = items
        num = len(item_infos)
        if num == 0:
            self.logger.info(f"没有物品可以出售.")
            return True

        sale_on_shelf_rsp = self.uuyoupin.call_api(
            "POST",
            "/api/commodity/Inventory/SellInventoryWithLeaseV2",
            data={
                "GameId": "730",  # Csgo
                "itemInfos": item_infos
            },
        ).json()
        if sale_on_shelf_rsp["Code"] == 0:
            self.logger.info(f"sale {num} items Succ.")
            return num
        else:
            self.logger.error(
                f"Put on Sale Failed. "
                f"Response code:{sale_on_shelf_rsp['Code']}, body:{sale_on_shelf_rsp}"
            )
            return -1

    def change_sale_price(self, items):
        item_infos = items
        num = len(item_infos)
        if num == 0:
            self.logger.info(f"没有物品可以修改价格。")
            return True

        rsp = self.uuyoupin.call_api(
            "PUT",
            "/api/commodity/Commodity/PriceChangeWithLeaseV2",
            data={
                "Commoditys": item_infos,
            },
        ).json()
        if rsp["Code"] == 0:
            self.logger.info(f"成功修改 {num} 件物品价格。")
            return num
        else:
            self.logger.error(
                f"change item sale price failed. "
                f"Response code:{rsp['Code']}, body:{rsp}"
            )
            return -1

    def auto_sell(self):
        self.logger.info("悠悠有品出售自动上架插件已启动")
        self.operate_sleep(5)

        if self.uuyoupin is not None:
            try:
                sale_item_list = []
                self.uuyoupin.send_device_info()
                self.logger.info("正在获取悠悠有品库存...")
                self.inventory_list = self.uuyoupin.get_inventory()
                self.operate_sleep(10)

                self.inventory_list = self.uuyoupin.get_inventory()

                for i, item in enumerate(self.inventory_list):
                    if item["AssetInfo"] is None:
                        continue
                    asset_id = item["SteamAssetId"]
                    item_id = item["TemplateInfo"]["Id"]
                    short_name = item["ShotName"]
                    buy_price = float(item["AssetBuyPrice"][5:]) if "AssetBuyPrice" in item else 0

                    self.item_buy_price_cache[item_id] = buy_price

                    if (
                            item["Tradable"] is False
                            or item["AssetStatus"] != 0
                            or not any(s != "" and is_subsequence(s, short_name) for s in
                                       self.config["uu_auto_sell_item"]["name"])
                    ):
                        continue
                    self.operate_sleep()

                    sale_price = self.get_market_sale_price(item_id)
                    if self.config['uu_auto_sell_item']['take_profile']:
                        self.logger.info(f"按{self.config['uu_auto_sell_item']['take_profile_ratio']:.2f}止盈率设置价格。")
                        if buy_price > 0:
                            sale_price = max(sale_price, self.get_take_profile_price(buy_price))
                            self.logger.info(f"最终出售价格{sale_price:.2f}。")
                        else:
                            self.logger.info("未获取到购入价格。")
                    if sale_price == 0:
                        continue

                    sale_item = {
                        "AssetId": asset_id,
                        "IsCanLease": False,
                        "IsCanSold": True,
                        "Price": sale_price,
                        "Remark": "",
                    }

                    sale_item_list.append(sale_item)

                self.logger.info(f"{len(sale_item_list)} 件物品可以出售。")

                self.operate_sleep()
                self.sell_item(sale_item_list)

            except TypeError as e:
                handle_caught_exception(e, "UUAutoSellItem")
                self.logger.error("悠悠有品出售自动上架出现错误")
                exit_code.set(1)
                return 1
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("出现未知错误, 稍后再试! ")
                try:
                    self.uuyoupin.get_user_nickname()
                except KeyError as e:
                    handle_caught_exception(e, "UUAutoSellItem")
                    self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                    self.logger.error("由于登录失败，插件将自动退出")
                    exit_code.set(1)
                    return 1

    def auto_change_price(self):
        self.logger.info("[UUAutoSellChangePrice] 悠悠有品出售自动修改价格已启动")
        self.operate_sleep(10)

        try:
            self.uuyoupin.send_device_info()
            self.logger.info("[UUAutoSellChangePrice] 正在获取悠悠有品已上架物品...")
            self.get_uu_sale_inventory()

            new_sale_item_list = []
            for i, item in enumerate(self.sale_inventory_list):
                asset_id = item["id"]
                item_id = item["templateId"]
                short_name = item["name"]
                buy_price = self.item_buy_price_cache.get(item_id, 0)

                if not any(s != "" and is_subsequence(s, short_name) for s in
                           self.config["uu_auto_sell_item"]["name"]):
                    continue

                sale_price = self.get_market_sale_price(item_id)
                if self.config['uu_auto_sell_item']['take_profile']:
                    self.logger.info(f"按{self.config['uu_auto_sell_item']['take_profile_ratio']:.2f}止盈率设置价格。")
                    if buy_price > 0:
                        self.logger.debug(sale_price)
                        self.logger.debug(self.get_take_profile_price(buy_price))
                        sale_price = max(sale_price, self.get_take_profile_price(buy_price))
                        self.logger.info(f"最终出售价格{sale_price:.2f}。")
                    else:
                        self.logger.info("未获取到购入价格。")

                if sale_price == 0:
                    continue
                sale_item = {
                    "CommodityId": asset_id,
                    "IsCanLease": False,
                    "IsCanSold": True,
                    "Price": sale_price,
                    "Remark": ""
                }
                new_sale_item_list.append(sale_item)
            self.logger.info(f"{len(new_sale_item_list)} 件物品可以更新出售价格。")
            self.operate_sleep(30)
            self.change_sale_price(new_sale_item_list)

        except TypeError as e:
            handle_caught_exception(e, "UUAutoSellItem-AutoChangePrice")
            self.logger.error("悠悠有品出售自动上架出现错误")
            exit_code.set(1)
            return 1
        except Exception as e:
            self.logger.error(e, exc_info=True)
            self.logger.info("出现未知错误, 稍后再试! ")
            try:
                self.uuyoupin.get_user_nickname()
            except KeyError as e:
                handle_caught_exception(e, "UUAutoSellItem-AutoChangePrice")
                self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                self.logger.error("由于登录失败，插件将自动退出")
                exit_code.set(1)
                return 1

    def exec(self):
        self.logger.info("run func exec.")
        token = get_valid_token_for_uu()
        if not token:
            self.logger.error("由于登录失败，插件将自动退出")
            exit_code.set(1)
            return 1
        else:
            self.uuyoupin = uuyoupinapi.UUAccount(token)

        self.logger.info(f"以下物品会出售: {self.config['uu_auto_sell_item']['name']}")

        self.auto_sell()

        run_time = self.config['uu_auto_sell_item']['run_time']
        interval = self.config['uu_auto_sell_item']['interval']

        self.logger.info(f"[AUTO SALE] 等待到 {run_time} 开始执行.")
        self.logger.info(f"[AUTO SALE CHANGE PRICE] 每隔 {interval} 分钟执行一次.")

        schedule.every().day.at(f"{run_time}").do(self.auto_sell)
        schedule.every(interval).minutes.do(self.auto_change_price)

        while True:
            schedule.run_pending()
            time.sleep(1)

    def operate_sleep(self, sleep=None):
        if sleep is None:
            time.sleep(self.timeSleep)
        else:
            time.sleep(sleep)

    def get_take_profile_price(self, buy_price):
        take_profile_ratio = self.config['uu_auto_sell_item']['take_profile_ratio']
        return buy_price * (1 + take_profile_ratio)


if __name__ == "__main__":
    # 调试代码
    with open("config/config.json5", "r", encoding="utf-8") as f:
        my_config = json5.load(f)

    uu_auto_sell = UUAutoSellItem(my_config)
    token = get_valid_token_for_uu()
    if not token:
        uu_auto_sell.logger.error("由于登录失败，插件将自动退出")
        exit_code.set(1)
    else:
        uu_auto_sell.uuyoupin = uuyoupinapi.UUAccount(token)
    uu_auto_sell.auto_sell()
    # uu_auto_sell.auto_change_price()
    time.sleep(5)
    uu_auto_sell.auto_change_price()
