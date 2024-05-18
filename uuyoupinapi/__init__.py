import random
import string
from venv import logger

import requests

from utils.logger import get_logger

logger = get_logger()


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
    @staticmethod
    def __init__(self, token):
        """
        :param token: 通过抓包获得的token
        """
        self.session = requests.Session()
        self.ignore_list = []
        random.seed(token)
        self.device_info = generate_device_info()
        self.session.headers.update(
            generate_headers(
                self.device_info["deviceId"], self.device_info["deviceId"], token=token
            )
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
    def get_token_automatically():
        """
        引导用户输入手机号，发送验证码，输入验证码，自动登录，并且返回token
        :return: token
        """
        device_info = generate_device_info()
        headers = generate_headers(device_info["deviceId"], device_info["deviceId"])

        phone_number = input("输入手机号(+86)：")
        session_id = device_info["deviceId"]
        print("随机生成的session_id：", session_id)
        result = UUAccount.send_login_sms_code(
            phone_number, session_id, headers=headers
        )
        response = {}
        if result['Code'] != 5050:
            print("发送验证码结果：", result["Msg"])
            sms_code = input("输入验证码：")
            response = UUAccount.sms_sign_in(
                phone_number, sms_code, session_id, headers=headers
            )
        else:
            print('该手机号需要手动发送短信进行验证，正在获取相关信息...')
            result = UUAccount.get_smsUpSignInConfig(headers).json()
            if result['Code'] == 0:
                print('请求结果：'+result['Msg'])
                print(f"请编辑发送短信 \033[1;33m{result['Data']['SmsUpContent']}\033[0m 到号码 \033[1;31m{result['Data']['SmsUpNumber']}\033[0m ！\n发送完成后请点击回车.")
                input()
                response = UUAccount.sms_sign_in(phone_number, '', session_id, headers=headers)
        print("登录结果：", response["Msg"])
        got_token = response["Data"]["Token"]
        print("token：", got_token)
        return got_token

    
    @staticmethod
    def get_smsUpSignInConfig(headers):
        return requests.get('https://api.youpin898.com/api/user/Auth/GetSmsUpSignInConfig',headers=headers)
        

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
        if code == '':
            url = 'https://api.youpin898.com/api/user/Auth/SmsUpSignIn'
        else:
            url = 'https://api.youpin898.com/api/user/Auth/SmsSignIn'
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
                logger.debug(
                    "[UUAutoAcceptOffer] 订单号为"
                    + order["orderNo"]
                    + "的订单已经被忽略"
                )
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
                "[UUAutoAcceptOffer] 有订单未能获取到Steam交易报价号，订单号为："
                + str(toDoList.keys()),
            )
        return data_to_return
