import json
import logging
import time

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone

from PyECOsteam.sign import generate_rsa_signature
from utils.logger import PluginLogger
from utils.static import CURRENT_VERSION
from utils.tools import jobHandler


class ECOsteamClient:
    # https://openapi.ecosteam.cn/index.html/ 查看API文档
    def __rps_counter(self):
        try:
            self.rps = 0
        except RuntimeError:
            pass

    def __init__(self, partnerId, RSAKey, qps=10) -> None:
        self.logger = PluginLogger("ECOsteam.cn")
        self.partnerId = partnerId
        self.RSAKey = RSAKey
        self.qps = qps
        self.rps = 0
        logging.getLogger("apscheduler").propagate = False
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
        scheduler = BackgroundScheduler(timezone=timezone("Asia/Shanghai"))
        jobHandler.add(scheduler.add_job(self.__rps_counter, "interval", seconds=1))
        scheduler.start()

    def post(self, api: str, data: dict):
        data["PartnerId"] = self.partnerId
        data["Timestamp"] = int(time.time())
        data["Sign"] = generate_rsa_signature(self.RSAKey, data)
        if self.rps >= self.qps:
            time.sleep(1)
        self.rps += 1
        resp = requests.post(
            "https://openapi.ecosteam.cn" + api,
            data=json.dumps(data, indent=4),
            headers={"User-Agent": "Steamauto " + CURRENT_VERSION, "Content-Type": "application/json"},
        )
        data["Sign"] = "******"
        self.logger.debug(f"POST {api} {data} {resp.text}")
        if not resp.ok:
            raise Exception(f"POST {api} {data} {resp.text}")
        resp_json = resp.json()
        if "ResultCode" in resp_json:
            if resp_json["ResultCode"] != "0":
                raise Exception(f"POST {api} {data} {resp.text}")
        return resp

    def GetTotalMoney(self):
        return self.post("/Api/Merchant/GetTotalMoney", {})

    def GetSellerOrderList(self, StartTime, EndTime, DetailsState=None, PageIndex=1, PageSize=80, SteamId=None):
        return self.post(
            "/Api/open/order/SellerOrderList",
            {
                "StartTime": StartTime,
                "EndTime": EndTime,
                "DetailsState": DetailsState,
                "PageIndex": PageIndex,
                "PageSize": PageSize,
                "SteamId": SteamId,
            },
        )

    def GetSellerOrderDetail(self, OrderNum=None, MerchantNo=None):
        return self.post(
            "/Api/open/order/SellerOrderDetail",
            {"OrderNum": OrderNum, "MerchantNo": MerchantNo},
        )

    def GetSellGoodsList(self, PageIndex=1, PageSize=50, steam_id=None):
        return self.post(
            "/Api/Selling/GetSellGoodsList",
            {"PageIndex": PageIndex, "PageSize": PageSize, "SteamId": steam_id},
        )

    def getFullSellGoodsList(self, steam_id):
        index = 1
        goods = list()
        while True:
            res = self.GetSellGoodsList(PageIndex=index, steam_id=steam_id).json()
            if res["ResultCode"] != "0":
                raise Exception(res["ResultMsg"])
            elif res["ResultData"]["PageResult"] == []:
                break
            else:
                index += 1
                goods += res["ResultData"]["PageResult"]
                if len(res["ResultData"]["PageResult"]) < 50:
                    break
        return goods

    def PublishStock(self, assets: list):
        return self.post("/Api/Selling/PublishStock", data={"Assets": assets})

    def OffshelfGoods(self, goodsNumList: list):
        return self.post("/Api/Selling/OffshelfGoods", data={"goodsNumList": goodsNumList})

    def GoodsPublishedBatchEdit(self, goodsBatchEditList: list):
        return self.post("/Api/Selling/GoodsPublishedBatchEdit", data={"goodsBatchEditList": goodsBatchEditList})

    def QueryStock(self, index, PageSize=100):
        return self.post("/Api/Selling/QueryStock", data={"PageIndex": index, "PageSize": PageSize})

    def getFullInventory(self):
        index = 1
        inv = list()
        while True:
            res = self.QueryStock(index).json()
            if res["ResultCode"] != "0":
                raise Exception(res["ResultMsg"])
            elif res["ResultData"]["PageResult"] == []:
                break
            else:
                index += 1
                inv += res["ResultData"]["PageResult"]

    def searchStockIds(self, assetId: list):
        index = 1
        inv = dict()
        while True:
            res = self.QueryStock(index).json()
            if res["ResultCode"] != "0":
                raise Exception(res["ResultMsg"])
            elif res["ResultData"]["PageResult"] == []:
                break
            else:
                self.logger.debug(f'已经进行到第{index}次遍历,本次遍历获取到的库存数量为{len(res["ResultData"]["PageResult"])}')
                index += 1
                for item in res["ResultData"]["PageResult"]:
                    if item["AssetId"] in assetId:
                        inv[item["AssetId"]] = item["StockId"]
                        assetId.remove(item["AssetId"])
                        if assetId == []:
                            return inv

    def RefreshUserSteamStock(self):
        return self.post("/Api/Selling/RefreshUserSteamStock", data={})

    def QuerySteamAccountList(self):
        return self.post("/Api/Merchant/QuerySteamAccountList", data={})
