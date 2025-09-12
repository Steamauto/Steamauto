import time

from PyC5Game import C5Account
from utils.logger import PluginLogger, handle_caught_exception
from utils.steam_client import accept_trade_offer, external_handler

logger = PluginLogger("C5AutoAcceptOffer")

class C5AutoAcceptOffer:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        with steam_client_mutex:
            self.steam_id = steam_client.get_steam64id_from_cookies()

    def init(self) -> bool:
        return False

    def exec(self):
        ignored_list = []
        try:
            self.interval = self.config.get('c5_auto_accept_offer').get('interval')
        except Exception as e:
            logger.error("读取配置文件出错！请检查配置文件内的interval是否正确")
            return True

        app_key = self.config.get('c5_auto_accept_offer').get('app_key')
        self.client = C5Account(app_key)
        if self.client.checkAppKey:
            logger.info("C5账号登录成功")
        else:
            logger.error("C5账号登录失败！请检查配置文件内的app_key是否正确")
            return True
        
        while True:
            try:
                logger.info('正在检索是否有待发货订单...')
                notDeliveredOrders = []
                page = 0
                while True:
                    page += 1
                    resp = self.client.orderList(status=1, page=page,steamId=self.steam_id)
                    if resp.get('errorCode','') == 400001:
                        logger.error('app_key错误，请检查配置文件内的app_key是否正确')
                        logger.error('由于app_key错误，插件已停止运行')
                        return 1
                    notDeliveredOrders = resp.get('data').get('list', [])
                    if len(resp.get('data').get('list', [])) < resp['data']['limit']:
                        break
                logger.info(f"共检索到{len(notDeliveredOrders)}个待发货订单")
                if notDeliveredOrders:
                    toSendOrderIds = []
                    for order in notDeliveredOrders:
                        if external_handler('C5-' + str(order['orderId']), desc=f"发货平台：C5Game\n发货商品：{order['name']}\n订单价格：{order['price']}元"):
                            toSendOrderIds.append(order['orderId'])
                    if len(toSendOrderIds) > 0:
                        logger.info(f'正在发送 {len(toSendOrderIds)} 个报价...')
                        self.client.deliver(toSendOrderIds)
                        logger.info('已请求C5服务器发送报价，30秒后获取报价ID')
                        time.sleep(30)
                deliveringOrders = []
                page = 0
                while True:
                    page += 1
                    resp = self.client.orderList(status=2, page=page,steamId=self.steam_id)
                    deliveringOrders = resp.get('data').get('list', [])
                    if len(resp.get('data').get('list', [])) < resp['data']['limit']:
                        break
                logger.info(f"共检索到{len(deliveringOrders)}个正在发货订单")
                for deliveringOrder in deliveringOrders:
                    logger.info(f'正在处理订单 {deliveringOrder["name"]} ...')
                    offerId = deliveringOrder['orderConfirmInfoDTO']['offerId']
                    if offerId in ignored_list:
                        logger.info(f'订单 {deliveringOrder["name"]} 已发货，跳过')
                        continue
                    if accept_trade_offer(self.steam_client, self.steam_client_mutex, offerId, desc=f"发货平台：C5Game\n发货商品：{deliveringOrder['name']}", reportToExternal=False):
                        logger.info(f'订单 {deliveringOrder["name"]} 发货完成')
                        ignored_list.append(offerId)
                        if deliveringOrders.index(deliveringOrder) != len(deliveringOrders) - 1:
                            logger.info("为避免频繁访问Steam接口，等待3秒后处理下一个订单")
                            time.sleep(3)
                    else:
                        logger.error(f'订单 {deliveringOrder["name"]} 发货失败，请检查网络或者Steam账号！')
            except Exception as e:
                handle_caught_exception(e, prefix="C5AutoAcceptOffer")
            logger.info(f"等待{self.interval}秒后重新检索是否有待发货订单")
            time.sleep(self.interval)
