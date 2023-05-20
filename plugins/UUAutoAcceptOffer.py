import sys
import time

import uuyoupinapi

from utils.static import *
from utils.tools import *


class UUAutoAcceptOffer:

    def __init__(self, logger, steam_client, config):
        self.logger = logger
        self.steam_client = steam_client
        self.config = config

    def init(self) -> bool:
        if not os.path.exists(UU_TOKEN_FILE_PATH):
            with open(UU_TOKEN_FILE_PATH, 'w', encoding='utf-8') as f:
                f.write('')
            return True
        return False

    def exec(self):
        uuyoupin = None
        with open(UU_TOKEN_FILE_PATH, 'r', encoding=get_encoding(UU_TOKEN_FILE_PATH)) as f:
            try:
                uuyoupin = uuyoupinapi.UUAccount(f.read())
                self.logger.info('[UUAutoAcceptOffer] 悠悠有品登录完成, 用户名: ' + uuyoupin.get_user_nickname())
            except KeyError as e:
                self.logger.error('[UUAutoAcceptOffer] 悠悠有品登录失败! 请检查token是否正确! ')
                self.logger.error('[UUAutoAcceptOffer] 由于登录失败，插件将自动退出')
                sys.exit(1)
        ignored_offer = []
        interval = self.config['uu_auto_accept_offer']['interval']
        if uuyoupin is not None:
            while True:
                try:
                    self.logger.info('[UUAutoAcceptOffer] 正在检查悠悠有品待发货信息...')
                    uu_wait_deliver_list = uuyoupin.get_wait_deliver_list()
                    len_uu_wait_deliver_list = len(uu_wait_deliver_list)
                    self.logger.info('[UUAutoAcceptOffer] ' + str(len_uu_wait_deliver_list) + '个悠悠有品待发货订单')
                    if len(uu_wait_deliver_list) != 0:
                        for item in uu_wait_deliver_list:
                            self.logger.info(
                                f"[UUAutoAcceptOffer] 正在接受悠悠有品待发货报价, 商品名: {item['item_name']}, "
                                f"报价ID: {item['offer_id']}")
                            if item['offer_id'] not in ignored_offer:
                                self.steam_client.accept_trade_offer(str(item['offer_id']))
                                ignored_offer.append(item['offer_id'])
                            self.logger.info('[UUAutoAcceptOffer] 接受完成! 已经将此交易报价加入忽略名单! ')
                            if uu_wait_deliver_list.index(item) != len_uu_wait_deliver_list - 1:
                                self.logger.info('[UUAutoAcceptOffer] 为了避免频繁访问Steam接口, 等待5秒后继续...')
                                time.sleep(5)
                except Exception as e:
                    self.logger.error(e, exc_info=True)
                    self.logger.info('[UUAutoAcceptOffer] 出现未知错误, 稍后再试! ')
                self.logger.info('[UUAutoAcceptOffer] 将在{0}秒后再次检查待发货订单信息!'.format(str(interval)))
                time.sleep(interval)
