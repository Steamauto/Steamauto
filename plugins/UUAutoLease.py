import os
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
        self.logger = PluginLogger("UUAutoLeaseItem")
        self.uuyoupin = None
        self.config = config
        self.timeSleep = 3.0
        self.inventory_list = []

    def init(self) -> bool:
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
        if inventory_list_rsp["Code"] == 0:
            inventory_list = inventory_list_rsp["Data"]["ItemsInfos"]
            self.logger.info(f"库存数量 {len(inventory_list)}")
        else:
            self.logger.error(inventory_list_rsp)
            self.logger.error("获取UU库存失败!")
            return []

        self.inventory_list = inventory_list

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

            lease_unit_price = np.mean(lease_unit_price_list) * 0.95
            long_lease_unit_price = min(
                lease_unit_price * 0.93, np.mean(long_lease_unit_price_list) * 0.95
            )
            lease_deposit = np.mean(lease_deposit_list) * 0.95

            lease_unit_price = max(lease_unit_price, 0.01)

            long_lease_unit_price = max(long_lease_unit_price, 0.01)

            self.logger.info(
                f"{commodity_name}, "
                f"lease_unit_price: {lease_unit_price:.2f}, long_lease_unit_price: {long_lease_unit_price:.2f}, "
                f"lease_deposit: {lease_deposit:.2f}"
            )
            self.logger.info(
                f"[DEBUG] lease_unit_price_list: {lease_unit_price_list}, "
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
                        or (item["Tradable"] == False)
                        or item["AssetStatus"] != 0
                    ):
                        continue
                    self.operate_sleep()

                    price_rsp = self.get_market_lease_price(item_id, min_price=price)
                    if price_rsp["LeaseUnitPrice"] == 0:
                        continue

                    lease_item = {
                        "AssetId": asset_id,
                        "CompensationType": 0,
                        "IsCanLease": True,
                        "IsCanSold": False,
                        "LeaseMaxDays": self.config["uu_auto_lease_item"]["lease_max_days"],
                        "LeaseUnitPrice": price_rsp["LeaseUnitPrice"],
                        "LongLeaseUnitPrice": price_rsp["LongLeaseUnitPrice"],
                        "LeaseDeposit": price_rsp["LeaseDeposit"],
                        "OpenLeaseActivity": False,
                        "PrivateLeaseCommodity": 0,
                        "NomarlChargePercent": "0.25",
                        "Remark": "",
                        "SupportZeroCD": 0,
                        "UseDepositSafeguard": 1,
                        "VipChargePercent": "0.2",
                        "VipSwitchStatus": 1,
                    }

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

    def exec(self):
        self.logger.info("run func exec.")

        run_time = self.config['uu_auto_lease_item']['run_time']
        self.logger.info(f"waiting run at {run_time}.")

        schedule.every().day.at(f"{run_time}").do(self.auto_lease)
        # schedule.every(random.randint(1, 2)).minutes.do(self.auto_lease)

        while True:
            schedule.run_pending()
            time.sleep(1)

    def operate_sleep(self):
        time.sleep(self.timeSleep)


if __name__ == "__main__":
    # 调试代码
    with open("config/config.json5", "r", encoding="utf-8") as f:
        config = json5.load(f)

    uu_auto_lease = UUAutoLeaseItem(config)
    uu_auto_lease.exec()
