# plugins/BuffAutoAcceptOffer.py

import datetime
import json
import os
import pickle
import time
from typing import Any, Dict, List, Optional, Tuple, Set

import apprise
from apprise import AppriseAsset

from utils.logger import PluginLogger, handle_caught_exception
from utils.static import (
    APPRISE_ASSET_FOLDER,
    BUFF_COOKIES_FILE_PATH,
    SESSION_FOLDER,
    SUPPORT_GAME_TYPES,
)
from utils.tools import exit_code, get_encoding

from BuffApi import BuffAccount


class BuffAutoAcceptOffer:
    """
    BUFF自动接受报价插件
    """

    def __init__(self, logger: PluginLogger, steam_client: Any, steam_client_mutex: Any, config: Dict[str, Any]):
        self.logger = logger
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config

        # 初始化BuffAccount
        buff_cookie = self._read_buff_cookie()
        self.buff_account = BuffAccount(steam_client=self.steam_client)

        self.asset = AppriseAsset(plugin_paths=[os.path.join(os.path.dirname(__file__), "..", APPRISE_ASSET_FOLDER)])
        self.order_info: Dict[str, Dict[str, Any]] = {}
        self.lowest_on_sale_price_cache: Dict[str, Dict[str, Any]] = {}
        self.ignored_offers: List[str] = []

    def _read_buff_cookie(self) -> str:
        """读取BUFF的cookie"""
        try:
            with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
                buff_cookie = f.read().strip()
                self.logger.info("已检测到BUFF cookies")
                return buff_cookie
        except FileNotFoundError:
            self.logger.error(f"未找到 {BUFF_COOKIES_FILE_PATH}, 请检查文件路径!")
            exit_code.set(1)
            raise
        except Exception as e:
            handle_caught_exception(e, "BuffAutoAcceptOffer")
            self.logger.error(f"读取 {BUFF_COOKIES_FILE_PATH} 时发生错误: {e}")
            exit_code.set(1)
            raise

    def init_plugin(self) -> bool:
        """初始化插件，包括登录BUFF和验证账户"""
        self.logger.info("BUFF自动接受报价插件已启动. 请稍候...")
        self.logger.info("正在准备登录至BUFF...")

        try:
            # 初始化BuffAccount
            if not self.buff_account.init():
                self.logger.error("BUFF会话无效，初始化失败!")
                return False

            # 获取用户昵称以验证登录状态
            username = self.buff_account.get_user_nickname()
            self.logger.info(f"已登录至BUFF 用户名: {username}")

            # 验证Steam账户与BUFF绑定的SteamID是否一致
            buff_steamid = self.buff_account.get_buff_bind_steamid()
            steamid = self.steam_client.get_steam64id_from_cookies()
            if steamid != buff_steamid:
                self.logger.error("当前登录账号与BUFF绑定的Steam账号不一致!")
                exit_code.set(1)
                return False

            # 启用买家发起交易报价功能
            self.buff_account.require_buyer_send_offer()

            return True
        except Exception as e:
            handle_caught_exception(e, "BuffAutoAcceptOffer")
            self.logger.error("初始化插件时发生错误!")
            exit_code.set(1)
            return False

    def send_notification(self, title: str, body: str) -> None:
        """
        发送通知消息
        """
        try:
            apprise_obj = apprise.Apprise(asset=self.asset)
            servers = self.config.get("buff_auto_accept_offer", {}).get("servers", [])
            for server in servers:
                apprise_obj.add(server)
            apprise_obj.notify(title=title, body=body)
            self.logger.info("通知已发送")
        except Exception as e:
            handle_caught_exception(e, "BuffAutoAcceptOffer")
            self.logger.error(f"发送通知失败: {e}")

    def fetch_notifications(self) -> None:
        """
        获取BUFF的通知信息，包括待发货订单。
        """
        try:
            notification_data = self.buff_account.get_notification()
            to_deliver_order = notification_data.get("to_deliver_order", {})

            csgo_deliver = int(to_deliver_order.get("csgo", 0))
            dota2_deliver = int(to_deliver_order.get("dota2", 0))
            total_deliver = csgo_deliver + dota2_deliver

            if total_deliver > 0:
                self.logger.info(f"检测到 {total_deliver} 个待发货请求!")
                self.logger.info(f"CSGO待发货: {csgo_deliver} 个")
                self.logger.info(f"DOTA2待发货: {dota2_deliver} 个")
        except Exception as e:
            handle_caught_exception(e, "BuffAutoAcceptOffer")
            self.logger.error("获取通知信息失败!")

    def fetch_trades(self) -> List[Dict[str, Any]]:
        """
        获取待处理的交易报价
        """
        try:
            trades = self.buff_account.get_steam_trade()
            self.logger.info(f"查找到 {len(trades)} 个待处理的BUFF未发货订单!")
            return trades
        except Exception as e:
            handle_caught_exception(e, "BuffAutoAcceptOffer")
            self.logger.error("获取待处理的交易报价失败!")
            return []

    def fetch_trade_offers_to_confirm(self) -> Set[str]:
        """
        获取待确认的供应订单
        """
        try:
            trade_offer_to_confirm = set()
            for game in SUPPORT_GAME_TYPES:
                game_name = game.get("game", "")
                app_id = game.get("app_id", "")
                sell_listing = self.buff_account.get_sell_order(goods_id=game_name, game_name=game_name, app_id=app_id)
                trade_offers = sell_listing.get("items", [])
                for trade_offer in trade_offers:
                    trade_offer_id = trade_offer.get("tradeofferid")
                    if trade_offer_id:
                        trade_offer_to_confirm.add(trade_offer_id)
                self.logger.info("为了避免访问接口过于频繁，休眠5秒...")
                time.sleep(5)
            self.logger.info(f"查找到 {len(trade_offer_to_confirm)} 个待处理的BUFF待确认供应订单!")
            return trade_offer_to_confirm
        except Exception as e:
            handle_caught_exception(e, "BuffAutoAcceptOffer")
            self.logger.error("获取待确认供应订单失败!")
            return set()

    def get_order_info(self, trades: List[Dict[str, Any]]) -> None:
        """
        获取卖出订单信息并缓存
        """
        try:
            self.buff_account.get_order_info(trades)
        except Exception as e:
            handle_caught_exception(e, "BuffAutoAcceptOffer")
            self.logger.error("获取卖出订单信息失败!")

    def should_accept_offer(self, trade: Dict[str, Any]) -> bool:
        """
        判断是否应该接受交易报价
        """
        try:
            sell_protection = self.config.get("buff_auto_accept_offer", {}).get("sell_protection", False)
            protection_price_percentage = self.config.get("buff_auto_accept_offer", {}).get("protection_price_percentage", 0.9)
            protection_price = self.config.get("buff_auto_accept_offer", {}).get("protection_price", 30.0)

            if sell_protection:
                self.logger.info("正在检查交易金额...")
                goods_id = str(next(iter(trade.get("goods_infos", {})), ""))
                price = float(self.buff_account.order_info.get(trade.get("tradeofferid", ""), {}).get("price", "0"))
                other_lowest_price = self.buff_account.get_lowest_on_sale_price(goods_id)

                if other_lowest_price == -1:
                    self.logger.error("无法获取最低价格，跳过此交易报价")
                    return False

                if self.is_price_protected(price, other_lowest_price, protection_price_percentage, protection_price):
                    self.logger.error("交易金额过低, 跳过此交易报价")
                    self.send_protection_notification(trade)
                    return False

            return True
        except Exception as e:
            handle_caught_exception(e, "BuffAutoAcceptOffer")
            self.logger.error(f"判断是否接受报价时发生错误: {e}")
            return False

    @staticmethod
    def is_price_protected(price: float, other_lowest_price: float, percentage: float, protection_price: float) -> bool:
        """
        判断价格是否符合保护策略
        """
        return price < other_lowest_price * percentage and other_lowest_price > protection_price

    def send_protection_notification(self, trade: Dict[str, Any]) -> None:
        """
        发送交易保护相关的通知
        """
        protection_notification = self.config.get("buff_auto_accept_offer", {}).get("protection_notification", {})
        if protection_notification:
            title = self.buff_account.format_str(protection_notification.get("title", ""), trade)
            body = self.buff_account.format_str(protection_notification.get("body", ""), trade)
            self.send_notification(title, body)

    def process_trades(self, trades: List[Dict[str, Any]]) -> None:
        """
        处理交易报价，包括接受报价和发送通知
        """
        if not trades:
            self.logger.info("没有待处理的交易报价。")
            return

        self.get_order_info(trades)

        for idx, trade in enumerate(trades, start=1):
            offer_id = trade.get("tradeofferid")
            self.logger.info(f"正在处理第 {idx} 个交易报价 报价ID {offer_id}")
            if offer_id and offer_id not in self.ignored_offers:
                if not self.should_accept_offer(trade):
                    continue
                try:
                    self.steam_client.accept_trade_offer(offer_id)
                    self.ignored_offers.append(offer_id)
                    self.logger.info("接受完成! 已将此交易报价加入忽略名单!")

                    # 发送卖出通知
                    sell_notification = self.config.get("buff_auto_accept_offer", {}).get("sell_notification", {})
                    if sell_notification:
                        title = self.buff_account.format_str(sell_notification.get("title", ""), trade)
                        body = self.buff_account.format_str(sell_notification.get("body", ""), trade)
                        self.send_notification(title, body)

                    self.logger.info("为了避免频繁访问Steam接口, 等待5秒后继续...")
                    time.sleep(5)
                except Exception as e:
                    handle_caught_exception(e, "BuffAutoAcceptOffer")
                    self.logger.error(f"无法接受报价: {e}")
            else:
                self.logger.info("该报价已经被处理过, 跳过.")

    def process_trade_offers(self, trade_offer_to_confirm: Set[str]) -> None:
        """
        处理待确认的供应订单
        """
        for trade_offer_id in trade_offer_to_confirm:
            if trade_offer_id not in self.ignored_offers:
                try:
                    offer = self.steam_client.get_trade_offer(trade_offer_id)
                    offer_state = offer.get("response", {}).get("offer", {}).get("trade_offer_state", -1)

                    if offer_state == 9:  # 已完成
                        with self.steam_client_mutex:
                            self.steam_client.confirm_transaction(trade_offer_id)
                        self.ignored_offers.append(trade_offer_id)
                        self.logger.info(f"令牌完成! ({trade_offer_id}) 已将此交易报价加入忽略名单!")
                    else:
                        self.logger.info(f"令牌未完成! ({trade_offer_id}), 报价状态异常 ({offer_state})")
                except Exception as e:
                    handle_caught_exception(e, "BuffAutoAcceptOffer")
                    self.logger.error(f"处理待确认供应订单失败 ({trade_offer_id}): {e}")

                self.logger.info("为了避免频繁访问Steam接口, 等待5秒后继续...")
                time.sleep(5)
            else:
                self.logger.info("该报价已经被处理过, 跳过.")

    def run(self) -> int:
        """
        插件的主执行方法
        """
        if not self.init_plugin():
            return 1

        interval = self.config.get("buff_auto_accept_offer", {}).get("interval", 300)

        while True:
            try:
                # 获取BUFF的通知信息
                self.fetch_notifications()

                # 获取待处理的交易报价
                trades = self.fetch_trades()

                # 处理交易报价
                self.process_trades(trades)

                # 获取待确认的供应订单
                trade_offer_to_confirm = self.fetch_trade_offers_to_confirm()

                # 处理待确认的供应订单
                self.process_trade_offers(trade_offer_to_confirm)

            except Exception as e:
                handle_caught_exception(e, "BuffAutoAcceptOffer")
                self.logger.info("出现未知错误, 稍后再试!")

            self.logger.info(f"将在{interval}秒后再次检查待发货订单信息!")
            time.sleep(interval)
