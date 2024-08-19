import datetime
import time

import json5
import numpy as np
import schedule

import uuyoupinapi
from utils.logger import PluginLogger, handle_caught_exception
from utils.tools import exit_code
from utils.uu_helper import get_valid_token_for_uu


def is_subsequence(s, t):
    t_index = 0
    s_index = 0
    while t_index < len(t) and s_index < len(s):
        if s[s_index] == t[t_index]:
            s_index += 1
        t_index += 1
    return s_index == len(s)


class UUAutoLeaseItem:
    def __init__(self, config):
        self.leased_inventory_list = None
        self.logger = PluginLogger("UUAutoLeaseItem")
        self.uuyoupin = None
        self.config = config
        self.timeSleep = 10.0
        self.inventory_list = []
        self.lease_price_cache = {}

    def init(self) -> bool:
        token = get_valid_token_for_uu()
        if not token:
            self.logger.error("悠悠有品登录失败！即将关闭程序！")
            exit_code.set(1)
            return True
        return False

    def get_uu_inventory(self):

        inventory_list_rsp = self.uuyoupin.call_api(
            "POST",
            "/api/commodity/Inventory/GetUserInventoryDataListV3",
            data={
                "pageIndex": 1,
                "pageSize": 1000,
                "AppType": 4,
                "IsMerge": 0,
                "Sessionid": self.uuyoupin.device_info["deviceId"],
            },
        ).json()
        inventory_list = []
        if inventory_list_rsp["Code"] == 0:
            inventory_list = inventory_list_rsp["Data"]["ItemsInfos"]
            self.logger.info(f"库存数量 {len(inventory_list)}")
        else:
            self.logger.error(inventory_list_rsp)
            self.logger.error("获取UU库存失败!")

        return inventory_list

    def get_uu_leased_inventory(self):
        rsp = self.uuyoupin.call_api(
            "POST",
            "/api/youpin/bff/new/commodity/v1/commodity/list/lease",
            data={
                "pageIndex": 1,
                "pageSize": 100,
                "whetherMerge": 0,
                "Sessionid": self.uuyoupin.device_info["deviceId"],
            },
        ).json()
        leased_inventory_list = []
        if rsp["code"] == 0:
            leased_inventory_list = rsp["data"]["commodityInfoList"]
            self.logger.info(f"上架数量 {len(leased_inventory_list)}")
        elif rsp["code"] == 9004001:
            self.logger.info("暂无自租商品")
        else:
            self.logger.error(leased_inventory_list)
            self.logger.error("获取UU上架物品失败!")
        self.leased_inventory_list = leased_inventory_list

    def get_market_lease_price(self, item_id, min_price, cnt=15, max_price=20000):

        if item_id in self.lease_price_cache:
            if datetime.datetime.now() - self.lease_price_cache[item_id]["cache_time"] <= datetime.timedelta(
                    minutes=20):
                commodity_name = self.lease_price_cache[item_id]["commodity_name"]
                lease_unit_price = self.lease_price_cache[item_id]["lease_unit_price"]
                long_lease_unit_price = self.lease_price_cache[item_id]["long_lease_unit_price"]
                lease_deposit = self.lease_price_cache[item_id]["lease_deposit"]
                self.logger.info(
                    f"{commodity_name} 使用缓存价格设置, "
                    f"lease_unit_price: {lease_unit_price:.2f}, long_lease_unit_price: {long_lease_unit_price:.2f}, "
                    f"lease_deposit: {lease_deposit:.2f}"
                )
                return {
                    "LeaseUnitPrice": lease_unit_price,
                    "LongLeaseUnitPrice": long_lease_unit_price,
                    "LeaseDeposit": lease_deposit,
                }

        lease_price_rsp = self.uuyoupin.call_api(
            "POST",
            "/api/homepage/v3/detail/commodity/list/lease",
            data={
                "hasLease": "true",
                "haveBuZhangType": 0,
                "listSortType": "2",
                "listType": 30,
                "mergeFlag": 0,
                "pageIndex": 1,
                "pageSize": 50,
                "sortType": "1",
                "sortTypeKey": "LEASE_DEFAULT",
                "status": "20",
                "stickerAbrade": 0,
                "stickersIsSort": False,
                "templateId": f"{item_id}",
                "ultraLongLeaseMoreZones": 0,
                "userId": self.uuyoupin.userId,
                "Sessionid": self.uuyoupin.device_info["deviceId"],
            },
        ).json()
        if lease_price_rsp["Code"] == 0:
            rsp_list = lease_price_rsp["Data"]["CommodityList"]
            rsp_cnt = len(rsp_list)
            commodity_name = rsp_list[0]["CommodityName"]

            lease_unit_price_list = []
            long_lease_unit_price_list = []
            lease_deposit_list = []
            cnt = min(cnt, rsp_cnt)
            for i in range(cnt):
                if (
                        rsp_list[i]["LeaseDeposit"]
                        and min_price < float(rsp_list[i]["LeaseDeposit"]) < max_price
                ):
                    if rsp_list[i]["LeaseUnitPrice"] and i < min(10, cnt):
                        lease_unit_price_list.append(
                            float(rsp_list[i]["LeaseUnitPrice"])
                        )
                    if rsp_list[i]["LongLeaseUnitPrice"]:
                        long_lease_unit_price_list.append(
                            float(rsp_list[i]["LongLeaseUnitPrice"])
                        )

                if (
                        rsp_list[i]["LeaseDeposit"]
                        and float(rsp_list[i]["LeaseDeposit"]) < max_price
                        and rsp_list[i]["LeaseUnitPrice"]
                        and i < min(10, cnt)
                ):

                    lease_deposit_list.append(float(rsp_list[i]["LeaseDeposit"]))

            lease_unit_price = np.mean(lease_unit_price_list) * 0.97
            lease_unit_price = max(lease_unit_price, lease_unit_price_list[0], 0.01)

            long_lease_unit_price = min(
                lease_unit_price * 0.98, np.mean(long_lease_unit_price_list) * 0.95
            )
            if len(long_lease_unit_price_list) == 0:
                long_lease_unit_price = max(lease_unit_price - 0.01, 0.01)
            else:
                long_lease_unit_price = max(long_lease_unit_price, long_lease_unit_price_list[0], 0.01)

            lease_deposit = max(np.mean(lease_deposit_list) * 0.98, min(lease_deposit_list))

            self.logger.info(
                f"{commodity_name}, "
                f"lease_unit_price: {lease_unit_price:.2f}, long_lease_unit_price: {long_lease_unit_price:.2f}, "
                f"lease_deposit: {lease_deposit:.2f}"
            )
            self.logger.info(
                f"lease_unit_price_list: {lease_unit_price_list}, "
                f"long_lease_unit_price_list: {long_lease_unit_price_list}"
            )
        else:
            lease_unit_price = long_lease_unit_price = lease_deposit = 0
            commodity_name = ""
            self.logger.error(
                f"Get Lease Price Failed. "
                f"Response code:{lease_price_rsp['Code']}, body:{lease_price_rsp}"
            )

        lease_unit_price = round(lease_unit_price, 2)
        long_lease_unit_price = round(long_lease_unit_price, 2)
        lease_deposit = round(lease_deposit, 2)

        if lease_unit_price != 0:
            self.lease_price_cache[item_id] = {
                "commodity_name": commodity_name,
                "lease_unit_price": lease_unit_price,
                "long_lease_unit_price": long_lease_unit_price,
                "lease_deposit": lease_deposit,
                "cache_time": datetime.datetime.now(),
            }

        return {
            "LeaseUnitPrice": lease_unit_price,
            "LongLeaseUnitPrice": long_lease_unit_price,
            "LeaseDeposit": lease_deposit,
        }

    def put_lease_item_on_shelf(self, items):
        item_infos = items
        num = len(item_infos)
        if num == 0:
            self.logger.info(f"Nothing to be put onto sell.")
            return True

        lease_on_shelf_rsp = self.uuyoupin.call_api(
            "POST",
            "/api/commodity/Inventory/SellInventoryWithLeaseV2",
            data={
                "GameId": "730",  # Csgo
                "itemInfos": item_infos,
                "Sessionid": self.uuyoupin.device_info["deviceId"],
            },
        ).json()
        if lease_on_shelf_rsp["Code"] == 0:
            self.logger.info(f"lease {num} items Succ.")
            return num
        else:
            self.logger.error(
                f"Put on Sale Failed. "
                f"Response code:{lease_on_shelf_rsp['Code']}, body:{lease_on_shelf_rsp}"
            )
            return -1

    def change_leased_price(self, items):
        item_infos = items
        num = len(item_infos)
        if num == 0:
            self.logger.info(f"Nothing to be put onto sell.")
            return True

        rsp = self.uuyoupin.call_api(
            "PUT",
            "/api/commodity/Commodity/PriceChangeWithLeaseV2",
            data={
                "Commoditys": item_infos,
                "Sessionid": self.uuyoupin.device_info["deviceId"],
            },
        ).json()
        if rsp["Code"] == 0:
            self.logger.info(f"lease {num} items Succ.")
            return num
        else:
            self.logger.error(
                f"Put on Sale Failed. "
                f"Response code:{rsp['Code']}, body:{rsp}"
            )
            return -1

    def auto_lease(self):
        self.logger.info("UU自动租赁上架插件已启动, 休眠3秒, 与自动接收报价插件错开运行时间")
        self.operate_sleep()

        if self.uuyoupin is not None:
            try:
                lease_item_list = []
                self.uuyoupin.send_device_info()
                self.logger.info("正在获取悠悠有品库存...")
                self.inventory_list = self.get_uu_inventory()
                self.operate_sleep(10)

                self.inventory_list = self.get_uu_inventory()

                for i, item in enumerate(self.inventory_list):
                    if item["AssetInfo"] is None:
                        continue
                    asset_id = item["SteamAssetId"]
                    item_id = item["TemplateInfo"]["Id"]
                    short_name = item["ShotName"]
                    price = item["TemplateInfo"]["MarkPrice"]
                    if (
                            price < self.config["uu_auto_lease_item"]["filter_price"]
                            or (item["Tradable"] is False)
                            or item["AssetStatus"] != 0
                            or any(s != "" and is_subsequence(s, short_name) for s in
                                   self.config["uu_auto_lease_item"]["filter_name"])
                    ):
                        continue
                    self.operate_sleep(20)

                    price_rsp = self.get_market_lease_price(item_id, min_price=price)
                    if price_rsp["LeaseUnitPrice"] == 0:
                        continue

                    lease_item = {
                        "AssetId": asset_id,
                        "IsCanLease": True,
                        "IsCanSold": False,
                        # "SupportZeroCD": 1,
                        "LeaseMaxDays": self.config["uu_auto_lease_item"]["lease_max_days"],
                        "LeaseUnitPrice": price_rsp["LeaseUnitPrice"],
                        "LongLeaseUnitPrice": price_rsp["LongLeaseUnitPrice"],
                        "LeaseDeposit": price_rsp["LeaseDeposit"],
                        "OpenLeaseActivity": False,
                        "PrivateLeaseCommodity": 0,
                        "NomarlChargePercent": "0.25",
                        "Remark": "",
                    }
                    if self.config["uu_auto_lease_item"]["lease_max_days"] <= 8:
                        del lease_item["LongLeaseUnitPrice"]

                    lease_item_list.append(lease_item)

                self.logger.info(f"{len(lease_item_list)} item can lease.")

                self.operate_sleep()
                self.put_lease_item_on_shelf(lease_item_list)

            except TypeError as e:
                handle_caught_exception(e, "UUAutoLeaseItem")
                self.logger.error("悠悠有品出租出现错误")
                exit_code.set(1)
                return 1
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("出现未知错误, 稍后再试! ")
                try:
                    self.uuyoupin.get_user_nickname()
                except KeyError as e:
                    handle_caught_exception(e, "UUAutoLeaseItem")
                    self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                    self.logger.error("由于登录失败，插件将自动退出")
                    exit_code.set(1)
                    return 1

    def auto_change_price(self):
        self.logger.info("[UUAutoChangePrice] UU租赁自动修改价格已启动, 休眠5秒, 与自动接收报价错开运行时间")
        self.operate_sleep(5)

        try:
            self.uuyoupin.send_device_info()
            self.logger.info("[UUAutoChangePrice] 正在获取悠悠有品已上架物品...")
            self.get_uu_leased_inventory()

            new_leased_item_list = []
            for i, item in enumerate(self.leased_inventory_list):
                asset_id = item["id"]
                item_id = item["templateId"]
                short_name = item["name"]
                price = float(item["referencePrice"][1:])

                if any(s != "" and is_subsequence(s, short_name) for s in
                       self.config["uu_auto_lease_item"]["filter_name"]):
                    continue

                price_rsp = self.get_market_lease_price(item_id, min_price=price)
                if price_rsp["LeaseUnitPrice"] == 0:
                    continue
                lease_item = {
                    "CommodityId": asset_id,
                    "IsCanLease": True,
                    "IsCanSold": False,
                    # "SupportZeroCD": 1,
                    "LeaseMaxDays": self.config["uu_auto_lease_item"]["lease_max_days"],
                    "LeaseUnitPrice": price_rsp["LeaseUnitPrice"],
                    "LongLeaseUnitPrice": price_rsp["LongLeaseUnitPrice"],
                    "LeaseDeposit": price_rsp["LeaseDeposit"],
                    "OpenLeaseActivity": False,
                    "NomarlChargePercent": "0.25",
                    "Remark": "",
                }
                if self.config["uu_auto_lease_item"]["lease_max_days"] <= 8:
                    del lease_item["LongLeaseUnitPrice"]
                new_leased_item_list.append(lease_item)
            self.logger.info(f"{len(new_leased_item_list)} item changed lease price.")
            self.operate_sleep(30)
            self.change_leased_price(new_leased_item_list)

        except TypeError as e:
            handle_caught_exception(e, "UUAutoLeaseItem-AutoChangePrice")
            self.logger.error("悠悠有品出租出现错误")
            exit_code.set(1)
            return 1
        except Exception as e:
            self.logger.error(e, exc_info=True)
            self.logger.info("出现未知错误, 稍后再试! ")
            try:
                self.uuyoupin.get_user_nickname()
            except KeyError as e:
                handle_caught_exception(e, "UUAutoLeaseItem-AutoChangePrice")
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

        self.logger.info(f"以下物品不会出租: {self.config['uu_auto_lease_item']['filter_name']}")

        self.pre_check_price()
        self.auto_lease()
        # self.auto_change_price()

        run_time = self.config['uu_auto_lease_item']['run_time']
        interval = self.config['uu_auto_lease_item']['interval']

        self.logger.info(f"[AUTO LEASE] waiting run at {run_time}.")
        self.logger.info(f"[AUTO CHANGE PRICE] run every {interval} minutes.")

        schedule.every().day.at(f"{run_time}").do(self.auto_lease)
        schedule.every(interval).minutes.do(self.auto_change_price)

        while True:
            schedule.run_pending()
            time.sleep(1)

    def operate_sleep(self, sleep=None):
        if sleep is None:
            time.sleep(self.timeSleep)
        else:
            time.sleep(sleep)

    def pre_check_price(self):
        self.get_market_lease_price(44444, 1000)
        self.logger.info("请检查押金设置是否有问题，如有请终止程序，否则15s后开始运行该插件")
        self.operate_sleep(15)


if __name__ == "__main__":
    # 调试代码
    with open("config/config.json5", "r", encoding="utf-8") as f:
        my_config = json5.load(f)

    uu_auto_lease = UUAutoLeaseItem(my_config)
    uu_auto_lease.auto_lease()
