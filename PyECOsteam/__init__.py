import logging
import time

from pytz import timezone
import requests
from apscheduler.schedulers.background import BackgroundScheduler

from PyECOsteam.sign import generate_rsa_signature


class ECOsteamClient:
    # https://openapi.ecosteam.cn/index.html/ 查看API文档
    def __rps_counter(self):
        self.rps = 0
    
    def __init__(self, partnerId, RSAKey, qps=10) -> None:
        self.partnerId = partnerId
        self.RSAKey = RSAKey
        self.qps = qps
        self.rps = 0
        logging.getLogger('apscheduler').propagate = False
        logging.getLogger('apscheduler').setLevel(logging.WARNING)
        scheduler = BackgroundScheduler(timezone=timezone('Asia/Shanghai'))
        scheduler.add_job(self.__rps_counter,'interval',seconds=1)
        scheduler.start()

    def post(self, api, data={}):
        data["PartnerId"] = self.partnerId
        data["Timestamp"] = int(time.time())
        data["Sign"] = generate_rsa_signature(self.RSAKey, data)
        if self.rps >= self.qps:
            time.sleep(1)
        self.rps += 1
        return requests.post("https://openapi.ecosteam.cn" + api, json=data)

    def GetTotalMoney(self):
        return self.post("/Api/Merchant/GetTotalMoney")

    def GetSellerOrderList(
        self, StartTime, EndTime, DetailsState=None, PageIndex=1, PageSize=80
    ):
        return self.post(
            "/Api/open/order/SellerOrderList",
            {
                "StartTime": StartTime,
                "EndTime": EndTime,
                "DetailsState": DetailsState,
                "PageIndex": PageIndex,
                "PageSize": PageSize,
            },
        )

    def GetSellGoodsList(self, PageIndex=1, PageSize=None):
        return self.post(
            "/Api/Selling/GetSellGoodsList",
            {"PageIndex": PageIndex, "PageSize": PageSize},
        )

    def GetSellerOrderDetail(self, OrderNum=None, MerchantNo=None):
        return self.post(
            "/Api/open/order/SellerOrderDetail",
            {"OrderNum": OrderNum, "MerchantNo": MerchantNo},
        )
