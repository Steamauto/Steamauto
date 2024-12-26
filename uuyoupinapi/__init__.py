import json
import random
import string
import time

import requests

from utils.logger import PluginLogger
from utils.models import Asset, LeaseAsset
from uuyoupinapi import models
from uuyoupinapi.models import UUMarketLeaseItem

logger = PluginLogger("uuyoupinapi")


def generate_random_string(length):
    """
    生成指定长度的字符串，包含 A-Z, a-z 和数字
    :param length: 字符串长度
    :return: 随机字符串
    """
    letters_and_digits = string.ascii_letters + string.digits
    return "".join(random.choice(letters_and_digits) for i in range(length))


def generate_device_info():
    return {
        "deviceId": generate_random_string(24),
        "deviceType": generate_random_string(6),
        "hasSteamApp": 0,
        "systemName ": "Android",
        "systemVersion": "14",
    }


def generate_headers(devicetoken, deviceid, token=""):
    return {
        "authorization": "Bearer " + token,
        "content-type": "application/json; charset=utf-8",
        "user-agent": "okhttp/3.14.9",
        "app-version": "5.26.1",
        "apptype": "4",
        "package-type": "uuyp",
        "devicetoken": devicetoken,
        "deviceid": deviceid,
        "platform": "android",
        "accept-encoding": "gzip",
    }

def is_json( data):
    try:
        json.loads(data)
    except Exception:
        return False
    return True
