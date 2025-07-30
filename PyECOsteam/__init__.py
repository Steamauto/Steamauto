import json
import logging
import time

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone

import PyECOsteam.models as models
from PyECOsteam.sign import generate_rsa_signature
from utils.logger import PluginLogger
from utils.models import Asset, LeaseAsset
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
        self.logger.debug(f"POST {api} {json.dumps(data,ensure_ascii=False)} {resp.text}")
        # if not resp.ok:
        #     raise Exception(f"POST {api} {data} {resp.text}")
        resp_json = resp.json()
        if "ResultCode" in resp_json:
            if resp_json["ResultCode"] != "0":
                if (
                    api == '/Api/Selling/OffshelfGoods'
                    and resp.text == '{"ResultCode":"1","ResultMsg":"操作失败","ResultData":false}'
                ):
                    self.logger.warning(f"下架操作出现异常,可能是因为商品已经下架,一般不影响程序运行,请忽略")
                else:
                    raise Exception(f"{resp.text}")
        return resp

    def GetTotalMoney(self):
        return self.post("/Api/Merchant/GetTotalMoney", {})

    def GetSellerOrderList(self, StartTime, EndTime, DetailsState=None, PageIndex=1, PageSize=100, SteamId=None):
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

    def getFullSellerOrderList(self, StartTime, EndTime, DetailsState=None, SteamId=None) -> list:
        index = 1
        orders = list()
        while True:
            res = self.GetSellerOrderList(StartTime, EndTime, DetailsState, PageIndex=index, SteamId=SteamId).json()
            if res["ResultCode"] != "0":
                raise Exception(res["ResultMsg"])
            elif res["ResultData"]["PageResult"] == []:
                break
            else:
                index += 1
                orders += res["ResultData"]["PageResult"]
                if len(res["ResultData"]["PageResult"]) < 100:
                    break
        return orders

    def GetSellerOrderDetail(self, OrderNum=None, MerchantNo=None):
        return self.post(
            "/Api/open/order/SellerOrderDetail",
            {"OrderNum": OrderNum, "MerchantNo": MerchantNo},
        )

    def GetSellGoodsList(self, PageIndex=1, PageSize=100, steam_id=None):
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
                if len(res["ResultData"]["PageResult"]) < 100:
                    break
        return goods

    def OffshelfGoods(self, goodsNumList: list[models.GoodsNum]):
        batches = [goodsNumList[i : i + 100] for i in range(0, len(goodsNumList), 100)]
        success_count = 0
        for batch in batches:
            rsp = self.post(
                "/Api/Selling/OffshelfGoods",
                data={"goodsNumList": [goodNum.model_dump(exclude_none=True) for goodNum in batch]},
            )
            for good in rsp.json()['ResultData']:
                if good['IsSuccess']:
                    success_count += 1
                else:
                    self.logger.error('下架在售商品时出现异常！错误信息：' + good['ErrorMsg'])
        failure_count = len(goodsNumList) - success_count
        return success_count, failure_count

    # def GoodsPublishedBatchEdit(self, goodsBatchEditList: list):
    #     return self.post("/Api/Selling/GoodsPublishedBatchEdit", data={"goodsBatchEditList": goodsBatchEditList})

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

    def OffshelfRentGoods(self, GoodsNumList: list[models.GoodsNum]):
        return self.post(
            "/Api/Rent/OffshelfRentGoods",
            data={"goodsNumList": [goodsNum.model_dump(exclude_none=True) for goodsNum in GoodsNumList]},
        )

    def QuerySelfRentGoods(self, steam_id, SteamGameId='730', State=1, PageIndex=1, PageSize=100, ShowType=0):
        return self.post(
            "/Api/Rent/QuerySelfRentGoods",
            data={
                "SteamId": steam_id,
                "SteamGameId": SteamGameId,
                "State": State,
                "PageIndex": PageIndex,
                "PageSize": PageSize,
                "ShowType": ShowType,
            },
        )

    def getFulRentGoodsList(self, steam_id) -> list[LeaseAsset]:
        index = 1
        goods = list()
        while True:
            res = self.QuerySelfRentGoods(steam_id, PageIndex=index).json()
            if res["ResultCode"] != "0":
                raise Exception(res["ResultMsg"])
            elif res["ResultData"]["PageResult"] == []:
                break
            else:
                index += 1
                goods += res["ResultData"]["PageResult"]
                if len(res["ResultData"]["PageResult"]) < 100:
                    break
        lease_assets = list()
        for good in goods:
            lease_assets.append(
                LeaseAsset(
                    assetid=good["AssetId"],
                    orderNo=good["GoodsNum"],
                    LeaseMaxDays=good["RentMaxDay"],
                    LeaseUnitPrice=good["Price"],
                    LeaseDeposit=good["Deposits"],
                    LongLeaseUnitPrice=good["LongRentPrice"],
                    market_hash_name=good['GoodsName'],
                )
            )
        return lease_assets

    def PublishRentAndSaleGoods(self, steamid, publishType, sell_assets: list[Asset] = [], lease_assets: list[LeaseAsset] = []):
        """
        :param publishType: 1-发布上架 2-改价上架
        请求示例：{
        "SteamId": "",
        "PublishType": {},
        "Assets": [
            {
            "AssetId": "",
            "SteamGameId": "",
            "TradeTypes": [
            ],
            "SellPrice": 0,
            "SellDescription": "",
            "RentMaxDay": 0,
            "RentPrice": 0,
            "LongRentPrice": 0,
            "RentDeposits": 0,
            "RentDescription": ""
            }
        ],
        "PartnerId": "",
        "Timestamp": "",
        "Sign": ""
        }
        """
        assets = []
        sell_assets_dict = dict({asset.assetid: asset for asset in sell_assets})
        lease_assets_dict = dict({asset.assetid: asset for asset in lease_assets})
        sell_lease_assets_id = set(sell_assets_dict.keys()) & set(lease_assets_dict.keys())
        # 若传入两个可选参数，则去重并合并
        for asset_id in sell_lease_assets_id:
            rsp_asset = {
                "AssetId": asset_id,
                "SteamGameId": sell_assets_dict[asset_id].appid,
                "TradeTypes": [1, 2],
                "SellPrice": sell_assets_dict[asset_id].price,
                "RentMaxDay": lease_assets_dict[asset_id].LeaseMaxDays,
                "RentPrice": lease_assets_dict[asset_id].LeaseUnitPrice,
                "RentDeposits": lease_assets_dict[asset_id].LeaseDeposit,
            }
            if lease_assets_dict[asset_id].LongLeaseUnitPrice:
                rsp_asset["LongRentPrice"] = lease_assets_dict[asset_id].LongLeaseUnitPrice
            del sell_assets_dict[asset_id]
            del lease_assets_dict[asset_id]
            assets.append(rsp_asset)

        for rsp_asset in sell_assets_dict.values():
            assets.append(models.ECOPublishStockAsset.fromAsset(rsp_asset).model_dump(exclude_none=True))

        for rsp_asset in lease_assets_dict.values():
            assets.append(models.ECORentAsset.fromLeaseAsset(rsp_asset).model_dump(exclude_none=True))

        batches = [assets[i : i + 100] for i in range(0, len(assets), 100)]
        change_reonshelf_list = []
        success_count = 0
        for batch in batches:
            rsp = self.post(
                "/Api/Rent/PublishRentAndSaleGoods", {"SteamId": steamid, "PublishType": publishType, "Assets": batch}
            ).json()
            for rsp_asset in rsp['ResultData']:
                if not rsp_asset['IsSuccess']:
                    if '已上架' in rsp_asset['ErrorMsg'] and publishType == 1:
                        self.logger.warning(
                            f"资产编号: {rsp_asset['AssetId']} 可能已经在租赁/出售货架上架(通常为可租可售商品需要以此方式上架) 稍后通过改价方式上架"
                        )
                        for asset in assets:
                            if asset['AssetId'] == rsp_asset['AssetId']:
                                change_reonshelf_list.append(asset)
                    else:
                        self.logger.error(f"有商品上架失败！资产编号: {rsp_asset['AssetId']} 错误信息:{rsp_asset['ErrorMsg']}")
                else:
                    success_count += 1
        if change_reonshelf_list:
            self.logger.info(f"即将通过改价方式上架{len(change_reonshelf_list)}个商品")
            sell_shelf = self.getFullSellGoodsList(steamid)
            lease_shelf = self.getFulRentGoodsList(steamid)
            for asset in change_reonshelf_list:
                if asset['TradeTypes'][0] == 1:
                    for lease_asset in lease_shelf:
                        if lease_asset.assetid == asset['AssetId']:
                            asset['RentPrice'] = lease_asset.LeaseUnitPrice
                            asset['RentDeposits'] = lease_asset.LeaseDeposit
                            asset['RentMaxDay'] = lease_asset.LeaseMaxDays
                            if lease_asset.LongLeaseUnitPrice:
                                asset['LongRentPrice'] = lease_asset.LongLeaseUnitPrice
                            break
                elif asset['TradeTypes'][0] == 2:
                    for sell_asset in sell_shelf:
                        if sell_asset['AssetId'] == asset['AssetId']:
                            asset['SellPrice'] = sell_asset['Price']
                            break
                asset['TradeTypes'] = [1, 2]
            batches = [change_reonshelf_list[i : i + 100] for i in range(0, len(change_reonshelf_list), 100)]
            for batch in batches:
                rsp = self.post(
                    "/Api/Rent/PublishRentAndSaleGoods", {"SteamId": steamid, "PublishType": 2, "Assets": batch}
                ).json()
                for rsp_asset in rsp['ResultData']:
                    if not rsp_asset['IsSuccess']:
                        self.logger.error(f"有商品上架失败！资产编号: {rsp_asset['AssetId']} 错误信息:{rsp_asset['ErrorMsg']}")
                    else:
                        success_count += 1
        failure_count = len(assets) - success_count
        return success_count, failure_count

    def SellerSendOffer(self ,OrderNum, GameId=730):
        """
        卖家订单发送报价

        :param OrderNum: 订单号
        :param GameId: 游戏ID,默认为CSGO
        :return:
        """
        return self.post("/Api/open/order/SellerSendOffer", {"OrderNum": OrderNum, "GameId": GameId})