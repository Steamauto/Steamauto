#
#   ____         __  __                  _
#  |  _ \       / _|/ _|     /\         (_)
#  | |_) |_   _| |_| |_     /  \   _ __  _
#  |  _ <| | | |  _|  _|   / /\ \ | '_ \| |
#  | |_) | |_| | | | |    / ____ \| |_) | |
#  |____/ \__,_|_| |_|   /_/    \_\ .__/|_|
#                                 | |
#                                 |_|
# Buff-Api By jiajiaxd(https://github.com/jiajiaxd)
# 请在遵守GPL-3.0协议的前提下使用本API。
# 仅供学习交流使用，所造成的一切后果将由使用者自行承担！

import copy
import json
import random
import time
from typing import no_type_check, Dict, List, Union

import requests

from utils.logger import PluginLogger
from BuffApi import models

logger = PluginLogger("BuffApi")


def get_ua():
    first_num = random.randint(55, 62)
    third_num = random.randint(0, 3200)
    fourth_num = random.randint(0, 140)
    os_type = [
        "(Windows NT 6.1; WOW64)",
        "(Windows NT 10.0; WOW64)",
        "(X11; Linux x86_64)",
        "(Macintosh; Intel Mac OS X 10_12_6)",
    ]
    chrome_version = f"Chrome/{first_num}.0.{third_num}.{fourth_num}"

    ua = " ".join(
        [
            "Mozilla/5.0",
            random.choice(os_type),
            "AppleWebKit/537.36",
            "(KHTML, like Gecko)",
            chrome_version,
            "Safari/537.36",
        ]
    )
    return ua


def get_random_header() -> dict:
    return {"User-Agent": get_ua()}