class UUAccount:
    def __init__(self, token: str):
        """
        :param token: 通过抓包获得的token
        """
        self.session = requests.Session()
        self.ignore_list = []
        random.seed(token)
        self.device_info = generate_device_info()
        self.session.headers.update(generate_headers(self.device_info["deviceId"], self.device_info["deviceId"], token=token))
        try:
            info = self.call_api("GET", "/api/user/Account/getUserInfo").json()
            self.nickname = info["Data"]["NickName"]
            self.userId = info["Data"]["UserId"]
        except KeyError:
            raise Exception("悠悠有品账号登录失败，请检查token是否正确")

    @staticmethod
    def __random_str(length):
        return "".join(random.sample(string.ascii_letters + string.digits, length))

    @staticmethod
    def get_smsUpSignInConfig(headers):
        return requests.get(
            "https://api.youpin898.com/api/user/Auth/GetSmsUpSignInConfig",
            headers=headers,
        )

    @staticmethod
    def send_login_sms_code(phone, session: str, headers={}, region_code=86):
        """
        发送登录短信验证码
        :param phone: 手机号
        :param session: 可以通过UUAccount.get_random_session_id()获得
        :return:
        """
        return requests.post(
            "https://api.youpin898.com/api/user/Auth/SendSignInSmsCode",
            json={"Area": region_code, "Mobile": phone, "Sessionid": session, "Code": ""},
            headers=headers,
        ).json()

    @staticmethod
    def sms_sign_in(phone, code, session, headers={}):
        """
        通过短信验证码登录，返回值内包含Token
        :param phone: 发送验证码时的手机号
        :param code: 短信验证码
        :param session: 可以通过UUAccount.get_random_session_id()获得，必须和发送验证码时的session一致
        :return:
        """
        if code == "":
            url = "https://api.youpin898.com/api/user/Auth/SmsUpSignIn"
        else:
            url = "https://api.youpin898.com/api/user/Auth/SmsSignIn"
        return requests.post(
            url,
            json={
                "Area": 86,
                "Code": code,
                "Sessionid": session,
                "Mobile": phone,
                "TenDay": 1,
            },
            headers=headers,
        ).json()

    def get_user_nickname(self):
        return self.nickname

    def send_device_info(self):
        return self.call_api(
            "GET",
            "/api/common/ClientInfo/AndroidInfo",
            data={
                "DeviceToken": self.device_info["deviceId"],
                "Sessionid": self.device_info["deviceId"],
            },
        )


    
    def call_api(self, method, path, data=None):
        """
        调用API
        :param method: GET, POST, PUT, DELETE
        :param path: 请求路径
        :param data: 发送的数据
        :return:
        """
        url = "https://api.youpin898.com" + path

        if method == "GET":
            response = self.session.get(url, params=data)
        elif method == "POST":
            response = self.session.post(url, json=data)
        elif method == "PUT":
            response = self.session.put(url, json=data)
        elif method == "DELETE":
            response = self.session.delete(url)
        else:
            raise Exception("Method not supported")
        log_output = response.content.decode()
        if is_json(log_output):
            json_output = json.loads(log_output)
            log_output = json.dumps(json_output, ensure_ascii=False)
            logger.debug(f"{method} {path} {json.dumps(data)} {log_output}")
            if json_output.get('code') == 84101:
                raise Exception('登录状态失效，请重新登录')
        else:
            raise Exception(f"网络错误，或服务器被悠悠屏蔽！请求失败！")
            
        return response

    def change_leased_price(self, items: list[LeaseAsset]):
        '''
        请求范例：
        {
            "Commoditys": [{
                "CommodityId": 819157345,
                "IsCanLease": true,
                "IsCanSold": true,
                "LeaseDeposit": "20000.0",
                "LeaseMaxDays": 30,
                "LeaseUnitPrice": 222,
                "LongLeaseUnitPrice": 20,
                "Price": 10,
            }],
            "Sessionid": "Zmhqhu7RG9gDAGrX...mYv4p"
        }
        返回范例：
        {
            "Code": 0,
            "Msg": "请求成功",
            "TipType": 10,
            "Data": {
                "SuccessCount": 1,
                "FailCount": 0,
                "Commoditys": [{
                    "CommodityId": 814953269,
                    "IsSuccess": 1,
                    "Message": null
                }]
            }
        }
        '''
        item_infos = list()
        for item in items:
            item_info = {
                "CommodityId": int(item.orderNo),  # type: ignore
                "IsCanLease": item.IsCanLease,
                "IsCanSold": item.IsCanSold,
                "LeaseDeposit": item.LeaseDeposit,
                "LeaseMaxDays": item.LeaseMaxDays,
                "LeaseUnitPrice": item.LeaseUnitPrice,
            }
            if item.LongLeaseUnitPrice:
                item_info["LongLeaseUnitPrice"] = item.LongLeaseUnitPrice
            if item_info["IsCanSold"]:
                item_info["Price"] = item.price
            item_infos.append(item_info)

        rsp = self.call_api(
            "PUT",
            "/api/commodity/Commodity/PriceChangeWithLeaseV2",
            data={
                "Commoditys": item_infos,
                "Sessionid": self.device_info["deviceId"],
            },
        ).json()
        if rsp['Data']['FailCount'] != 0:
            for commodity in rsp["Data"]["Commoditys"]:
                if commodity["IsSuccess"] != 1:
                    logger.error(f"修改商品价格失败，商品ID：{commodity[id]}，原因：{commodity['Message']}")
        return rsp['Data']['SuccessCount']

    def send_offer(self, orderNo):
        rsp = self.call_api(
            'PUT',
            '/api/youpin/bff/trade/v1/order/sell/delivery/send-offer',
            data={'orderNo': orderNo, 'Sessionid': self.device_info["deviceId"]},
        ).json()
        if rsp['code'] == 0:
            return True
        else:
            return rsp['msg']

    def get_offer_status(self, orderNo):
        rsp = self.call_api(
            'POST',
            '/api/youpin/bff/trade/v1/order/sell/delivery/get-offer-status',
            data={'orderNo': orderNo, 'Sessionid': self.device_info["deviceId"]},
        ).json()
        if rsp['code'] == 0:
            return rsp
        else:
            return rsp['msg']

    def get_wait_deliver_list(self, game_id=730, return_offer_id=True):
        """
        获取待发货列表
        :param return_offer_id: 默认为True，是否返回steam交易报价号
        :param game_id: 游戏ID，默认为730(CSGO)
        :return: 待发货列表，格式为[{'order_id': '订单号', 'item_name': '物品名称', 'offer_id': 'steam交易报价号'}... , ...]
        """
        toDoList_response = self.call_api(
            "POST",
            "/api/youpin/bff/trade/todo/v1/orderTodo/list",
            data={
                "userId": self.userId,
                "pageIndex": 1,
                "pageSize": 100,
                "Sessionid": self.device_info["deviceId"],
            },
        ).json()
        toDoList = dict()
        for order in toDoList_response["data"]:
            if order["orderNo"] in self.ignore_list:
                logger.debug("[UUAutoAcceptOffer] 订单号为" + order["orderNo"] + "的订单已经被忽略")
            elif order["message"] == "有买家下单，待您发送报价":
                logger.info(
                    f'[UUAutoAcceptOffer] 订单号为 {order["orderNo"]} 的订单({order["commodityName"]})为待发送报价订单，正在尝试发送报价...'
                )
                result = self.send_offer(order["orderNo"])
                if result == True:
                    logger.info(
                        f'[UUAutoAcceptOffer] 订单号为 {order["orderNo"]} 的订单({order["commodityName"]})的报价已经在发送，正在等待发送完成...'
                    )
                    for i in range(5):
                        result = self.get_offer_status(order["orderNo"])
                        if result['data']['status'] == 3:
                            logger.info(
                                f'[UUAutoAcceptOffer] 订单号为 {order["orderNo"]} 的订单({order["commodityName"]})的报价发送成功，将会在下一次轮询进行令牌确认'
                            )
                            break
                        if i == 4:
                            logger.warning(
                                f'[UUAutoAcceptOffer] 订单号为 {order["orderNo"]} 的订单({order["commodityName"]})的报价发送等待超时'
                            )
                            break
                        time.sleep(1.5)
                else:
                    logger.error(
                        f'[UUAutoAcceptOffer] 订单号为 {order["orderNo"]} 的订单({order["commodityName"]})发送报价失败，原因：{result}'
                    )
            else:
                toDoList[order["orderNo"]] = order
        data_to_return = []
        # 傻逼悠悠有3种获取报价ID的方式
        if len(toDoList.keys()) != 0:
            data = self.call_api(
                "POST",
                "/api/youpin/bff/trade/sale/v1/sell/list",
                data={
                    "keys": "",
                    "orderStatus": "140",
                    "pageIndex": 1,
                    "pageSize": 100,
                },
            ).json()["data"]
            for order in data["orderList"]:
                if int(order["offerType"]) == 2:
                    if order["tradeOfferId"] is not None:
                        if order["orderNo"] in toDoList.keys():
                            del toDoList[order["orderNo"]]
                        data_to_return.append(
                            {
                                "offer_id": order["tradeOfferId"],
                                "item_name": order["productDetail"]["commodityName"],
                            }
                        )
        if len(toDoList.keys()) != 0:
            for order in list(toDoList.keys()):
                time.sleep(3)
                orderDetail = self.call_api(
                    "POST",
                    "/api/youpin/bff/order/v2/detail",
                    data={
                        "orderId": order,
                        "Sessionid": self.device_info["deviceId"],
                    },
                ).json()
                if orderDetail["data"] and 'orderDetail' in orderDetail["data"]:
                    orderDetail = orderDetail["data"]["orderDetail"]
                    if 'offerId' in orderDetail:
                        data_to_return.append(
                            {
                                "offer_id": orderDetail["offerId"],
                                "item_name": orderDetail["productDetail"]["commodityName"],
                            }
                        )
                        if order in toDoList.keys():
                            del toDoList[order]
        if len(toDoList.keys()) != 0:
            for order in list(toDoList.keys()):
                orderDetail = self.call_api(
                    "POST",
                    "/api/youpin/bff/trade/v1/order/query/detail",
                    data={
                        "orderNo": order,
                        "Sessionid": self.device_info["deviceId"],
                    },
                ).json()
                orderDetail = orderDetail["data"]
                if orderDetail and 'tradeOfferId' in orderDetail:
                    data_to_return.append(
                        {
                            "offer_id": orderDetail["tradeOfferId"],
                            "item_name": orderDetail["commodity"]["name"],
                        }
                    )
                    if order in toDoList.keys():
                        del toDoList[order]
        if len(toDoList.keys()) != 0:
            logger.warning(
                "[UUAutoAcceptOffer] 有订单未能获取到Steam交易报价号，订单号为：" + str(toDoList.keys()),
            )
        return data_to_return

    def get_sell_list(self):
        data = {"pageIndex": 0, "pageSize": 100, "whetherMerge": 0}
        shelf = list()
        while True:
            data["pageIndex"] += 1
            response = self.call_api("POST", "/api/youpin/bff/new/commodity/v1/commodity/list/sell", data=data)
            if response.json()["code"] != 0:
                break
            else:
                for item in response.json()["data"]["commodityInfoList"]:
                    if "steamAssetId" in item:
                        shelf.append(item)
        return shelf

    def put_items_on_lease_shelf(self, item_infos: list[models.UUOnLeaseShelfItem], GameId=730):
        '''
        请求范例：
        {
            "AppType": 3,
            "AppVersion": "5.20.1",
            "GameId": 730,
            "ItemInfos": [{
                "AssetId": 38872746818,
                "IsCanLease": true,
                "IsCanSold": false,
                "LeaseDeposit": "30000.0",
                "LeaseMaxDays": 30,
                "LeaseUnitPrice": 10,
                "LongLeaseUnitPrice": 10,
            }],
            "Sessionid": "..."
        }
        返回范例：
        {
            "Code": 0,
            "Msg": "成功",
            "TipType": 10,
            "Data": [{
                "AssetId": 38872746818,
                "CommodityId": 814835547,
                "CommodityNo": "2024082046...90528",
                "Status": 1,
                "Remark": ""
            }]
        }
        '''
        lease_on_shelf_rsp = self.call_api(
            "POST",
            "/api/commodity/Inventory/SellInventoryWithLeaseV2",
            data={
                "GameId": GameId,
                "itemInfos": [item.model_dump(exclude_none=True) for item in item_infos],
                "Sessionid": self.device_info["deviceId"],
            },
        ).json()
        success_count = 0
        for asset in lease_on_shelf_rsp["Data"]:
            if asset["Status"] == 1:
                success_count += 1
            else:
                logger.error(f"上架物品 {asset['AssetId']}(AssetId) 失败，原因：{asset['Remark']}")
        return success_count

    def get_uu_leased_inventory(self, pageIndex=1, pageSize=100) -> list[LeaseAsset]:
        new_leased_inventory_list = self.get_one_channel_leased_inventory(
            "/api/youpin/bff/new/commodity/v1/commodity/list/lease", pageIndex, pageSize
        )
        zero_leased_inventory_list = self.get_one_channel_leased_inventory(
            "/api/youpin/bff/new/commodity/v1/commodity/list/zeroCDLease", pageIndex, pageSize
        )
        return new_leased_inventory_list + zero_leased_inventory_list

    def get_one_channel_leased_inventory(self, path, pageIndex=1, pageSize=100) -> list[LeaseAsset]:
        rsp = self.call_api(
            "POST",
            path,
            data={
                "pageIndex": pageIndex,
                "pageSize": pageSize,
                "whetherMerge": 0,
                "Sessionid": self.device_info["deviceId"],
            },
        ).json()
        leased_inventory_list = []
        if rsp['code'] == 0:
            for item in rsp["data"]["commodityInfoList"]:
                leased_inventory_list.append(
                    LeaseAsset(
                        assetid=str(item['steamAssetId']),
                        templateid=item['templateId'],
                        short_name=item['name'],
                        LeaseDeposit=float(item['depositAmount']),
                        LeaseUnitPrice=float(item['shortLeaseAmount']),
                        LongLeaseUnitPrice=float(item['longLeaseAmount']) if item['longLeaseAmount'] else float(0),
                        LeaseMaxDays=item['leaseMaxDays'],
                        IsCanSold=bool(item['commodityCanSell']),
                        IsCanLease=bool(item['commodityCanLease']),
                        orderNo=item['id'],
                        price=float(item['referencePrice'][1:])
                    )
                )
        elif rsp["code"] == 9004001:
            pass
        else:
            raise Exception("获取悠悠租赁已上架物品失败!")
        # logger.info(f"上架数量 {len(leased_inventory_list)}")
        return leased_inventory_list

    def get_inventory(self,refresh = False):
        data_to_send = {
                "pageIndex": 1,
                "pageSize": 1000,
                "AppType": 4,
                "IsMerge": 0,
                "Sessionid": self.device_info["deviceId"],
            }
        if refresh:
            data_to_send['IsRefresh'] = True
            data_to_send['RefreshType'] = 2
        inventory_list_rsp = self.call_api(
            "POST",
            "/api/commodity/Inventory/GetUserInventoryDataListV3",
            data=data_to_send,
        ).json()
        inventory_list = []
        if inventory_list_rsp["Code"] == 0:  # 我他妈真是服了你了悠悠，Code的C一会儿大写一会儿小写
            inventory_list = inventory_list_rsp["Data"]["ItemsInfos"]
            logger.info(f"库存数量 {len(inventory_list)}")
        else:
            logger.error(inventory_list_rsp)
            logger.error("获取悠悠库存失败!")

        return inventory_list

    def get_market_lease_price(self, template_id: int, min_price=0, max_price=20000, cnt=15,sortTypeKey='LEASE_DEFAULT') -> list[UUMarketLeaseItem]:
        rsp = self.call_api(
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
                "sortTypeKey": sortTypeKey,
                "status": "20",
                "stickerAbrade": 0,
                "stickersIsSort": False,
                "templateId": f"{template_id}",
                "ultraLongLeaseMoreZones": 0,
                "userId": self.userId,
                "Sessionid": self.device_info["deviceId"],
            },
        ).json()
        lease_list = []
        if rsp["Code"] == 0:
            rsp_list = rsp["Data"]["CommodityList"]
            rsp_cnt = len(rsp_list)
            cnt = min(cnt, rsp_cnt)
            for i in range(cnt):
                item = rsp_list[i]
                if item["LeaseDeposit"] and min_price < float(item["LeaseDeposit"]) < max_price:
                    lease_list.append(
                        UUMarketLeaseItem(
                            LeaseUnitPrice=item["LeaseUnitPrice"] if item["LeaseUnitPrice"] else None,
                            LongLeaseUnitPrice=item["LongLeaseUnitPrice"] if item["LongLeaseUnitPrice"] else None,
                            LeaseDeposit=item["LeaseDeposit"] if item["LeaseDeposit"] else None,
                            CommodityName=item["CommodityName"],
                        )
                    )
        else:
            logger.error(f"查询出租价格失败，返回结果：{rsp['Code']}，全部内容：{rsp}")
        return lease_list

    def off_shelf(self, commodity_ids: list):  # 这个API出售和租赁都能用。。。不知道悠悠程序员是怎么想的
        return self.call_api(
            "PUT",
            "/api/commodity/Commodity/OffShelf",
            data={
                "Ids": ",".join([str(id) for id in commodity_ids]),
                "IsDeleteCommodityCache": 1,
                "IsForceOffline": True,
            },
        )

    def sell_items(self, assets: dict):
        item_infos = [{"AssetId": asset, "Price": assets[asset], "Remark": None} for asset in assets.keys()]
        rsp = self.call_api(
            "POST",
            "/api/commodity/Inventory/SellInventoryWithLeaseV2",
            data={
                "GameID": 730,
                "ItemInfos": item_infos,
            },
        ).json()
        success_count = 0
        for commodity in rsp["Data"]:
            if commodity["Status"] != 1:
                if '不能重复上架' not in commodity['Remark']:
                    logger.error(f"商品 {commodity['AssetId']} 上架失败，原因：{commodity['Remark']}")
                else:
                    logger.warning(f"商品 {commodity['AssetId']} 可能因为悠悠服务器延迟而导致程序重复上架")
            else:
                success_count += 1
        return success_count

    def change_price(self, assets: dict):
        item_infos = [
            {"CommodityId": int(asset), "Price": str(assets[asset]), "Remark": None, "IsCanSold": True} for asset in assets.keys()
        ]
        return self.call_api(
            "PUT",
            "/api/commodity/Commodity/PriceChangeWithLeaseV2",
            data={"Commoditys": item_infos},
        )

    def onshelf_sell_and_lease(self, sell_assets: list[Asset] = [], lease_assets: list[LeaseAsset] = []):
        '''
        请求范例：
        {
            "AppType": 3,
            "AppVersion": "5.20.1",
            "GameId": 730,
            "ItemInfos": [{
                "AssetId": 38872746818,
                "IsCanLease": true,
                "IsCanSold": false,
                "LeaseDeposit": "30000.0",
                "LeaseMaxDays": 30,
                "LeaseUnitPrice": 10,
                "LongLeaseUnitPrice": 10,
                "Price": 10000,
            }],
            "Sessionid": "..."
        }
        返回范例：
        {
            "Code": 0,
            "Msg": "成功",
            "TipType": 10,
            "Data": [{
                "AssetId": 38872746818,
                "CommodityId": 814835547,
                "CommodityNo": "2024082046...90528",
                "Status": 1,
                "Remark": ""
            }]
        }
        '''
        item_infos = []
        sell_assets_dict = dict({asset.assetid: asset for asset in sell_assets})
        lease_assets_dict = dict({asset.assetid: asset for asset in lease_assets})
        sell_lease_assets_id = set(sell_assets_dict.keys()) & set(lease_assets_dict.keys())
        # 若传入两个可选参数，则去重并合并
        for asset_id in sell_lease_assets_id:
            item_info = {
                "AssetId": asset_id,
                "IsCanLease": True,
                "IsCanSold": True,
                "LeaseDeposit": str(lease_assets_dict[asset_id].LeaseDeposit),
                "LeaseMaxDays": lease_assets_dict[asset_id].LeaseMaxDays,
                "LeaseUnitPrice": lease_assets_dict[asset_id].LeaseUnitPrice,
                "Price": sell_assets_dict[asset_id].price,
            }
            if lease_assets_dict[asset_id].LongLeaseUnitPrice:
                item_info["LongLeaseUnitPrice"] = lease_assets_dict[asset_id].LongLeaseUnitPrice
            item_infos.append(item_info)
            del sell_assets_dict[asset_id]
            del lease_assets_dict[asset_id]

        item_infos += [models.UUOnSellShelfItem.fromAsset(asset).model_dump(exclude_none=True) for asset in sell_assets_dict.values()]
        item_infos += [models.UUOnLeaseShelfItem.fromLeaseAsset(asset).model_dump(exclude_none=True) for asset in lease_assets_dict.values()]
        
        batches = [item_infos[i:i+50] for i in range(0, len(item_infos), 50)]
        change_price_onshelf_list = []
        success_count = 0
        for batch in batches:
            rsp = self.call_api(
                "POST",
                "/api/commodity/Inventory/SellInventoryWithLeaseV2",
                data={
                    "GameId": 730,
                    "ItemInfos": batch,
                    "Sessionid": self.device_info["deviceId"],
                },
            ).json()
            for asset in rsp["Data"]:
                if asset["Status"] == 1:
                    success_count += 1
                else:
                    if '不能重复上架' in asset['Remark']:
                        logger.warning(f"上架物品 {asset['AssetId']} 可能已经在租赁/出售货架上架(通常为可租可售商品需要以此方式上架) 稍后通过改价方式上架")
                        for item in batch:
                            if item['AssetId'] == asset['AssetId']:
                                change_price_onshelf_list.append(item)
                                break
                    else:
                        logger.error(f"上架物品 {asset['AssetId']} 失败，原因：{asset['Remark']}")
        if change_price_onshelf_list:
            
            logger.info(f'即将通过改价方式上架{len(change_price_onshelf_list)}个物品')
            sell_shelf = self.get_sell_list()
            lease_shelf = self.get_uu_leased_inventory()
            for asset in change_price_onshelf_list:
                if asset['IsCanSold']:
                    for lease_asset in lease_shelf:
                        if asset['AssetId'] == int(lease_asset.assetid):
                            asset['CommodityId'] = lease_asset.orderNo
                            asset['LeaseDeposit'] = str(lease_asset.LeaseDeposit)
                            asset['LeaseMaxDays'] = lease_asset.LeaseMaxDays
                            asset['LeaseUnitPrice'] = lease_asset.LeaseUnitPrice
                            if lease_asset.LongLeaseUnitPrice:
                                asset['LongLeaseUnitPrice'] = lease_asset.LongLeaseUnitPrice
                            asset['IsCanLease'] = True
                            del asset['AssetId']
                            break
                elif asset['IsCanLease']:
                    for sell_asset in sell_shelf:
                        if asset['AssetId'] == int(sell_asset['steamAssetId']):
                            asset['CommodityId'] = sell_asset['id']
                            asset['Price'] = sell_asset['price']
                            asset['IsCanSold'] = True
                            del asset['AssetId']
                            break
            batches = [change_price_onshelf_list[i:i+50] for i in range(0, len(change_price_onshelf_list), 50)]
            for batch in batches:
                rsp = self.call_api(
                    "PUT",
                    "/api/commodity/Commodity/PriceChangeWithLeaseV2",
                    data={
                        "Commoditys": batch,
                        "Sessionid": self.device_info["deviceId"],
                    },
                ).json()
                try:
                    for asset in rsp["Data"]['Commoditys']:
                        if asset["IsSuccess"] == 1:
                            success_count += 1
                        else:
                            logger.error(f"上架物品 {asset['CommodityId']}(悠悠商品编号) 失败，原因：{asset['Remark']}")
                except TypeError:
                    logger.error(f"上架物品失败，原因可能由于该物品处于待发货列表")
        failure_count = len(item_infos) - success_count
        return success_count, failure_count
    
    def change_price_sell_and_lease(self, sell_assets: list[Asset] = [], lease_assets: list[LeaseAsset] = []):
        '''
        请求示例：
        {
            "Commoditys": [{
                "CommodityId": 819475347,
                "IsCanLease": true,
                "IsCanSold": true,
                "LeaseDeposit": "100000.0",
                "LeaseMaxDays": 30,
                "LeaseUnitPrice": 100,
                "LongLeaseUnitPrice": 50,
                "Price": 90,
            }],
            "Sessionid": "Zm...4p"
        }
        返回示例：
        {
            "Code": 0,
            "Msg": "请求成功",
            "TipType": 10,
            "Data": {
                "SuccessCount": 1,
                "FailCount": 0,
                "Commoditys": [{
                    "CommodityId": 819475347,
                    "IsSuccess": 1,
                    "Message": null
                }]
            }
        }
        '''
        item_infos = []
        sell_assets_dict = dict({asset.assetid: asset for asset in sell_assets})
        lease_assets_dict = dict({asset.assetid: asset for asset in lease_assets})
        sell_lease_commodityID = set(sell_assets_dict.keys()) & set(lease_assets_dict.keys())
        # 若传入两个可选参数，则去重并合并
        for id in sell_lease_commodityID:
            item_info = {
                "CommodityId": id,
                "IsCanLease": True,
                "IsCanSold": True,
                "LeaseDeposit": str(lease_assets_dict[id].LeaseDeposit),
                "LeaseMaxDays": lease_assets_dict[id].LeaseMaxDays,
                "LeaseUnitPrice": lease_assets_dict[id].LeaseUnitPrice,
                "Price": sell_assets_dict[id].price,
            }
            if lease_assets_dict[id].LongLeaseUnitPrice:
                item_info["LongLeaseUnitPrice"] = lease_assets_dict[id].LongLeaseUnitPrice
            item_infos.append(item_info)
            del sell_assets_dict[id]
            del lease_assets_dict[id]

        item_infos += [models.UUChangePriceItem.fromAsset(asset).model_dump(exclude_none=True) for asset in sell_assets_dict.values()]
        item_infos += [models.UUChangePriceItem.fromLeaseAsset(asset).model_dump(exclude_none=True) for asset in lease_assets_dict.values()]
        
        batches = [item_infos[i:i+50] for i in range(0, len(item_infos), 50)]
        success_count = 0
        for batch in batches:
            rsp = self.call_api(
                "PUT",
                "/api/commodity/Commodity/PriceChangeWithLeaseV2",
                data={
                    "Commoditys": batch,
                    "Sessionid": self.device_info["deviceId"],
                },
            ).json()
            for asset in rsp["Data"]['Commoditys']:
                if asset["IsSuccess"] == 1:
                    success_count += 1
                else:
                    logger.error(f"上架物品 {asset['CommodityId']}(悠悠商品编号) 失败，原因：{asset['Remark']}")
        failure_count = len(item_infos) - success_count
        return success_count, failure_count
    
    def get_leased_out_list(self):
        data = {"gameId": 730, "pageIndex": 0, "pageSize": 50,"sortType": 0,"keywords":""}
        result = []
        while True:
            data['pageIndex'] += 1
            response = self.call_api("POST", "/api/youpin/bff/trade/v1/order/lease/out/list", data=data).json()
            result += response["data"]["orderDataList"]
            if len(response["data"]["orderDataList"]) < 50:
                break 
        return result
    
    def get_template_id_by_order_id(self, order_id):
        response = self.call_api("POST", "/api/youpin/bff/order/v2/detail", data={"orderId": order_id}).json()
        return response['data']['orderDetail']['productDetail']['commodityTemplateId']
    
    def get_least_market_price(self, template_id):
        response = self.call_api("POST", "/api/homepage/v2/detail/commodity/list/sell", data={"templateId": template_id}).json()
        if response['Code'] == 84104:
            raise SystemError('悠悠风控，暂时无法获取价格')
        try:
            return response['Data']['CommodityList'][0]['Price']
        except:
            return 0

    def get_trend_inventory(self):
        inventory_list_rsp = self.call_api(
            "POST",
            "/api/youpin/commodity/user/inventory/price/trend",
            data={
                "pageIndex": 1,
                "pageSize": 1000,
                "IsMerge": 0
            },
        ).json()
        inventory_list = []
        if inventory_list_rsp["code"] == 0:
            inventory_list = inventory_list_rsp["data"]["itemsInfos"]
            logger.info(f"库存数量 {len(inventory_list)}")
        else:
            logger.error(inventory_list_rsp)
            logger.error("获取悠悠库存失败!")
        return inventory_list

    def save_buy_price(self, assets: list):
        """
        {"productUniqueKeyList":[{"steamAssetId":"39605491748","marketHashName":"USP-S | Printstream (Minimal Wear)",
        "buyPrice":"341","abrade":"0.1401326358318328900"}]}
        """
        item_infos = [
            {
                "steamAssetId": str(asset["steamAssetId"]),
                "marketHashName": asset["marketHashName"],
                "buyPrice": str(asset["buyPrice"]),
                "abrade": str(asset["abrade"])
            }
            for asset in assets
        ]
        rsp = self.call_api(
            "POST",
            "/api/youpin/commodity/product/user/batch/save/buy/price",
            data={"productUniqueKeyList": item_infos},
        ).json()
        if "code" in rsp and rsp["code"] == 0:
            logger.info(f"保存购入价格成功。")
        else:
            logger.error(f"保存购入价格失败，原因：{rsp}")

    def get_buy_order(self, pageIndex=1):
        buy_order_rsp = self.call_api(
            "POST",
            "/api/youpin/bff/trade/sale/v1/buy/list",
            data={
                "keys": "",
                "orderStatus": 340,
                "pageIndex": pageIndex,
                "pageSize": 20,
                "presenterId": 0,
                "sceneType": 0,
                "Sessionid": self.device_info["deviceId"],
            },
        ).json()
        buy_price = []
        if buy_order_rsp["code"] == 0:
            order_list = buy_order_rsp["data"]["orderList"]
            for order in order_list:
                if not order["orderStatusName"] == "已完成":
                    continue
                product_detail_list = order["productDetailList"]
                if order["commodityNum"] <= 3:
                    for product in product_detail_list:
                        buy_price.append(
                            {
                                "order_id": order["orderId"],
                                "abrade": product["abrade"][:11],
                                "buy_asset_id": product["assertId"] if product["assertId"] is not None else product["commodityId"],
                                "buy_price": product["price"] / 100,
                                "name": product["commodityName"],
                                "order_time": int(order["finishOrderTime"]),
                                "type_name": product["typeName"],
                                "buy_from": 'uu'
                            }
                        )
                else:
                    buy_price.extend(self.get_buy_batch_order(order["id"], order["buyerUserId"]))
                    time.sleep(10)
            logger.info(f"获取购入订单成功。数量: {len(buy_price)}")
        else:
            logger.error(f"获取购入订单失败，原因：{buy_order_rsp}")

        return buy_price

    def get_buy_batch_order(self, orderNo, userId):
        buy_batch_order_rsp = self.call_api(
            "POST",
            "/api/youpin/bff/trade/v1/order/query/detail",
            data={
                "orderNo": str(orderNo),
                "userId": userId,
                "Sessionid": self.device_info["deviceId"],
            },
        ).json()
        buy_price = []
        if buy_batch_order_rsp["code"] == 0:
            data = buy_batch_order_rsp["data"]
            for commodity in data["userCommodityVOList"][0]["commodityVOList"]:
                buy_price.append(
                    {
                        "order_id": orderNo,
                        "abrade": commodity["abrade"][:11],
                        "buy_asset_id": commodity["id"],
                        "buy_price": float(commodity["price"]),
                        "name": commodity["name"],
                        "order_time": int(data["orderCanceledTime"]),
                        "buy_from": 'uu'
                    }
                )

            logger.info(f"获取 batch 购入订单成功。数量: {len(buy_price)}")
        else:
            logger.error(f"获取 batch 购入订单失败，原因：{buy_batch_order_rsp}， 订单号：{orderNo}")

        return buy_price

    def get_zero_cd_list(self, pageIndex=1, pageSize=20):
        zero_cd_rsp = self.call_api(
            "POST",
            "/api/youpin/bff/trade/v1/order/lease/sublet/canEnable/list",
            data={
                "pageIndex": pageIndex,
                "pageSize": pageSize,
            },
        ).json()
        zero_cd_valid_list = []
        if zero_cd_rsp["code"] == 0:
            zero_cd_valid_list = zero_cd_rsp["data"]["orderDataList"]
        return zero_cd_valid_list

    def enable_zero_cd(self, orders_list):
        enable_zero_cd_rsp = self.call_api(
            "POST",
            "/api/youpin/bff/order/sublet/open",
            data={
                "orderIdList": orders_list,
                "subletConfig": {
                    "subletSwitchFlag": 1,
                    "subletPricingFlag": 1,
                    "pricingMinPercent": "95"
                }
            }
        ).json()

        if enable_zero_cd_rsp["code"] == 0:
            logger.info(f"0cd出租设置成功。")
        else:
            logger.error(f"0cd出租设置失败，原因：{enable_zero_cd_rsp}")
