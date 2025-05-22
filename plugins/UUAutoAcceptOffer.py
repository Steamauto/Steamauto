import time
import uuyoupinapi # This is fine

from utils.plugin_base import PluginBase # Add this
from utils.logger import handle_caught_exception # PluginLogger import removed
from utils.notifier import send_notification # This is fine
from utils.steam_client import accept_trade_offer # This is fine
from utils.tools import exit_code # This is fine
from utils.uu_helper import get_valid_token_for_uu # This is fine

# Module-level logger removed

class UUAutoAcceptOffer(PluginBase):
    def __init__(self, plugin_name: str, main_logger, config: dict, steam_client, steam_client_mutex):
        super().__init__(plugin_name, main_logger, config, steam_client, steam_client_mutex)
        # self.logger, self.config, self.steam_client, self.steam_client_mutex are set by PluginBase

        self.plugin_specific_config = self.config.get(self.plugin_name, {})
        if not self.plugin_specific_config:
            self.logger.warning(f"在主配置中未找到插件 {self.plugin_name} 的配置。将使用默认值或可能出现问题。")
        
        # No UU-specific client instance here, it's created in exec/init
        self.logger.info(f"插件 {self.plugin_name} 已初始化。")

    def init(self) -> bool:
        self.logger.debug(f"调用插件 {self.plugin_name} 的特定init方法。")
        # Pass self.logger to helper if it accepts it. Assuming get_valid_token_for_uu now does.
        token = get_valid_token_for_uu(self.logger) 
        if not token:
            self.logger.error("悠悠有品登录在初始化期间失败！插件将无法运行。")
            # Plugins should not set global exit_code in init. 
            # PluginManager's check_plugins_initialization handles this.
            return True # True indicates an error
        self.logger.info("初始化期间成功获取悠悠有品令牌。")
        return False # False indicates success

    def exec(self):
        self.logger.info(f"插件 {self.plugin_name} 开始执行。")
        uuyoupin_client = None # Changed variable name to avoid conflict with module name
        
        # Token is fetched again in exec, could potentially reuse from init if stored.
        # For now, following original logic of fetching fresh token for exec.
        token = get_valid_token_for_uu(self.logger) # Pass self.logger
        if not token:
            self.logger.error("由于悠悠有品登录失败，插件将退出。")
            exit_code.set(1) # Runtime failure, plugin cannot continue
            return 1 
        else:
            uuyoupin_client = uuyoupinapi.UUAccount(token)
        
        ignored_offer_ids = [] # Renamed for clarity
        # Fetch interval from plugin-specific config, with a default value
        interval = self.plugin_specific_config.get("interval", 60) # Default to 60 seconds

        if uuyoupin_client is not None: # Ensure client was initialized
            while True:
                try:
                    # Assuming send_device_info is still relevant.
                    # If it can fail and needs specific error handling, add try-except.
                    uuyoupin_client.send_device_info() 
                    
                    self.logger.info("正在检查悠悠有品待发货信息...")
                    uu_wait_deliver_list = uuyoupin_client.get_wait_deliver_list()
                    
                    if uu_wait_deliver_list is None: # API might return None on error
                        self.logger.error("获取悠悠有品待发货列表失败 (API返回None)。稍后重试。")
                        time.sleep(interval)
                        continue

                    len_uu_wait_deliver_list = len(uu_wait_deliver_list)
                    self.logger.info(f"{len_uu_wait_deliver_list}个悠悠有品待发货订单")

                    if len_uu_wait_deliver_list != 0:
                        for index, item in enumerate(uu_wait_deliver_list):
                            accepted_this_iteration = False # Flag to control delay
                            item_name = item.get('item_name', '未知物品') # Safer access
                            offer_id = item.get('offer_id')

                            self.logger.info(f"正在接受悠悠有品待发货报价, 商品名: {item_name}, 报价ID: {offer_id}")
                            
                            if offer_id is None:
                                self.logger.warning("此订单为需要手动发货(或异常)的订单, 不能自动处理, 跳过此订单!")
                            elif str(offer_id) in ignored_offer_ids: # Ensure offer_id is string for comparison
                                self.logger.info(f"此交易报价 {offer_id} 已经被Steamauto处理过, 出现此提示的原因是悠悠系统延迟或者该订单为批量购买订单.这不是一个报错!")
                            else:
                                if accept_trade_offer(self.steam_client, self.steam_client_mutex, str(offer_id), desc=f"发货平台：悠悠有品\n发货商品：{item_name}"):
                                    ignored_offer_ids.append(str(offer_id))
                                    self.logger.info(f'接受报价[{str(offer_id)}]完成!')
                                    accepted_this_iteration = True
                                else:
                                    self.logger.error(f"接受报价 {offer_id} 失败.")
                            
                            if (index != len_uu_wait_deliver_list - 1) and accepted_this_iteration:
                                # Use a configurable delay if available, else default
                                steam_request_delay = self.plugin_specific_config.get('steam_request_delay', 5)
                                self.logger.info(f"为了避免频繁访问Steam接口, 等待{steam_request_delay}秒后继续...")
                                time.sleep(steam_request_delay)
                
                except Exception as e:
                    # Use self.plugin_name for context
                    if '登录状态失效，请重新登录' in str(e): # Specific error check
                        handle_caught_exception(e, self.plugin_name, known=True)
                        send_notification('检测到悠悠有品登录已经失效,请重新登录', title='悠悠有品登录失效')
                        self.logger.error("检测到悠悠有品登录已经失效,请重新登录. 插件将自动退出.")
                        exit_code.set(1) # Runtime failure
                        return 1 
                    else:
                        handle_caught_exception(e, self.plugin_name, known=False)
                        self.logger.error("出现未知错误, 稍后再试!")
                
                self.logger.info(f"将在{interval}秒后再次检查待发货订单信息!")
                time.sleep(interval)
        else:
            self.logger.error("悠悠有品客户端未初始化。插件无法执行。")
            exit_code.set(1)
            return 1
