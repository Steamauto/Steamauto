import time


class SteamAutoAcceptOffer:

    def __init__(self, logger, steam_client, config):
        self.logger = logger
        self.steam_client = steam_client
        self.config = config

    def init(self):
        return False

    def exec(self):
        while True:
            try:
                trade_summary = self.steam_client.get_trade_offers_summary()['response']
                self.logger.info('[SteamAutoAcceptOffer] 检测到有%d个待处理的交易报价' % trade_summary['pending_received_count'])
                if trade_summary['pending_received_count'] > 0:
                    trade_offers = self.steam_client.get_trade_offers(merge=False)['response']
                    if len(trade_offers['trade_offers_received']) > 0:
                        for trade_offer in trade_offers['trade_offers_received']:
                            self.logger.debug(f'\n报价[{trade_offer["tradeofferid"]}] '
                                              f'\n支出: {len(trade_offer.get("items_to_give", {}))} 个物品'
                                              f'\n接收: {len(trade_offer.get("items_to_receive", {}))} 个物品')
                            if len(trade_offer.get("items_to_give", {})) == 0:
                                self.logger.info(f'[SteamAutoAcceptOffer] 检测到报价[{trade_offer["tradeofferid"]}]'
                                                 f'属于礼物报价，正在接受报价...')
                                self.steam_client.accept_trade_offer(trade_offer['tradeofferid'])
                                self.logger.info(f'[SteamAutoAcceptOffer] 报价[{trade_offer["tradeofferid"]}]接受成功！')
                            else:
                                self.logger.info(f'[SteamAutoAcceptOffer] 检测到报价[{trade_offer["tradeofferid"]}]'
                                                 f'需要支出物品，自动跳过处理')
            except Exception as e:
                self.logger.error(e,exc_info=True)
                self.logger.error("[SteamAutoAcceptOffer] 发生未知错误！稍后再试...")
            time.sleep(self.config['steam_auto_accept_offer']['interval'])
