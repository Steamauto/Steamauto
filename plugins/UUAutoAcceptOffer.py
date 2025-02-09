import time

import uuyoupinapi
from utils.logger import PluginLogger, handle_caught_exception
from utils.steam_client import accept_trade_offer
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
                        for item in uu_wait_deliver_list:
                            accepted = False
                            self.logger.info(
                                f"正在接受悠悠有品待发货报价, 商品名: {item['item_name']}, " f"报价ID: {item['offer_id']}"
                            )
                            if item["offer_id"] is None:
                                self.logger.warning("此订单为需要手动发货(或异常)的订单, 不能自动处理, 跳过此订单! ")
                            elif item["offer_id"] in ignored_offer:
                                self.logger.info(
                                    "此交易报价已经被Steamauto处理过, 出现此提示的原因"
                                    "是悠悠系统延迟或者该订单为批量购买订单.这不是一个报错!"
                                )
                            else:
                                if accept_trade_offer(self.steam_client, self.steam_client_mutex, str(item["offer_id"])):
                                    ignored_offer.append(item["offer_id"])
                                    self.logger.info(f'接受报价[{str(item["offer_id"])}]完成!')
                                    accepted = True
                            if (uu_wait_deliver_list.index(item) != len_uu_wait_deliver_list - 1) and accepted:
                                self.logger.info("为了避免频繁访问Steam接口, 等待5秒后继续...")
                                time.sleep(5)
                except Exception as e:
                    if '登录状态失效，请重新登录' in str(e):
                        handle_caught_exception(e, "UUAutoAcceptOffer", known=True)
                        self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                        self.logger.error("由于登录失败，插件将自动退出")
                        exit_code.set(1)
                        return 1
                    else:
                        handle_caught_exception(e, "UUAutoAcceptOffer", known=False)
                        self.logger.error("出现未知错误, 稍后再试! ")
                self.logger.info("将在{0}秒后再次检查待发货订单信息!".format(str(interval)))
                time.sleep(interval)
