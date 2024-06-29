import os
import random
import time

import json5
import numpy as np
import schedule

import uuyoupinapi
from utils.logger import handle_caught_exception, PluginLogger
from utils.static import UU_TOKEN_FILE_PATH
from utils.tools import exit_code, get_encoding


class UUAutoLeaseItem:
    def __init__(self, config):
        self.logger = PluginLogger("UUAutoLeaseItem")
        self.uuyoupin = None
        self.config = config
        self.timeSleep = 3.0
        self.inventory_list = []

    def init(self) -> bool:
        if not os.path.exists(UU_TOKEN_FILE_PATH):
            with open(UU_TOKEN_FILE_PATH, "w", encoding="utf-8") as f:
                f.write("")
            return True
        return False

    def check_uu_account_state(self):

        with open(
            UU_TOKEN_FILE_PATH, "r", encoding=get_encoding(UU_TOKEN_FILE_PATH)
        ) as f:
            try:
                self.uuyoupin = uuyoupinapi.UUAccount(f.read())
                self.logger.info(
                    "悠悠有品登录完成, 用户名: " + self.uuyoupin.get_user_nickname()
                )
                self.uuyoupin.send_device_info()
            except Exception as e:
                handle_caught_exception(e, "[UUAutoAcceptOffer]")
                self.logger.error("悠悠有品登录失败! 请检查token是否正确! ")
                self.logger.error("由于登录失败，插件将自动退出")
                exit_code.set(1)
                return 1

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

    @staticmethod
    def filter_price(values):
        values = np.array(values)
        # 计算 Q1 和 Q3
        q1_dep = np.percentile(values, 25)
        q3_dep = np.percentile(values, 75)
        # 计算 IQR
        iqr_dep = q3_dep - q1_dep
        # 过滤掉异常值
        filtered_values = values[
            (values >= q1_dep - 1.5 * iqr_dep) & (values <= q3_dep + 1.5 * iqr_dep)
            ]
        return filtered_values

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

            valid_short_price_array = self.filter_price(lease_unit_price_list)
            valid_long_price_array = self.filter_price(long_lease_unit_price_list)
            valid_lease_deposit_array = self.filter_price(lease_deposit_list)
            lease_unit_price = np.mean(valid_short_price_array) * 0.93 - 0.01
            long_lease_unit_price = min(
                lease_unit_price * 0.92, np.mean(valid_long_price_array) * 0.92 - 0.01
            )
            lease_deposit = np.mean(valid_lease_deposit_array) * 0.95

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

    def post_process(self):
        with open(
            self.config["uu_auto_lease_item"]["uu_lease_item_cfg"],
            "w",
            encoding="utf-8",
        ) as f:
            json5.dump(self.config["lease_items"], f, indent=4, ensure_ascii=False)
        self.logger.info("post process done.")

    def auto_lease(self):
        self.logger.info("UU自动租赁上架插件已启动, 休眠3秒, 与自动接收报价插件错开运行时间")
        time.sleep(3)
        self.check_uu_account_state()

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
                        # assetId not in self.config["lease_items"].keys()
                        price < 100
                        or (item["Tradable"] == False)
                        or item["AssetStatus"] != 0
                    ):
                        continue
                    self.operate_sleep()

                    price_rsp = self.get_market_lease_price(item_id, min_price=price)
                    if price_rsp["LeaseUnitPrice"] == 0:
                        continue

                    lease_max_days = 60
                    remark = ""
                    if asset_id in self.config["lease_items"]:
                        saved_item = self.config["lease_items"][asset_id]
                        if "LeaseMaxDays" in saved_item:
                            lease_max_days = saved_item["LeaseMaxDays"]
                        if "Remark" in saved_item:
                            remark = saved_item["Remark"]

                    lease_item = {
                        "AssetId": asset_id,
                        "CompensationType": 0,
                        "IsCanLease": True,
                        "IsCanSold": False,
                        "LeaseMaxDays": lease_max_days,
                        "LeaseUnitPrice": price_rsp["LeaseUnitPrice"],
                        "LongLeaseUnitPrice": price_rsp["LongLeaseUnitPrice"],
                        "LeaseDeposit": price_rsp["LeaseDeposit"],
                        "OpenLeaseActivity": False,
                        "PrivateLeaseCommodity": 0,
                        "NomarlChargePercent": "0.25",
                        "Remark": remark,
                        "SupportZeroCD": 0,
                        "UseDepositSafeguard": 1,
                        "VipChargePercent": "0.2",
                        "VipSwitchStatus": 1,
                    }
                    lease_deposit = price_rsp["LeaseDeposit"]
                    # if "assetId" in self.config['lease_items']:
                    #     lease_deposit = min(self.config['lease_items']['LeaseDeposit'], lease_deposit)
                    self.config["lease_items"][asset_id] = {
                        "itemId": item_id,
                        "name": item["TemplateInfo"]["CommodityName"],
                        "LeaseMaxDays": lease_max_days,
                        "LeaseUnitPrice": price_rsp["LeaseUnitPrice"],
                        "LongLeaseUnitPrice": price_rsp["LongLeaseUnitPrice"],
                        "LeaseDeposit": lease_deposit,
                    }

                    lease_item_list.append(lease_item)

                self.logger.info(f"{len(lease_item_list)} item can lease.")

                self.post_process()

                self.operate_sleep()
                self.put_lease_item_on_shelf(lease_item_list)

            except TypeError as e:
                handle_caught_exception(e, "[UUAutoAcceptOffer]")
                self.logger.error("悠悠有品出租出现错误")
                exit_code.set(1)
                return 1
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("出现未知错误, 稍后再试! ")
                try:
                    self.uuyoupin.get_user_nickname()
                except KeyError as e:
                    handle_caught_exception(e, "[UUAutoAcceptOffer]")
                    self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                    self.logger.error("由于登录失败，插件将自动退出")
                    exit_code.set(1)
                    return 1

    def auto_accept(self):
        pass

    def exec(self):
        self.logger.info("run func exec.")
        self.logger.info(f"valid lease num: {len(self.config['lease_items'])}")
        self.logger.info(f"waiting run at 17:00.")

        schedule.every().day.at("17:00").do(self.auto_lease)
        # schedule.every(random.randint(1, 2)).minutes.do(self.auto_lease)

        while True:
            schedule.run_pending()
            time.sleep(1)

    def operate_sleep(self):
        time.sleep(self.timeSleep)


def load_config_index(path="config/config.json5"):
    with open(path, "r", encoding="utf-8") as f:
        config = json5.load(f)
    with open(
        config["uu_auto_lease_item"]["uu_lease_item_cfg"], "r", encoding="utf-8"
    ) as f:
        config["lease_items"] = json5.load(f)
    return config


if __name__ == "__main__":
    config = load_config_index()

    uu_auto_lease = UUAutoLeaseItem(config)
    uu_auto_lease.exec()
