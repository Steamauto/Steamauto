import os
import pickle
import time
from webbrowser import get

import json5

import uuyoupinapi
from utils.logger import PluginLogger, handle_caught_exception
from utils.static import SESSION_FOLDER, UU_TOKEN_FILE_PATH
from utils.tools import exit_code
from utils.uu_helper import get_valid_token_for_uu


class UUAutoAcceptOffer:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.logger = PluginLogger("UUAutoAcceptOffer")
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config

    def init(self) -> bool:
        token = get_valid_token_for_uu()
        if not token:
            self.logger.error("悠悠有品登录失败！即将关闭程序！")
            exit_code.set(1)
            return True
        return False

    def exec(self):
        uuyoupin = None
        token = get_valid_token_for_uu()
        if not token:
            self.logger.error("由于登录失败，插件将自动退出")
            exit_code.set(1)
            return 1
        else:
            uuyoupin = uuyoupinapi.UUAccount(token)
        ignored_offer = []
        interval = self.config["uu_auto_accept_offer"]["interval"]
        if uuyoupin is not None:
            while True:
                try:
                    uuyoupin.send_device_info()
                    self.logger.info("正在检查悠悠有品待发货信息...")
                    uu_wait_deliver_list = uuyoupin.get_wait_deliver_list()
                    len_uu_wait_deliver_list = len(uu_wait_deliver_list)
                    self.logger.info("" + str(len_uu_wait_deliver_list) + "个悠悠有品待发货订单")
                    if len(uu_wait_deliver_list) != 0:
                        with self.steam_client_mutex:
                            if not self.steam_client.is_session_alive():
                                self.logger.info("Steam会话已过期, 正在重新登录...")
                                self.steam_client._session.cookies.clear()
                                self.steam_client.login(
                                    self.steam_client.username,
                                    self.steam_client._password,
                                    json5.dumps(self.steam_client.steam_guard),
                                )
                                self.logger.info("Steam会话已更新")
                                steam_session_path = os.path.join(
                                    SESSION_FOLDER,
                                    self.steam_client.username.lower() + ".pkl",
                                )
                                with open(steam_session_path, "wb") as f:
                                    pickle.dump(self.steam_client.session, f)
                        for item in uu_wait_deliver_list:
                            accepted = False
                            self.logger.info(
                                f"正在接受悠悠有品待发货报价, 商品名: {item['item_name']}, "
                                f"报价ID: {item['offer_id']}"
                            )
                            if item["offer_id"] is None:
                                self.logger.warning("此订单为需要手动发货(或异常)的订单, 不能自动处理, 跳过此订单! ")
                            elif item["offer_id"] not in ignored_offer:
                                try:
                                    with self.steam_client_mutex:
                                        self.steam_client.accept_trade_offer(str(item["offer_id"]))
                                    ignored_offer.append(item["offer_id"])
                                    self.logger.info(f'接受报价[{str(item["offer_id"])}]完成!')
                                    accepted = True
                                except Exception as e:
                                    handle_caught_exception(e, "UUAutoAcceptOffer")
                                    self.logger.error("Steam异常, 暂时无法接受报价, 请稍后再试! ")
                            else:
                                self.logger.info(
                                    "此交易报价已经被Steamauto处理过, 出现此提示的原因"
                                    "是悠悠系统延迟或者该订单为批量购买订单.这不是一个报错!"
                                )
                            if (uu_wait_deliver_list.index(item) != len_uu_wait_deliver_list - 1) and accepted:
                                self.logger.info("为了避免频繁访问Steam接口, 等待5秒后继续...")
                                time.sleep(5)
                except Exception as e:
                    handle_caught_exception(e, "UUAutoAcceptOffer")
                    self.logger.info("出现未知错误, 稍后再试! ")
                    try:
                        uuyoupin.get_user_nickname()
                    except KeyError as e:
                        handle_caught_exception(e, "UUAutoAcceptOffer")
                        self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                        self.logger.error("由于登录失败，插件将自动退出")
                        exit_code.set(1)
                        return 1
                self.logger.info("将在{0}秒后再次检查待发货订单信息!".format(str(interval)))
                time.sleep(interval)
