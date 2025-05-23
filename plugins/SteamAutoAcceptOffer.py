import os
import pickle
import time

from utils.logger import PluginLogger, handle_caught_exception
from utils.static import SESSION_FOLDER


class SteamAutoAcceptOffer:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.logger = PluginLogger('SteamAutoAcceptOffer')
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config

    def init(self):
        return False

    def exec(self):
        while True:
            try:
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("Steam会话已过期, 正在重新登录...")
                        self.steam_client.relogin()
                        self.logger.info("Steam会话已更新")
                        steam_session_path = os.path.join(SESSION_FOLDER, self.steam_client.username.lower() + ".pkl")
                        with open(steam_session_path, "wb") as f:
                            pickle.dump(self.steam_client.session, f)
                self.logger.info('正在检查待处理的交易报价...')
                with self.steam_client_mutex:
                    trade_summary = self.steam_client.get_trade_offers(merge=False)["response"]
                self.logger.info(f"检测到有{len(trade_summary['trade_offers_received'])}个待处理的交易报价")
                if len(trade_summary["trade_offers_received"]) > 0:
                    with self.steam_client_mutex:
                        trade_offers = self.steam_client.get_trade_offers(merge=False)["response"]
                    if len(trade_offers["trade_offers_received"]) > 0:
                        for trade_offer in trade_offers["trade_offers_received"]:
                            self.logger.debug(
                                f'\n报价[{trade_offer["tradeofferid"]}] '
                                f'\n支出: {len(trade_offer.get("items_to_give", {}))} 个物品'
                                f'\n接收: {len(trade_offer.get("items_to_receive", {}))} 个物品'
                            )
                            if len(trade_offer.get("items_to_give", {})) == 0:
                                self.logger.info(
                                    f'检测到报价[{trade_offer["tradeofferid"]}]' f"属于礼物报价，正在接受报价..."
                                )
                                try:
                                    with self.steam_client_mutex:
                                        self.steam_client.accept_trade_offer(trade_offer["tradeofferid"])
                                except Exception as e:
                                    handle_caught_exception(e, "SteamAutoAcceptOffer", known=True)
                                    self.logger.error("Steam异常! 稍后再试...")
                                self.logger.info(f'报价[{trade_offer["tradeofferid"]}]接受成功！')
                            else:
                                self.logger.info(
                                    f'检测到报价[{trade_offer["tradeofferid"]}]' f"需要支出物品，自动跳过处理"
                                )
            except Exception as e:
                handle_caught_exception(e, "SteamAutoAcceptOffer")
                self.logger.error("发生未知错误！稍后再试...")
            time.sleep(self.config["steam_auto_accept_offer"]["interval"])
