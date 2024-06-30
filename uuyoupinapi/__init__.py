import random
import string

import requests

from utils.logger import PluginLogger

logger = PluginLogger("UUAutoAcceptOffer")


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
        "systemVersion": "13",
    }


def generate_headers(devicetoken, deviceid, token=""):
    return {
        "authorization": "Bearer " + token,
        "content-type": "application/json; charset=utf-8",
        "user-agent": "okhttp/3.14.9",
        "app-version": "5.14.2",
        "apptype": "4",
        "package-type": "uuyp",
        "devicetoken": devicetoken,
        "deviceid": deviceid,
        "platform": "android",
    }


class UUAccount:
    def __init__(self, token):
        """
        :param token: 通过抓包获得的token
        """
        self.session = requests.Session()
        self.ignore_list = []
        random.seed(token)
        self.device_info = generate_device_info()
        self.session.headers.update(
            generate_headers(self.device_info["deviceId"], self.device_info["deviceId"], token=token)
        )
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
    def send_login_sms_code(phone, session: str, headers=""):
        """
        发送登录短信验证码
        :param phone: 手机号
        :param session: 可以通过UUAccount.get_random_session_id()获得
        :return:
        """
        return requests.post(
            "https://api.youpin898.com/api/user/Auth/SendSignInSmsCode",
            json={"Area": 86, "Mobile": phone, "Sessionid": session, "Code": ""},
            headers=headers,
        ).json()

    @staticmethod
    def sms_sign_in(phone, code, session, headers=""):
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
            return self.session.get(url, params=data)
        elif method == "POST":
            return self.session.post(url, json=data)
        elif method == "PUT":
            return self.session.put(url, data=data)
        elif method == "DELETE":
            return self.session.delete(url)
        else:
            raise Exception("Method not supported")

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
            if order["orderNo"] not in self.ignore_list:
                logger.debug("[UUAutoAcceptOffer] 订单号为" + order["orderNo"] + "的订单已经被忽略")
                toDoList[order["orderNo"]] = order
        data_to_return = []
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
                        del toDoList[order["orderNo"]]
                        data_to_return.append(
                            {
                                "offer_id": order["tradeOfferId"],
                                "item_name": order["productDetail"]["commodityName"],
                            }
                        )
        if len(toDoList.keys()) != 0:
            for order in list(toDoList.keys()):
                try:
                    orderDetail = self.call_api(
                        "POST",
                        "/api/youpin/bff/order/v2/detail",
                        data={
                            "orderId": order,
                            "Sessionid": self.device_info["deviceId"],
                        },
                    ).json()
                    orderDetail = orderDetail["data"]["orderDetail"]
                    data_to_return.append(
                        {
                            "offer_id": orderDetail["offerId"],
                            "item_name": orderDetail["productDetail"]["commodityName"],
                        }
                    )
                    del toDoList[order]
                except TypeError:
                    logger.error(
                        "[UUAutoAcceptOffer] 订单号为"
                        + order
                        + "的订单未能获取到Steam交易报价号，可能是悠悠系统错误或者需要卖家手动发送报价。该报价已经加入忽略列表。"
                    )
                    self.ignore_list.append(order)
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
            response = self.call_api("POST", "/api/youpin/bff/new/commodity/v1/commodity/list/sell")
            if response.json()["code"] != 0:
                break
            else:
                for item in response.json()["data"]["commodityInfoList"]:
                    shelf.append(item)
        return shelf

    def off_shelf(self, commodity_ids: list):
        return self.call_api(
            "POST",
            "/api/commodity/Commodity/OffShelf",
            data={
                "commodityId": commodity_ids.join(","),
                "IsDeleteCommodityCache": 1,
                "IsForceOffline": True,
            },
        )

    def sell_items(self, assets: dict):
        item_infos = [{"AssetId": asset, "Price": assets[asset], "Remark": None} for asset in assets.keys()]
        self.call_api(
            "POST",
            "/api/commodity/Inventory/SellInventoryWithLeaseV2",
            data={
                "GameID": 730,
                "ItemInfos": item_infos,
            },
        )
