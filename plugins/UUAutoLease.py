import time

import json5
import numpy as np
import schedule

import uuyoupinapi
from utils.logger import PluginLogger, handle_caught_exception
from utils.tools import exit_code
from utils.uu_helper import get_valid_token_for_uu


class UUAutoLeaseItem:
    def __init__(self, config):
        self.leased_inventory_list = None
        self.logger = PluginLogger("UUAutoLeaseItem")
        self.uuyoupin = None
        self.config = config
        self.timeSleep = 3.0
        self.inventory_list = []

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

        self.inventory_list = inventory_list

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
        else:
            self.logger.error(leased_inventory_list)
            self.logger.error("获取UU库存失败!")
        self.leased_inventory_list = leased_inventory_list

    def get_market_lease_price(self, item_id, min_price, cnt=10, max_price=10000):
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
            commodity_name = rsp_list[0]["CommodityName"]

            lease_unit_price_list = []
            long_lease_unit_price_list = []
            for i in range(cnt):
                if (
                        rsp_list[i]["LeaseDeposit"]
                        and min_price < float(rsp_list[i]["LeaseDeposit"]) < max_price
                ):
                    if rsp_list[i]["LeaseUnitPrice"]:
                        lease_unit_price_list.append(
                            float(rsp_list[i]["LeaseUnitPrice"])
                        )
                    if rsp_list[i]["LongLeaseUnitPrice"]:
                        long_lease_unit_price_list.append(
                            float(rsp_list[i]["LongLeaseUnitPrice"])
                        )

            lease_deposit_list = []
            for i in range(cnt):
                if (
                        rsp_list[i]["LeaseDeposit"]
                        and float(rsp_list[i]["LeaseDeposit"]) < max_price
                        and rsp_list[i]["LeaseUnitPrice"]
                ):
                    lease_deposit_list.append(float(rsp_list[i]["LeaseDeposit"]))

            lease_unit_price = np.mean(lease_unit_price_list) * 0.98
            long_lease_unit_price = min(
                lease_unit_price * 0.98, np.mean(long_lease_unit_price_list) * 0.98
            )
            lease_deposit = np.mean(lease_deposit_list) * 0.99

            lease_unit_price = max(lease_unit_price, lease_unit_price_list[0], 0.01)
            if len(long_lease_unit_price_list) == 0:
                long_lease_unit_price = max(lease_unit_price - 0.01, 0.01)
            else:
                long_lease_unit_price = max(long_lease_unit_price, long_lease_unit_price_list[0], 0.01)
            lease_deposit = max(lease_deposit, min(lease_deposit_list))

            self.logger.info(
                f"{commodity_name}, "
                f"lease_unit_price: {lease_unit_price:.2f}, long_lease_unit_price: {long_lease_unit_price:.2f}, "
                f"lease_deposit: {lease_deposit:.2f}"
            )
            self.logger.debug(
                f"lease_unit_price_list: {lease_unit_price_list}, "
                f"long_lease_unit_price_list: {long_lease_unit_price_list}"
            )
        else:
            lease_unit_price = long_lease_unit_price = lease_deposit = 0
            self.logger.error(
                f"Get Lease Price Failed. "
                f"Response code:{lease_price_rsp['Code']}, body:{lease_price_rsp}"
            )

        return {
            "LeaseUnitPrice": round(lease_unit_price, 2),
            "LongLeaseUnitPrice": round(long_lease_unit_price, 2),
            "LeaseDeposit": round(lease_deposit, 2),
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

        token = get_valid_token_for_uu()
        if not token:
            self.logger.error("由于登录失败，插件将自动退出")
            exit_code.set(1)
            return 1
        else:
            self.uuyoupin = uuyoupinapi.UUAccount(token)

        if self.uuyoupin is not None:
            try:
                lease_item_list = []
                self.uuyoupin.send_device_info()
                self.logger.info("正在获取悠悠有品库存...")
                self.get_uu_inventory()

                for i, item in enumerate(self.inventory_list):
                    if item["AssetInfo"] is None:
                        continue
                    asset_id = item["SteamAssetId"]
                    item_id = item["TemplateInfo"]["Id"]
                    price = item["TemplateInfo"]["MarkPrice"]
                    if (
                            price < self.config["uu_auto_lease_item"]["filter_price"]
                            or (item["Tradable"] is False)
                            or item["AssetStatus"] != 0
                    ):
                        continue
                    self.operate_sleep()

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
                handle_caught_exception(e, "[UUAutoLeaseItem]")
                self.logger.error("悠悠有品出租出现错误")
                exit_code.set(1)
                return 1
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("出现未知错误, 稍后再试! ")
                try:
                    self.uuyoupin.get_user_nickname()
                except KeyError as e:
                    handle_caught_exception(e, "[UUAutoLeaseItem]")
                    self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                    self.logger.error("由于登录失败，插件将自动退出")
                    exit_code.set(1)
                    return 1

    def auto_change_price(self):
        self.logger.info("UU自动租赁修改价格插件已启动, 休眠5秒, 与自动接收报价插件错开运行时间")
        self.operate_sleep(5)

        try:
            self.uuyoupin.send_device_info()
            self.logger.info("正在获取悠悠有品已上架物品...")
            self.get_uu_leased_inventory()

            new_leased_item_list = []
            for i, item in enumerate(self.leased_inventory_list):
                asset_id = item["id"]
                item_id = item["templateId"]
                price = float(item["referencePrice"][1:])
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
            self.operate_sleep()
            self.change_leased_price(new_leased_item_list)

        except TypeError as e:
            handle_caught_exception(e, "[UUAutoLeaseItem]")
            self.logger.error("悠悠有品出租出现错误")
            exit_code.set(1)
            return 1
        except Exception as e:
            self.logger.error(e, exc_info=True)
            self.logger.info("出现未知错误, 稍后再试! ")
            try:
                self.uuyoupin.get_user_nickname()
            except KeyError as e:
                handle_caught_exception(e, "[UUAutoLeaseItem]")
                self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                self.logger.error("由于登录失败，插件将自动退出")
                exit_code.set(1)
                return 1

    def exec(self):
        self.logger.info("run func exec.")
        self.auto_lease()
        self.auto_change_price()

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


if __name__ == "__main__":
    # 调试代码
    with open("config/config.json5", "r", encoding="utf-8") as f:
        config = json5.load(f)

    uu_auto_lease = UUAutoLeaseItem(config)
    uu_auto_lease.auto_lease()
