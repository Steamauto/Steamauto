import json
import os
import pickle
import time

import json5
from requests.exceptions import ProxyError
from steampy.exceptions import InvalidCredentials, ConfirmationExpected

import uuyoupinapi

from utils.logger import handle_caught_exception
from utils.static import UU_TOKEN_FILE_PATH, SESSION_FOLDER
from utils.tools import get_encoding, exit_code


class UUAutoAcceptOffer:
    def __init__(self, logger, steam_client, steam_client_mutex, config):
        self.logger = logger
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config

    def init(self) -> bool:
        if not os.path.exists(UU_TOKEN_FILE_PATH):
            with open(UU_TOKEN_FILE_PATH, "w", encoding="utf-8") as f:
                f.write("")
            return True
        return False

    def exec(self):
        uuyoupin = None
        with open(UU_TOKEN_FILE_PATH, "r", encoding=get_encoding(UU_TOKEN_FILE_PATH)) as f:
            try:
                uuyoupin = uuyoupinapi.UUAccount(f.read())
                self.logger.info("[UUAutoAcceptOffer] 悠悠有品登录完成, 用户名: " + uuyoupin.get_user_nickname())
                uuyoupin.send_device_info()
            except KeyError as e:
                handle_caught_exception(e)
                self.logger.error("[UUAutoAcceptOffer] 悠悠有品登录失败! 请检查token是否正确! ")
                self.logger.error("[UUAutoAcceptOffer] 由于登录失败，插件将自动退出")
                exit_code.set(1)
                return 1
        ignored_offer = []
        interval = self.config["uu_auto_accept_offer"]["interval"]
        if uuyoupin is not None:
            while True:
                try:
                    with self.steam_client_mutex:
                        if not self.steam_client.is_session_alive():
                            self.logger.info("[UUAutoAcceptOffer] Steam会话已过期, 正在重新登录...")
                            self.steam_client._session.cookies.clear()
                            self.steam_client.login(
                                self.steam_client.username,
                                self.steam_client._password,
                                json5.dumps(self.steam_client.steam_guard),
                            )
                            self.logger.info("[UUAutoAcceptOffer] Steam会话已更新")
                            steam_session_path = os.path.join(SESSION_FOLDER, self.steam_client.username.lower() + ".pkl")
                            with open(steam_session_path, "wb") as f:
                                pickle.dump(self.steam_client.session, f)
                    uuyoupin.send_device_info()
                    self.logger.info("[UUAutoAcceptOffer] 正在检查悠悠有品待发货信息...")
                    uu_wait_deliver_list = uuyoupin.get_wait_deliver_list()
                    len_uu_wait_deliver_list = len(uu_wait_deliver_list)
                    self.logger.info("[UUAutoAcceptOffer] " + str(len_uu_wait_deliver_list) + "个悠悠有品待发货订单")
                    if len(uu_wait_deliver_list) != 0:
                        for item in uu_wait_deliver_list:
                            accepted = False
                            self.logger.info(
                                f"[UUAutoAcceptOffer] 正在接受悠悠有品待发货报价, 商品名: {item['item_name']}, " f"报价ID: {item['offer_id']}"
                            )
                            if item["offer_id"] is None:
                                self.logger.warning("[UUAutoAcceptOffer] 此订单为需要手动发货的订单, 无法处理, 跳过此订单! ")
                            elif item["offer_id"] not in ignored_offer:
                                try:
                                    with self.steam_client_mutex:
                                        self.steam_client.accept_trade_offer(str(item["offer_id"]))
                                    ignored_offer.append(item["offer_id"])
                                    self.logger.info(f'[UUAutoAcceptOffer] 接受报价[{str(item["offer_id"])}]完成!')
                                    accepted = True
                                except ProxyError:
                                    self.logger.error("[UUAutoAcceptOffer] 代理异常, 本软件可不需要代理或任何VPN")
                                    self.logger.error("[UUAutoAcceptOffer] 可以尝试关闭代理或VPN后重启软件")
                                except (ConnectionError, ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError):
                                    self.logger.error("[UUAutoAcceptOffer] 网络异常, 请检查网络连接")
                                    self.logger.error("[UUAutoAcceptOffer] 这个错误可能是由于代理或VPN引起的, 本软件可无需代理或任何VPN")
                                    self.logger.error("[UUAutoAcceptOffer] 如果你正在使用代理或VPN, 请尝试关闭后重启软件")
                                    self.logger.error("[UUAutoAcceptOffer] 如果你没有使用代理或VPN, 请检查网络连接")
                                except InvalidCredentials as e:
                                    self.logger.error("[UUAutoAcceptOffer] mafile有问题, 请检查mafile是否正确" "(尤其是identity_secret)")
                                    self.logger.error(str(e))
                                except ConfirmationExpected as e:
                                    handle_caught_exception(e)
                                    self.logger.error("[UUAutoAcceptOffer] Steam Session已经过期, 请删除session文件夹并重启Steamauto")
                                except ValueError as e:
                                    self.logger.error("[BuffAutoAcceptOffer] Steam 宵禁限制, 请稍后再试!")
                                    handle_caught_exception(e)
                                except Exception as e:
                                    handle_caught_exception(e)
                                    self.logger.error("[UUAutoAcceptOffer] Steam异常, 暂时无法接受报价, 请稍后再试! ")
                            else:
                                self.logger.info(
                                    "[UUAutoAcceptOffer] 此交易报价已经被Steamauto处理过, 出现此提示的原因" "是悠悠系统延迟或者该订单为批量购买订单.这不是一个报错!"
                                )
                            if (uu_wait_deliver_list.index(item) != len_uu_wait_deliver_list - 1) and accepted:
                                self.logger.info("[UUAutoAcceptOffer] 为了避免频繁访问Steam接口, 等待5秒后继续...")
                                time.sleep(5)
                                
                    if self.config["uu_auto_accept_offer"].get("auto_confirm", False) == True:
                        with self.steam_client_mutex:
                            trade_summary = self.steam_client.get_trade_offers_summary()["response"]
                        self.logger.info("[UUAutoAcceptOffer] 检测到有%d个待确认的发送交易报价" % trade_summary["pending_sent_count"])
                        if trade_summary["pending_sent_count"] > 0:
                            trade_offers = self.steam_client.get_trade_offers(merge=False)["response"]
                            if len(trade_offers["trade_offers_sent"]) > 0:
                                for trade_offer in trade_offers["trade_offers_sent"]:
                                    self.steam_client._confirm_transaction(trade_offer["tradeofferid"])
                                    self.logger.info(f'[UUAutoAcceptOffer] 确认发送报价[{str(trade_offer["tradeofferid"])}]完成!')
                                    ignored_offer.append(trade_offer["tradeofferid"])
                                    time.sleep(5)
                except TypeError as e:
                    handle_caught_exception(e)
                    self.logger.error("[UUAutoAcceptOffer] 悠悠有品待发货信息获取失败, 请检查账号是否正确! 插件将自动退出")
                    exit_code.set(1)
                    return 1
                except Exception as e:
                    self.logger.error(e, exc_info=True)
                    self.logger.info("[UUAutoAcceptOffer] 出现未知错误, 稍后再试! ")
                    try:
                        uuyoupin.get_user_nickname()
                    except KeyError as e:
                        handle_caught_exception(e)
                        self.logger.error("[UUAutoAcceptOffer] 检测到悠悠有品登录已经失效,请重新登录")
                        self.logger.error("[UUAutoAcceptOffer] 由于登录失败，插件将自动退出")
                        exit_code.set(1)
                        return 1
                self.logger.info("[UUAutoAcceptOffer] 将在{0}秒后再次检查待发货订单信息!".format(str(interval)))
                time.sleep(interval)