class BuffAccount:
    """
    支持自定义User-Agent
    参数为Buff的cookie
    参考格式：
    session=*******
    若报错，大概率是因为你被BUFF的反爬虫机制检测到了，请多次尝试！

    附：
    Buff的每个商品的每种磨损（品质）均有一个独立的goods_id,每一件商品都有一个独立的id
    """

    BASE_URL = "https://buff.163.com"

    def __init__(self, buffcookie, user_agent=get_ua()):
        self.session = requests.session()
        self.session.headers = {"User-Agent": user_agent}
        headers = copy.deepcopy(self.session.headers)
        headers["Cookie"] = buffcookie
        self.get_notification(headers=headers)

    def get(self, url, **kwargs):
        for i in range(10):
            response = self.session.get(url, **kwargs)
            logger.debug(f"GET {url} {response.status_code} {json.dumps(response.json(), ensure_ascii=False)}")
            if "系统繁忙" in response.text:
                logger.warning(f"BUFF接口繁忙，正在重试...{i + 1}/10")
                time.sleep(2)
            else:
                break
        return response

    def post(self, url, **kwargs):
        for i in range(5):
            response = self.session.post(url, **kwargs)
            logger.debug(f"POST {url} {response.status_code} {json.dumps(response.json(), ensure_ascii=False)}")
            if "系统繁忙" in response.text:
                logger.warning(f"BUFF接口繁忙，正在重试...{i + 1}/10")
                time.sleep(2)
            else:
                break
        return response

    def get_user_nickname(self) -> str:
        """
        :return: str
        """
        try:
            user_info = self.get_user_info()
            if user_info and "nickname" in user_info:
                self.username = user_info["nickname"]
                return self.username
        except AttributeError:
            raise ValueError("Buff登录失败！请稍后再试或检查cookie填写是否正确.")
        return ""

    def get_user_info(self) -> Dict:
        """获取用户信息，包含SteamID等数据"""
        response = self.get(f"{self.BASE_URL}/account/api/user/info")
        if response.status_code == 200:
            data = response.json()
            if data["code"] == "OK" and "data" in data:
                return data["data"]
        return {}

    def set_force_buyer_send_offer(self) -> bool:
        """设置只允许买家发起交易报价"""
        headers = self.CSRF_Fucker()
        headers["Referer"] = f"{self.BASE_URL}/user-center/profile"
        data = {"force_buyer_send_offer": "true"}

        resp = self.post(f"{self.BASE_URL}/account/api/prefer/force_buyer_send_offer", json=data, headers=headers)

        if resp.status_code == 200 and resp.json()["code"] == "OK":
            return True
        return False

    def get_sell_order_to_deliver(self, game: str, appid: Union[str, int]) -> Dict:
        """获取等待发货的订单"""
        params = {"game": game, "appid": str(appid)}
        response = self.get(f"{self.BASE_URL}/api/market/sell_order/to_deliver", params=params)
        if response.status_code == 200:
            data = response.json()
            if data["code"] == "OK" and "data" in data:
                return data["data"]
        return {}

    def get_sell_order_history(self, appid: Union[str, int]) -> List:
        """获取销售历史记录"""
        params = {"appid": str(appid), "mode": "1"}
        response = self.get(f"{self.BASE_URL}/api/market/sell_order/history", params=params)
        if response.status_code == 200:
            data = response.json()
            if data["code"] == "OK" and "data" in data and "items" in data["data"]:
                return data["data"]["items"]
        return []

    def get_user_brief_assest(self) -> dict:
        """
        包含用户余额等信息
        :return: dict
        """
        return json.loads(self.get(f"{self.BASE_URL}/api/asset/get_brief_asset").text).get("data")

    def search_goods(self, key: str, game_name="csgo") -> list:
        return (
            json.loads(
                self.get(
                    f"{self.BASE_URL}/api/market/search/suggest",
                    params={"text": key, "game": game_name},
                ).text
            )
            .get("data")
            .get("suggestions")
        )

    def get_sell_order(self, goods_id, page_num=1, game_name="csgo", sort_by="default", proxy=None, min_paintseed=None, max_paintseed=None) -> dict:
        """
        获取指定饰品的在售商品
        :return: dict
        """
        params = {
            "game": game_name,
            "goods_id": goods_id,
            "page_num": page_num,
            "sort_by": sort_by,
        }
        need_login = False
        if min_paintseed:
            params["min_paintseed"] = min_paintseed
            need_login = True
        if max_paintseed:
            params["max_paintseed"] = max_paintseed
            need_login = True
        if sort_by != "default":
            need_login = True
        if need_login:
            return json.loads(
                self.get(
                    f"{self.BASE_URL}/api/market/goods/sell_order",
                    params=params,
                    headers=get_random_header(),
                    proxies=proxy,
                ).text
            ).get("data")
        else:
            return json.loads(
                requests.get(
                    f"{self.BASE_URL}/api/market/goods/sell_order",
                    params=params,
                    headers=get_random_header(),
                    proxies=proxy,
                ).text
            ).get("data")

    def get_available_payment_methods(self, sell_order_id, goods_id, price, game_name="csgo") -> dict:
        """
        :param game_name:默认为csgo
        :param sell_order_id:
        :param goods_id:
        :param price:饰品价格
        :return: dict key只会包含buff-alipay和buff-bankcard，若不存在key，则代表此支付方式不可用。value值为当前余额
        """

        methods = (
            json.loads(
                self.get(
                    f"{self.BASE_URL}/api/market/goods/buy/preview",
                    params={
                        "game": game_name,
                        "sell_order_id": sell_order_id,
                        "goods_id": goods_id,
                        "price": price,
                    },
                ).text
            )
            .get("data")
            .get("pay_methods")
        )
        available_methods = dict()
        if methods[0].get("error") is None:
            available_methods["buff-alipay"] = methods[0].get("balance")
        if methods[2].get("error") is None:
            available_methods["buff-bankcard"] = methods[2].get("balance")
        return available_methods

    def buy_goods(
        self,
        sell_order_id,
        goods_id,
        price,
        pay_method: str,
        ask_seller_send_offer: bool,
        game_name="csgo",
    ):
        """
        由于部分卖家禁用了由卖家发起报价，因此不推荐使用此API
        :param sell_order_id:
        :param goods_id:
        :param price:
        :param pay_method:仅支持buff-alipay或buff-bankcard.
        :param ask_seller_send_offer: 是否要求卖家发送报价
        若为False则为由买家发送报价
        警告：本API并不会自动发起报价，报价需要用户在手机版BUFF上发起！！！
        若卖家禁用了由卖家发起报价，则会自动更改为由买家发送报价！！！
        建议与github.com/jiajiaxd/Buff-Bot配合使用，效果更佳！
        :param game_name: 默认为csgo
        :return:若购买成功则返回'购买成功'，购买失败则返回错误信息
        """
        load = {
            "game": game_name,
            "goods_id": goods_id,
            "price": price,
            "sell_order_id": sell_order_id,
            "token": "",
            "cdkey_id": "",
        }
        if pay_method == "buff-bankcard":
            load["pay_method"] = 1
        elif pay_method == "buff-alipay":
            load["pay_method"] = 3
        else:
            raise ValueError("Invalid pay_method")
        headers = copy.deepcopy(self.session.headers)
        headers["accept"] = "application/json, text/javascript, */*; q=0.01"
        headers["content-type"] = "application/json"
        headers["dnt"] = "1"
        headers["origin"] = self.BASE_URL
        headers["referer"] = f"{self.BASE_URL}/goods/{str(goods_id)}?from=market"
        headers["x-requested-with"] = "XMLHttpRequest"
        # 获取最新csrf_token
        self.get(f"{self.BASE_URL}/api/message/notification")
        self.session.cookies.get("csrf_token")
        headers["x-csrftoken"] = str(self.session.cookies.get("csrf_token"))
        response = json.loads(self.post(f"{self.BASE_URL}/api/market/goods/buy", json=load, headers=headers).text)
        bill_id = response.get("data").get("id")
        self.get(
            f"{self.BASE_URL}/api/market/bill_order/batch/info",
            params={"bill_orders": bill_id},
        )
        headers["x-csrftoken"] = str(self.session.cookies.get("csrf_token"))
        time.sleep(0.5)  # 由于Buff服务器处理支付需要一定的时间，所以一定要在这里加上sleep，否则无法发送下一步请求
        if ask_seller_send_offer:
            load = {"bill_orders": [bill_id], "game": game_name}
            response = self.post(
                f"{self.BASE_URL}/api/market/bill_order/ask_seller_to_send_offer",
                json=load,
                headers=headers,
            )
        else:
            load = {"bill_order_id": bill_id, "game": game_name}
            response = self.post(
                f"{self.BASE_URL}/api/market/bill_order/notify_buyer_to_send_offer",
                json=load,
                headers=headers,
            )
        response = json.loads(response.text)
        if response.get("msg") is None and response.get("code") == "OK":
            return "购买成功"
        else:
            return response

    def get_notification(self, headers=None) -> dict:
        """
        获取notification
        :return: dict
        """
        if headers:
            self.session.headers = headers
        response = self.get(f"{self.BASE_URL}/api/message/notification")
        data = response.json()
        if response.status_code == 200:
            return data["data"]
        elif "error" in data:
            return data
        else:
            return {}

    def get_steam_trade(self) -> list:
        response = self.get(f"{self.BASE_URL}/api/market/steam_trade")
        if response.status_code == 200:
            data = response.json()
            if data["code"] == "OK":
                return data["data"]
        return []

    def on_sale(self, assets: list[models.BuffOnSaleAsset]):
        """
        仅支持CSGO 返回上架成功商品的id
        """
        response = self.post(
            f"{self.BASE_URL}/api/market/sell_order/create/manual_plus",
            json={
                "appid": "730",
                "game": "csgo",
                "assets": [asset.model_dump(exclude_none=True) for asset in assets],
            },
            headers=self.CSRF_Fucker(),
        )
        success = []
        problem_assets = {}
        for good in response.json()["data"].keys():
            if response.json()["data"][good] == "OK":
                success.append(good)
            else:
                problem_assets[good] = response.json()["data"][good]
        return success, problem_assets

    def cancel_sale(self, sell_orders: list, exclude_sell_orders: list = []):
        """
        返回下架成功数量
        """
        success = 0
        problem_sell_orders = {}
        for index in range(0, len(sell_orders), 50):
            response = self.post(
                f"{self.BASE_URL}/api/market/sell_order/cancel",
                json={
                    "game": "csgo",
                    "sell_orders": sell_orders[index : index + 50],
                    "exclude_sell_orders": exclude_sell_orders,
                },
                headers=self.CSRF_Fucker(),
            )
            if response.json()["code"] != "OK":
                raise Exception(response.json().get("msg", None))
            for key in response.json()["data"].keys():
                if response.json()["data"][key] == "OK":
                    success += 1
                else:
                    problem_sell_orders[key] = response.json()["data"][key]
        return success, problem_sell_orders

    def get_on_sale(self, page_num=1, page_size=500, mode="2,5", fold="0"):
        return self.get(
            f"{self.BASE_URL}/api/market/sell_order/on_sale",
            params={
                "page_num": page_num,
                "page_size": page_size,
                "mode": mode,
                "fold": fold,
                "game": "csgo",
                "appid": 730,
            },
        )

    def change_price(self, sell_orders: list):
        """
        problem的key是订单ID
        """
        success = 0
        problems = {}
        for index in range(0, len(sell_orders), 50):
            response = self.post(
                f"{self.BASE_URL}/api/market/sell_order/change",
                json={
                    "appid": "730",
                    "sell_orders": sell_orders[index : index + 50],
                },
                headers=self.CSRF_Fucker(),
            )
            if response.json()["code"] != "OK":
                raise Exception(response.json().get("msg", None))
            for key in response.json()["data"].keys():
                if response.json()["data"][key] == "OK":
                    success += 1
                else:
                    problems[key] = response.json()["data"][key]
        return success, problems

    @no_type_check
    def CSRF_Fucker(self):
        self.get(f"{self.BASE_URL}/api/market/steam_trade")
        csrf_token = self.session.cookies.get("csrf_token", domain="buff.163.com")
        headers = copy.deepcopy(self.session.headers)
        headers.update(
            {
                "X-CSRFToken": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/json",
                "Referer": f"{self.BASE_URL}/market/sell_order/create?game=csgo",
            }
        )
        return headers
