import json
import time

from requests.exceptions import ProxyError
from steampy.exceptions import InvalidCredentials, ConfirmationExpected


class SteamAutoAcceptOffer:
    def __init__(self, logger, steam_client, steam_client_mutex, config):
        self.logger = logger
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
                        self.logger.info("[SteamAutoAcceptOffer] Steam会话已过期, 正在重新登录...")
                        self.steam_client.login(
                            self.steam_client.username, self.steam_client._password, json.dumps(self.steam_client.steam_guard)
                        )
                        self.logger.info("[SteamAutoAcceptOffer] Steam会话已更新")
                with self.steam_client_mutex:
                    trade_summary = self.steam_client.get_trade_offers_summary()["response"]
                self.logger.info("[SteamAutoAcceptOffer] 检测到有%d个待处理的交易报价" % trade_summary["pending_received_count"])
                if trade_summary["pending_received_count"] > 0:
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
                                    f'[SteamAutoAcceptOffer] 检测到报价[{trade_offer["tradeofferid"]}]' f"属于礼物报价，正在接受报价..."
                                )
                                try:
                                    with self.steam_client_mutex:
                                        self.steam_client.accept_trade_offer(trade_offer["tradeofferid"])
                                except ProxyError:
                                    self.logger.error("[SteamAutoAcceptOffer] 代理异常, 本软件可不需要代理或任何VPN")
                                    self.logger.error("[SteamAutoAcceptOffer] 可以尝试关闭代理或VPN后重启软件")
                                except (ConnectionError, ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError):
                                    self.logger.error("[SteamAutoAcceptOffer] 网络异常, 请检查网络连接")
                                    self.logger.error("[SteamAutoAcceptOffer] 这个错误可能是由于代理或VPN引起的, 本软件可无需代理或任何VPN")
                                    self.logger.error("[SteamAutoAcceptOffer] 如果你正在使用代理或VPN, 请尝试关闭后重启软件")
                                    self.logger.error("[SteamAutoAcceptOffer] 如果你没有使用代理或VPN, 请检查网络连接")
                                except InvalidCredentials as e:
                                    self.logger.error("[SteamAutoAcceptOffer] mafile有问题, 请检查mafile是否正确" "(尤其是identity_secret)")
                                    self.logger.error(str(e))
                                except ConfirmationExpected:
                                    self.logger.error("[UUAutoAcceptOffer] Steam Session已经过期, 请删除session文件夹并重启Steamauto")
                                except Exception as e:
                                    self.logger.error(e, exc_info=True)
                                    self.logger.error("[SteamAutoAcceptOffer] Steam异常! 稍后再试...")
                                self.logger.info(f'[SteamAutoAcceptOffer] 报价[{trade_offer["tradeofferid"]}]接受成功！')
                            else:
                                self.logger.info(
                                    f'[SteamAutoAcceptOffer] 检测到报价[{trade_offer["tradeofferid"]}]' f"需要支出物品，自动跳过处理"
                                )
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.error("[SteamAutoAcceptOffer] 发生未知错误！稍后再试...")
            time.sleep(self.config["steam_auto_accept_offer"]["interval"])
