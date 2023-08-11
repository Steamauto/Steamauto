import requests
import string
import random

from utils.logger import logger


def generate_random_string(length):
    """
    生成指定长度的字符串，包含 A-Z, a-z 和数字
    :param length: 字符串长度
    :return: 随机字符串
    """
    letters_and_digits = string.ascii_letters + string.digits
    return "".join(random.choice(letters_and_digits) for i in range(length))


class UUAccount:
    def __init__(self, token):
        """
        :param token: 通过抓包获得的token
        """
        self.session = requests.Session()
        random.seed(token)
        self.device_info = {
            "deviceId": generate_random_string(24),
            "deviceType": generate_random_string(6),
            "hasSteamApp": 0,
            "systemName ": "Android",
            "systemVersion": "13",
        }
        self.session.headers.update(
            {
                "authorization": "Bearer " + token,
                "content-type": "application/json; charset=utf-8",
                "user-agent": "okhttp/3.14.9",
                "app-version": "5.4.1",
                "apptype": "4",
                "devicetoken": self.device_info["deviceId"],
                "deviceid": self.device_info["deviceId"],
                "platform": "android",
            }
        )

    @staticmethod
    def __random_str(length):
        import random
        import string

        return "".join(random.sample(string.ascii_letters + string.digits, length))

    @staticmethod
    def get_token_automatically():
        """
        引导用户输入手机号，发送验证码，输入验证码，自动登录，并且返回token
        :return: token
        """
        phone_number = input("输入手机号：")
        session_id = UUAccount.get_random_session_id()
        print("随机生成的session_id：", session_id)
        print("发送验证码结果：", UUAccount.send_login_sms_code(phone_number, session_id)["Msg"])
        sms_code = input("输入验证码：")
        response = UUAccount.sms_sign_in(phone_number, sms_code, session_id)
        print("登录结果：", response["Msg"])
        got_token = response["Data"]["Token"]
        print("token：", got_token)
        return got_token

    @staticmethod
    def get_random_session_id():
        return UUAccount.__random_str(32)

    @staticmethod
    def send_login_sms_code(phone, session: str):
        """
        发送登录短信验证码
        :param phone: 手机号
        :param session: 可以通过UUAccount.get_random_session_id()获得
        :return:
        """
        return requests.post(
            "https://api.youpin898.com/api/user/Auth/SendSignInSmsCode", json={"Mobile": phone, "Sessionid": session}
        ).json()

    @staticmethod
    def sms_sign_in(phone, code, session):
        """
        通过短信验证码登录，返回值内包含Token
        :param phone: 发送验证码时的手机号
        :param code: 短信验证码
        :param session: 可以通过UUAccount.get_random_session_id()获得，必须和发送验证码时的session一致
        :return:
        """
        return requests.post(
            "https://api.youpin898.com/api/user/Auth/SmsSignIn",
            json={"Code": code, "SessionId": session, "Mobile": phone, "TenDay": 1},
        ).json()

    def get_user_nickname(self):
        return self.call_api("GET", "/api/user/Account/getUserInfo").json()["Data"]["NickName"]

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
        获取待发货列表（出售）
        :param return_offer_id: 默认为True，是否返回steam交易报价号
        :param game_id: 游戏ID，默认为730(CSGO)
        :return: 待发货列表，格式为[{'order_id': '订单号', 'item_name': '物品名称', 'offer_id': 'steam交易报价号'}... , ...]
        """
        # data = self.call_api('POST', '/api/youpin/bff/trade/sell/page/v1/waitDeliver/waitDeliverList', data={
        #     "gameId": game_id,
        #     "pageIndex": 1,
        #     "pageSize": 1000
        # })
        # wait_deliver_list = data.json()['data']['waitDeliverList']
        # data_to_return = []
        # if wait_deliver_list is not None:
        #     for item in wait_deliver_list:
        #         if item['orderInfoVO']['orderType'] == 1:
        #             dict_to_append = dict()
        #             dict_to_append['order_id'] = item['orderInfoVO']['orderNo']
        #             dict_to_append['item_name'] = item['commodityInfoVO']['commodityName']
        #             if return_offer_id:
        #                 dict_to_append['offer_id'] = self.get_steam_offer_id_by_order_id(dict_to_append['order_id'])
        #             data_to_return.append(dict_to_append)
        data = self.call_api(
            "POST",
            "/api/youpin/bff/trade/sale/v1/sell/list",
            data={"keys": "", "orderStatus": "140", "pageIndex": 1, "pageSize": 100},
        )
        if data is None:
            logger.error("待发货列表获取失败")
            return []
        else:
            data = data.json()["data"]
        data_to_return = []
        for order in data["orderList"]:
            if int(order["offerType"]) == 2:
                if order["tradeOfferId"] is not None:
                    data_to_return.append(
                        {
                            "offer_id": order["tradeOfferId"],
                            "item_name": order["productDetail"]["commodityName"],
                        }
                    )
                else:
                    response = self.call_api(
                        "GET", "/api/trade/Order/OrderPagedDetail", data={"orderNo": order["orderNo"]}
                    ).json()
                    data_to_return.append(
                        {
                            "offer_id": response["Data"]["SteamOfferId"],
                            "item_name": order["productDetail"]["commodityName"],
                        }
                    )
            else:
                data_to_return.append(
                    {
                        "offer_id": None,
                        "item_name": order["productDetail"]["commodityName"],
                    }
                )
        return data_to_return
