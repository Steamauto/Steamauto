import time

from PyC5Game import C5Account
from utils.plugin_base import PluginBase # Added
from utils.logger import handle_caught_exception # PluginLogger import removed
from utils.steam_client import accept_trade_offer # Fine

# Module-level logger removed

class C5AutoAcceptOffer(PluginBase):
    def __init__(self, plugin_name: str, main_logger, config: dict, steam_client, steam_client_mutex):
        super().__init__(plugin_name, main_logger, config, steam_client, steam_client_mutex)
        # self.logger, self.config, self.steam_client, self.steam_client_mutex are set by PluginBase

        self.plugin_specific_config = self.config.get(self.plugin_name, {})
        if not self.plugin_specific_config:
            self.logger.warning(f"在主配置中未找到插件 {self.plugin_name} 的配置。将使用默认值或可能出现问题。")
        
        self.client = None # Will be initialized in exec
        self.interval = self.plugin_specific_config.get('interval', 60) # Default to 60s if not set

        self.logger.info(f"插件 {self.plugin_name} 已初始化。")

    def init(self) -> bool:
        self.logger.debug(f"调用插件 {self.plugin_name} 的特定init方法。")
        # Add any specific init checks here if needed, e.g., for app_key
        if not self.plugin_specific_config.get('app_key'):
            self.logger.error("插件配置中缺少 'app_key'。C5相关功能将无法工作。")
            return True # Indicate an error
        return super().init() # Calls PluginBase.init(), which returns False

    def exec(self):
        self.logger.info(f"插件 {self.plugin_name} 开始执行。")
        ignored_list = []
        
        app_key = self.plugin_specific_config.get('app_key')
        if not app_key: # Should have been caught by init, but double check
            self.logger.error("app_key 未配置。插件无法运行。")
            return 1 # Error exit

        self.client = C5Account(app_key)
        # CheckAppKey is a property that might make a request.
        # Consider moving to an init method or handling potential exceptions.
        try:
            if self.client.checkAppKey: # This is a property, not a method to call
                self.logger.info("C5账户 app_key 校验成功。")
            else:
                self.logger.error("C5账户 app_key 校验失败！请检查配置文件中的 app_key。")
                return 1 # Error exit
        except Exception as e:
            self.logger.error(f"C5Account app_key 校验时出错: {e}", exc_info=True)
            return 1


        while True:
            try:
                current_steam_id = None
                with self.steam_client_mutex:
                    if self.steam_client and hasattr(self.steam_client, 'get_steam64id_from_cookies'):
                        current_steam_id = self.steam_client.get_steam64id_from_cookies()
                    else:
                        self.logger.error("Steam客户端不可用或未登录，无法获取SteamID。")
                        time.sleep(self.interval); continue
                
                if not current_steam_id:
                    self.logger.error("无法获取SteamID。跳过本次迭代。")
                    time.sleep(self.interval); continue

                self.logger.info('正在检索是否有待发货订单...')
                notDeliveredOrders = []
                page = 0
                while True:
                    page += 1
                    # Pass steamId to orderList
                    resp = self.client.orderList(status=1, page=page, steamId=current_steam_id) 
                    if resp.get('errorCode','') == 400001: # Specific error for invalid app_key
                        self.logger.error('app_key错误，请检查配置文件内的app_key是否正确. 插件已停止运行')
                        return 1 # Critical error, stop plugin
                    
                    data = resp.get('data', {})
                    current_page_orders = data.get('list', [])
                    notDeliveredOrders.extend(current_page_orders) # Use extend for list
                    
                    if len(current_page_orders) < data.get('limit', 20): # Default limit might be 20
                        break
                
                self.logger.info(f"共检索到{len(notDeliveredOrders)}个待发货订单")
                if notDeliveredOrders:
                    notDeliveredOrderIds = [order['orderId'] for order in notDeliveredOrders if 'orderId' in order]
                    if notDeliveredOrderIds:
                        self.logger.info(f'正在请求C5服务器发送报价...')
                        # Assuming self.client.deliver can handle a list of order IDs
                        deliver_response = self.client.deliver(notDeliveredOrderIds)
                        self.logger.debug(f"C5发货请求响应: {deliver_response}")
                        self.logger.info('已请求C5服务器发送报价，60秒后获取报价ID')
                        time.sleep(60) # Consider making this configurable
                    else:
                        self.logger.info("没有有效的订单ID可用于发货.")

                deliveringOrders = []
                page = 0
                while True:
                    page += 1
                    resp = self.client.orderList(status=2, page=page, steamId=current_steam_id)
                    data = resp.get('data', {})
                    current_page_delivering_orders = data.get('list', [])
                    deliveringOrders.extend(current_page_delivering_orders)
                    if len(current_page_delivering_orders) < data.get('limit', 20):
                        break
                
                self.logger.info(f"共检索到{len(deliveringOrders)}个正在发货订单")
                for index, deliveringOrder in enumerate(deliveringOrders):
                    order_name = deliveringOrder.get('name', '未知订单')
                    self.logger.info(f'正在处理订单 {order_name} ...')
                    
                    offerId = deliveringOrder.get('orderConfirmInfoDTO', {}).get('offerId')
                    if not offerId:
                        self.logger.warning(f"订单 {order_name} 没有报价ID (offerId). 跳过.")
                        continue

                    if offerId in ignored_list:
                        self.logger.info(f'订单 {order_name} (OfferID: {offerId}) 已处理，跳过')
                        continue
                    
                    # Ensure steam_client is available for accept_trade_offer
                    if not self.steam_client:
                        self.logger.error("Steam客户端不可用，无法接受交易报价。")
                        break # Break from this inner loop, will retry later

                    if accept_trade_offer(self.steam_client, self.steam_client_mutex, offerId, desc=f"发货平台：C5Game\n发货商品：{order_name}"):
                        self.logger.info(f'订单 {order_name} (OfferID: {offerId}) 发货完成')
                        ignored_list.append(offerId)
                        if index != len(deliveringOrders) - 1:
                            delay = self.plugin_specific_config.get('steam_request_delay', 3)
                            self.logger.info(f"为避免频繁访问Steam接口，等待{delay}秒后处理下一个订单")
                            time.sleep(delay)
                    else:
                        self.logger.error(f'订单 {order_name} (OfferID: {offerId}) 发货失败，请检查网络或者Steam账号！')
            
            except Exception as e:
                handle_caught_exception(e, self.plugin_name) # Use self.plugin_name
            
            self.logger.info(f"等待{self.interval}秒后重新检索是否有待发货订单")
            time.sleep(self.interval)
