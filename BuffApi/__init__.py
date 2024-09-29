# BuffApi/__init__.py

import copy
import datetime
import json
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple, no_type_check

import requests
from requests import Session
from requests.exceptions import RequestException

import apprise
from apprise import AppriseAsset
from requests.structures import CaseInsensitiveDict

from utils.buff_helper import get_valid_session_for_buff
from utils.logger import PluginLogger, handle_caught_exception
from BuffApi.models import BuffOnSaleAsset
from utils.static import (
    APPRISE_ASSET_FOLDER,
    BUFF_COOKIES_FILE_PATH,
    SESSION_FOLDER,
    SUPPORT_GAME_TYPES,
)
from utils.tools import get_encoding, exit_code

logger = PluginLogger("BuffApi")


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
    def __init__(self, steam_client, user_agent: Optional[str] = None):
        self.buff_headers = {}
        self.session: Session = requests.Session()
        self.steam_client = steam_client
        self.session.headers.update({"User-Agent": user_agent or self.get_ua()})
        self.logger = PluginLogger("BuffAutoAcceptOffer")
        if not self.login():
            raise Exception("Buff登录失败！请稍后再试或检查cookie填写是否正确.")
        self.asset = AppriseAsset(plugin_paths=[self._get_apprise_asset_path()])
        self.order_info: Dict[str, Dict[str, Any]] = {}
        self.lowest_on_sale_price_cache: Dict[str, Dict[str, Any]] = {}

    def login(self):
        buff_cookie = get_valid_session_for_buff(self.steam_client, logger)
        if not buff_cookie:
            return False
        self.buff_headers = self._initialize_headers(buff_cookie)
        # 获取用户昵称以验证登录状态
        username = self.get_user_nickname()
        self.logger.info(f"已登录至BUFF 用户名: {username}")

        # 验证Steam账户与BUFF绑定的SteamID是否一致
        buff_steamid = self.get_buff_bind_steamid()
        steamid = self.steam_client.get_steam64id_from_cookies()
        if steamid != buff_steamid:
            self.logger.error("当前登录账号与BUFF绑定的Steam账号不一致!")
            return False
        return True

    def require_buyer_send_offer(self) -> None:
        """
        启用买家发起交易报价功能。
        """
        url = "https://buff.163.com/account/api/prefer/force_buyer_send_offer"
        data = {"force_buyer_send_offer": "true"}

        try:
            # 获取 CSRF Token
            resp = self.session.get("https://buff.163.com/api/market/steam_trade", timeout=10)
            resp.raise_for_status()
            csrf_token = resp.cookies.get("csrf_token", "")
            if not csrf_token:
                self.logger.error("未能获取到 CSRF Token")
                return

            headers = self.buff_headers.copy()
            headers.update({
                "X-CSRFToken": csrf_token,
                "Origin": "https://buff.163.com",
                "Referer": "https://buff.163.com/user-center/profile"
            })

            # 发送请求
            resp = self.session.post(url, headers=headers, json=data, timeout=10)
            resp.raise_for_status()

            response_json = resp.json()
            if response_json.get("code") == "OK":
                self.logger.info("已开启买家发起交易报价功能")
            else:
                self.logger.error("开启买家发起交易报价功能失败: " + str(response_json))
        except RequestException as e:
            handle_caught_exception(e, "BuffAutoAcceptOffer")
            self.logger.error("开启买家发起交易报价功能失败: " + str(e))

    def get_order_info(self, trades: List[Dict[str, Any]]) -> None:
        """
        获取卖出订单信息并缓存。
        """
        for trade in trades:
            trade_id = trade.get("tradeofferid")
            if trade_id and trade_id not in self.order_info:
                time.sleep(5)  # 避免频繁请求
                sell_order_history_url = f"https://buff.163.com/api/market/sell_order/history?appid={trade.get('appid')}&mode=1"
                try:
                    resp = self.session.get(sell_order_history_url, timeout=10)
                    resp.raise_for_status()
                    resp_json = resp.json()
                    if resp_json.get("code") == "OK":
                        for sell_item in resp_json.get("data", {}).get("items", []):
                            sell_trade_id = sell_item.get("tradeofferid")
                            if sell_trade_id:
                                self.order_info[sell_trade_id] = sell_item
                    else:
                        self.logger.error("获取卖出订单信息失败: " + str(resp_json))
                except RequestException as e:
                    handle_caught_exception(e, "BuffAutoAcceptOffer")
                    self.logger.error("获取卖出订单信息失败: " + str(e))

    def get_buff_bind_steamid(self) -> str:
        """
        获取BUFF绑定的SteamID。
        """
        try:
            response = self.session.get("https://buff.163.com/account/api/user/info", timeout=10)
            response.raise_for_status()
            response_json = response.json()
            if response_json.get("code") == "OK":
                return response_json.get("data", {}).get("steamid", "")
            else:
                self.logger.error("获取BUFF绑定的SteamID失败: " + str(response_json))
                return ""
        except RequestException as e:
            handle_caught_exception(e, "BuffAutoAcceptOffer")
            self.logger.error("获取BUFF绑定的SteamID失败: " + str(e))
            return ""

    def check_buff_account_state(self) -> str:
        """
        检查BUFF账户的登录状态。
        """
        try:
            response = self.session.get("https://buff.163.com/account/api/user/info", timeout=10)
            response.raise_for_status()
            response_json = response.json()
            if response_json.get("code") == "OK":
                data = response_json.get("data", {})
                nickname = data.get("nickname")
                if nickname:
                    steam_trade_response = self.session.get("https://buff.163.com/api/market/steam_trade", timeout=10).json()
                    if not steam_trade_response.get("data"):
                        self.logger.error("BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试!")
                        return ""
                    return nickname
            self.logger.error("BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试!")
            return ""
        except RequestException as e:
            handle_caught_exception(e, "BuffAutoAcceptOffer")
            self.logger.error("检查BUFF账户状态失败: " + str(e))
            return ""

    def format_str(self, text: str, trade: Dict[str, Any]) -> str:
        """
        格式化通知消息。
        """
        for good in trade.get("goods_infos", {}):
            good_item = trade["goods_infos"][good]
            buff_price = float(self.order_info.get(trade["tradeofferid"], {}).get("price", "0"))
            created_at_time_str = datetime.datetime.fromtimestamp(trade["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
            text = text.format(
                item_name=good_item.get("name", ""),
                steam_price=good_item.get("steam_price", ""),
                steam_price_cny=good_item.get("steam_price_cny", ""),
                buyer_name=trade.get("bot_name", ""),
                buyer_avatar=trade.get("bot_avatar", ""),
                order_time=created_at_time_str,
                game=good_item.get("game", ""),
                good_icon=good_item.get("original_icon_url", ""),
                buff_price=buff_price,
                sold_count=len(trade.get("items_to_trade", [])),
                offer_id=trade.get("tradeofferid", ""),
            )
        return text

    @staticmethod
    def get_ua() -> str:
        """生成随机的User-Agent"""
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
        ua = " ".join([
            "Mozilla/5.0",
            random.choice(os_type),
            "AppleWebKit/537.36",
            "(KHTML, like Gecko)",
            chrome_version,
            "Safari/537.36",
        ])
        return ua

    @staticmethod
    def _get_apprise_asset_path() -> str:
        """获取Apprise资源文件夹路径"""
        return os.path.join(os.path.dirname(__file__), "..", APPRISE_ASSET_FOLDER)

    def _initialize_headers(self, buff_cookie: str) -> CaseInsensitiveDict[str | bytes]:
        """初始化请求头，包括Cookie"""
        headers = copy.deepcopy(self.session.headers)
        headers["Cookie"] = buff_cookie
        self.session.headers.update(headers)
        return headers

    def get_random_header(self) -> Dict[str, str]:
        """生成带有随机User-Agent的请求头"""
        return {"User-Agent": self.get_ua()}

    def get(self, url: str, **kwargs) -> requests.Response:
        """发送GET请求并记录日志"""
        try:
            response = self.session.get(url, timeout=10, **kwargs)
            response.raise_for_status()
            logger.debug(f"GET {url} {response.status_code} {response.text}")
            return response
        except RequestException as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error(f"GET请求失败: {url} 错误: {e}")
            raise

    def post(self, url: str, **kwargs) -> requests.Response:
        """发送POST请求并记录日志"""
        try:
            response = self.session.post(url, timeout=10, **kwargs)
            response.raise_for_status()
            logger.debug(f"POST {url} {response.status_code} {response.text}")
            return response
        except RequestException as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error(f"POST请求失败: {url} 错误: {e}")
            raise

    def get_user_nickname(self) -> str:
        """获取用户昵称"""
        url = "https://buff.163.com/account/api/user/info"
        try:
            response = self.get(url)
            data = response.json().get("data", {})
            nickname = data.get("nickname")
            if not nickname:
                raise ValueError("未能获取到昵称")
            return nickname
        except (ValueError, KeyError) as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error("Buff登录失败！请稍后再试或检查cookie填写是否正确.")
            raise

    def get_user_brief_asset(self) -> Dict[str, Any]:
        """获取用户余额等信息"""
        url = "https://buff.163.com/api/asset/get_brief_asset"
        try:
            response = self.get(url)
            return response.json().get("data", {})
        except (ValueError, KeyError) as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error("获取用户资产失败!")
            raise

    def search_goods(self, key: str, game_name: str = "csgo") -> List[Dict[str, Any]]:
        """搜索商品"""
        url = "https://buff.163.com/api/market/search/suggest"
        params = {"text": key, "game": game_name}
        try:
            response = self.get(url, params=params)
            return response.json().get("data", {}).get("suggestions", [])
        except (ValueError, KeyError) as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error("搜索商品失败!")
            return []

    def get_sell_order(self, goods_id: str, page_num: int = 1, game_name: str = "csgo", sort_by: str = "default", proxy: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """获取指定饰品的在售商品"""
        url = "https://buff.163.com/api/market/goods/sell_order"
        params = {
            "game": game_name,
            "goods_id": goods_id,
            "page_num": page_num,
            "sort_by": sort_by,
        }
        headers = self.get_random_header() if sort_by != "default" else {}
        try:
            response = self.get(url, params=params, headers=headers, proxies=proxy)
            return response.json().get("data", {})
        except (ValueError, KeyError) as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error("获取在售订单失败!")
            return {}

    def get_available_payment_methods(self, sell_order_id: str, goods_id: str, price: float, game_name: str = "csgo") -> Dict[str, float]:
        """
        获取可用的支付方式及余额

        :param sell_order_id: 销售订单ID
        :param goods_id: 商品ID
        :param price: 商品价格
        :param game_name: 游戏名称，默认为csgo
        :return: 可用支付方式及对应余额
        """
        url = "https://buff.163.com/api/market/goods/buy/preview"
        params = {
            "game": game_name,
            "sell_order_id": sell_order_id,
            "goods_id": goods_id,
            "price": price,
        }
        try:
            response = self.get(url, params=params)
            methods = response.json().get("data", {}).get("pay_methods", [])
            available_methods = {}
            if len(methods) > 0 and methods[0].get("error") is None:
                available_methods["buff-alipay"] = methods[0].get("balance", 0.0)
            if len(methods) > 2 and methods[2].get("error") is None:
                available_methods["buff-bankcard"] = methods[2].get("balance", 0.0)
            return available_methods
        except (ValueError, KeyError) as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error("获取可用支付方式失败!")
            return {}

    def buy_goods(
            self,
            sell_order_id: str,
            goods_id: str,
            price: float,
            pay_method: str,
            ask_seller_send_offer: bool,
            game_name: str = "csgo",
    ) -> str:
        """
        购买商品

        :param sell_order_id: 销售订单ID
        :param goods_id: 商品ID
        :param price: 商品价格
        :param pay_method: 支付方式，仅支持buff-alipay或buff-bankcard
        :param ask_seller_send_offer: 是否要求卖家发送报价
        :param game_name: 游戏名称，默认为csgo
        :return: 购买结果消息
        """
        payload = self._build_buy_goods_payload(sell_order_id, goods_id, price, pay_method, game_name)
        headers = self._prepare_buy_headers()

        try:
            response = self.post("https://buff.163.com/api/market/goods/buy", json=payload, headers=headers)
            response_data = response.json().get("data", {})
            bill_id = response_data.get("id")

            if not bill_id:
                raise ValueError("未能获取到账单ID")

            self._fetch_bill_order_info(bill_id, game_name)
            self._handle_buy_offer(bill_id, ask_seller_send_offer, game_name)

            return "购买成功"
        except (ValueError, KeyError) as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error(f"购买失败: {e}")
            return f"购买失败: {e}"
        except RequestException as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error(f"购买请求失败: {e}")
            return f"购买请求失败: {e}"

    @staticmethod
    def _build_buy_goods_payload(
            sell_order_id: str,
            goods_id: str,
            price: float,
            pay_method: str,
            game_name: str
    ) -> Dict[str, Any]:
        """构建购买商品的Payload"""
        pay_method_mapping = {"buff-bankcard": 1, "buff-alipay": 3}
        if pay_method not in pay_method_mapping:
            raise ValueError("Invalid pay_method")

        return {
            "game": game_name,
            "goods_id": goods_id,
            "price": price,
            "sell_order_id": sell_order_id,
            "pay_method": pay_method_mapping[pay_method],
            "token": "",
            "cdkey_id": "",
        }

    def _prepare_buy_headers(self) -> CaseInsensitiveDict[str | bytes]:
        """准备购买商品的请求头"""
        headers = copy.deepcopy(self.session.headers)
        headers.update({
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json",
            "DNT": "1",
            "Origin": "https://buff.163.com",
            "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
            "X-Requested-With": "XMLHttpRequest",
        })
        headers["X-CSRFToken"] = self._get_csrf_token()
        return headers

    def _get_csrf_token(self) -> str:
        """获取最新的CSRF Token"""
        self.refresh_csrf_token()
        csrf_token = self.session.cookies.get("csrf_token", "")
        if not csrf_token:
            self.logger.error("未能刷新CSRF Token")
        return csrf_token

    def _fetch_bill_order_info(self, bill_id: str, game_name: str) -> None:
        """获取账单订单信息"""
        url = "https://buff.163.com/api/market/bill_order/batch/info"
        params = {"bill_orders": bill_id, "game": game_name}
        try:
            self.post(url, params=params)
        except RequestException as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error(f"获取账单订单信息失败: {e}")

    def _handle_buy_offer(self, bill_id: str, ask_seller_send_offer: bool, game_name: str) -> None:
        """处理购买报价"""
        headers = self._prepare_buy_headers()
        time.sleep(0.5)  # 等待Buff服务器处理支付

        if ask_seller_send_offer:
            payload = {"bill_orders": [bill_id], "game": game_name}
            url = "https://buff.163.com/api/market/bill_order/ask_seller_to_send_offer"
        else:
            payload = {"bill_order_id": bill_id, "game": game_name}
            url = "https://buff.163.com/api/market/bill_order/notify_buyer_to_send_offer"

        try:
            response = self.post(url, json=payload, headers=headers)
            response_data = response.json()
            if response_data.get("msg") is None and response_data.get("code") == "OK":
                self.logger.info("购买成功，已发送报价请求。")
            else:
                self.logger.error(f"购买失败: {response_data}")
        except (ValueError, KeyError) as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error(f"处理购买报价失败: {e}")
        except RequestException as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error(f"发送购买报价请求失败: {e}")

    def get_notification(self) -> Dict[str, Any]:
        """获取通知信息"""
        url = "https://buff.163.com/api/message/notification"
        try:
            response = self.get(url)
            return response.json().get("data", {})
        except (ValueError, KeyError) as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error("获取通知信息失败!")
            return {}

    def get_steam_trade(self) -> Dict[str, Any]:
        """获取Steam Trade信息"""
        url = "https://buff.163.com/api/market/steam_trade"
        try:
            response = self.get(url)
            return response.json().get("data", {})
        except (ValueError, KeyError) as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error("获取Steam Trade信息失败!")
            return {}

    def on_sale(self, assets: List[BuffOnSaleAsset], app_id: str = "730", game: str = "csgo") -> (
            Tuple)[List[str], Dict[str, Any]]:
        """
        上架商品

        :param game: 游戏名称 (csgo)
        :param app_id: 游戏ID (730)
        :param assets: 上架的资产列表
        :return: 成功上架的商品ID列表和存在问题的商品ID及其错误信息
        """
        url = "https://buff.163.com/api/market/sell_order/create/manual_plus"
        payload = {
            "appid": app_id,
            "game": game,
            "assets": [asset.model_dump(exclude_none=True) for asset in assets],
        }
        headers = self._refresh_csrf_token()

        try:
            response = self.post(url, json=payload, headers=headers)
            response_data = response.json()

            if response_data.get("code") != "OK":
                raise Exception(response_data.get("msg", "未知错误"))

            success, problem_assets = self._process_sale_response(response_data)
            return success, problem_assets
        except (ValueError, KeyError, Exception) as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error(f"上架失败: {e}")
            return [], {}

    @staticmethod
    def _process_sale_response( response_data: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
        """处理上架响应"""
        success = []
        problem_assets = {}
        for good_id, status in response_data.get("data", {}).items():
            if status == "OK":
                success.append(good_id)
            else:
                problem_assets[good_id] = status
        return success, problem_assets

    @staticmethod
    def process_problem(response: requests.Response) -> Tuple[int, Dict[str, Any]]:
        """处理通用问题响应"""
        if response.json().get("code") != "OK":
            raise Exception(response.json().get("msg", "未知错误"))
        success = 0
        problem = {}
        for key, status in response.json().get("data", {}).items():
            if status == "OK":
                success += 1
            else:
                problem[key] = status
        return success, problem

    def cancel_sale(self, sell_orders: List[str], exclude_sell_orders: Optional[List[str]] = None) -> Tuple[int, Dict[str, Any]]:
        """
        取消销售订单

        :param sell_orders: 需要取消的销售订单ID列表
        :param exclude_sell_orders: 需要排除的销售订单ID列表
        :return: 取消成功的数量和存在问题的订单ID及其错误信息
        """
        if exclude_sell_orders is None:
            exclude_sell_orders = []

        url = "https://buff.163.com/api/market/sell_order/cancel"
        headers = self._refresh_csrf_token()
        success_total = 0
        problem_total = {}

        for i in range(0, len(sell_orders), 50):
            batch = sell_orders[i:i + 50]
            payload = {
                "game": "csgo",
                "sell_orders": batch,
                "exclude_sell_orders": exclude_sell_orders,
            }
            try:
                response = self.post(url, json=payload, headers=headers)
                _success, problem = self.process_problem(response)
                success_total += _success
                problem_total.update(problem)
            except Exception as e:
                handle_caught_exception(e, "BuffAccount")
                self.logger.error(f"取消销售订单失败: {e}")

        return success_total, problem_total

    def get_on_sale(self, page_num: int = 1, page_size: int = 1000, mode: str = "2,5", fold: str = "0",
                    game: str = "csgo", app_id: str = "730") -> Dict[str, Any]:
        """获取在售商品"""
        url = "https://buff.163.com/api/market/sell_order/on_sale"
        params = {
            "page_num": page_num,
            "page_size": page_size,
            "mode": mode,
            "fold": fold,
            "game": game,
            "appid": app_id
        }
        try:
            response = self.get(url, params=params)
            return response.json().get("data", {})
        except (ValueError, KeyError) as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error("获取在售商品失败!")
            return {}

    def change_price(self, sell_orders: List[str], app_id: str = "730") -> Tuple[int, Dict[str, Any]]:
        """
        更改销售订单价格

        :param sell_orders: 需要更改价格的销售订单ID列表
        :return: 更改成功的数量和存在问题的订单ID及其错误信息
        """
        url = "https://buff.163.com/api/market/sell_order/change"
        headers = self._refresh_csrf_token()
        success_total = 0
        problems = {}

        for i in range(0, len(sell_orders), 50):
            batch = sell_orders[i:i + 50]
            payload = {
                "appid": app_id,
                "sell_orders": batch,
            }
            try:
                response = self.post(url, json=payload, headers=headers)
                _success, problem = self.process_problem(response)
                success_total += _success
                problems.update(problem)
            except Exception as e:
                handle_caught_exception(e, "BuffAccount")
                self.logger.error(f"更改价格失败: {e}")

        return success_total, problems

    def _refresh_csrf_token(self) -> dict[Any, Any] | CaseInsensitiveDict[str | bytes]:
        """刷新CSRF Token并返回更新后的请求头"""
        url = "https://buff.163.com/api/market/steam_trade"
        try:
            self.get(url)
            csrf_token = self.session.cookies.get("csrf_token", "")
            if not csrf_token:
                self.logger.error("未能刷新CSRF Token")
                return {}
            headers = copy.deepcopy(self.session.headers)
            headers.update({
                "X-CSRFToken": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/json",
                "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
            })
            return headers
        except RequestException as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error("刷新CSRF Token失败!")
            return {}

    def get_buy_history(self, game: str, page_size: int = 300) -> Dict[str, Any]:
        """
        获取购买记录

        :param page_size: 每次请求的数量
        :param game: 游戏名称
        :return: 购买记录的字典
        """
        history_file_path = os.path.join(SESSION_FOLDER, f"buy_history_{game}.json")
        local_history = self._load_local_history(history_file_path)

        page_num = 1
        result = {}
        while True:
            self.logger.debug(f"正在获取{game} 购买记录, 页数: {page_num}")
            url = "https://buff.163.com/api/market/buy_order/history"
            params = {
                "page_num": page_num,
                "page_size": page_size,
                "game": game
            }
            try:
                response = self.get(url, params=params)
                response_json = response.json()
                if response_json.get("code") != "OK":
                    self.logger.error("获取历史订单失败")
                    self.logger.info(f"当前每次请求的数量: {page_size}, 请尝试减少数量")
                    break
                items = response_json.get("data", {}).get("items", [])
                should_break = False
                for item in items:
                    if item.get('state') != 'SUCCESS':
                        continue
                    key_str = self.form_key_str(item)
                    if key_str not in result:
                        result[key_str] = item.get("price")
                    if key_str in local_history and item.get("price") == local_history[key_str]:
                        self.logger.info("后面没有新的订单了, 无需继续获取")
                        should_break = True
                        break
                if len(items) < page_size or should_break:
                    break
                page_num += 1
                self.logger.info("避免被封号, 休眠15秒")
                time.sleep(15)
            except Exception as e:
                handle_caught_exception(e, "BuffAccount")
                self.logger.error(f"获取购买记录失败: {e}")
                break

        # 合并本地历史记录
        if local_history:
            for key, price in local_history.items():
                result.setdefault(key, price)

        # 保存最新的购买记录
        if result:
            self._save_local_history(history_file_path, result)

        return result

    def get_all_buff_inventory(self, game: str = "csgo") -> List[Dict[str, Any]]:
        """
        获取BUFF库存

        :param game: 游戏名称
        :return: 库存列表
        """
        self.logger.info(f"正在获取 {game} BUFF 库存...")
        page_num = 1
        page_size = 300
        sort_by = "time.desc"
        state = "all"
        force = 0
        force_wear = 0
        url = "https://buff.163.com/api/market/steam_inventory"
        total_items = []
        while True:
            params = {
                "page_num": page_num,
                "page_size": page_size,
                "sort_by": sort_by,
                "state": state,
                "force": force,
                "force_wear": force_wear,
                "game": game
            }
            try:
                self.logger.info("避免被封号, 休眠15秒")
                time.sleep(15)
                response = self.get(url, params=params)
                response_json = response.json()
                if response_json.get("code") == "OK":
                    items = response_json.get("data", {}).get("items", [])
                    total_items.extend(items)
                    if len(items) < page_size:
                        break
                    page_num += 1
                else:
                    self.logger.error(response_json)
                    break
            except Exception as e:
                handle_caught_exception(e, "BuffAccount")
                self.logger.error(f"获取BUFF库存失败: {e}")
                break
        return total_items

    @staticmethod
    def form_key_str(item: Dict[str, Any]) -> str:
        """
        根据订单信息生成唯一的键字符串

        :param item: 订单项
        :return: 唯一键字符串
        """
        keys = ["appid", "assetid", "classid", "contextid"]
        key_list = [str(item["asset_info"].get(key, "")) for key in keys]
        return "_".join(key_list)

    def _load_local_history(self, history_file_path: str) -> Dict[str, Any]:
        """加载本地购买记录"""
        try:
            if os.path.exists(history_file_path):
                with open(history_file_path, "r", encoding=get_encoding(history_file_path)) as f:
                    return json.load(f)
        except Exception as e:
            self.logger.debug(f"读取本地购买记录失败, 错误信息: {e}", exc_info=True)
        return {}

    def _save_local_history(self, history_file_path: str, history: Dict[str, Any]) -> None:
        """保存购买记录到本地"""
        try:
            with open(history_file_path, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=4)
        except Exception as e:
            self.logger.error(f"保存购买记录失败: {e}")

    def update_asset_remarks(self, app_id: str, assets: List[Dict[str, str]]) -> bool:
        """
        更新资产备注

        :param app_id: 游戏ID
        :param assets: 资产列表，包含assetid和remark
        :return: 是否成功
        """
        url = "https://buff.163.com/api/market/steam_asset_remark/change"
        payload = {
            "appid": app_id,
            "assets": assets
        }
        headers = self._refresh_csrf_token()
        try:
            response = self.post(url, json=payload, headers=headers)
            response_json = response.json()
            if response_json.get("code") == "OK":
                return True
            else:
                self.logger.error(f"更新备注失败: {response_json.get('msg', '未知错误')}")
                return False
        except Exception as e:
            handle_caught_exception(e, "BuffAccount")
            self.logger.error(f"更新备注请求失败: {e}")
            return False
