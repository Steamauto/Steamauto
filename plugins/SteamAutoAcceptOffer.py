import os
import pickle
import time

from utils.plugin_base import PluginBase # Added
from utils.logger import handle_caught_exception # PluginLogger removed
from utils.static import SESSION_FOLDER # Fine

# Module-level logger removed

class SteamAutoAcceptOffer(PluginBase):
    def __init__(self, plugin_name: str, main_logger, config: dict, steam_client, steam_client_mutex):
        super().__init__(plugin_name, main_logger, config, steam_client, steam_client_mutex)
        # self.logger, self.config, self.steam_client, self.steam_client_mutex are set by PluginBase

        self.plugin_specific_config = self.config.get(self.plugin_name, {})
        if not self.plugin_specific_config:
            self.logger.warning(f"在主配置中未找到插件 {self.plugin_name} 的配置。将使用默认值或可能出现问题。")
        
        # No plugin-specific variables to initialize for this plugin beyond what PluginBase handles
        # and what's fetched from config in exec.
        self.logger.info(f"插件 {self.plugin_name} 已初始化。")

    def init(self) -> bool:
        self.logger.debug(f"调用插件 {self.plugin_name} 的特定init方法。")
        # Example: Check for required config keys if any
        if self.plugin_specific_config.get("interval") is None:
             self.logger.warning("插件配置中缺少 'interval'。如果在执行时未找到，将使用默认值60秒。")
        # No specific initialization that can fail here, so return False (success)
        return super().init() # Calls PluginBase.init(), which returns False

    def exec(self):
        self.logger.info(f"插件 {self.plugin_name} 开始执行。")
        
        # Fetch interval from plugin-specific config, with a default value
        interval = self.plugin_specific_config.get("interval", 60) # Default to 60 seconds

        while True:
            try:
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("Steam会话已过期, 正在重新登录...")
                        self.steam_client.relogin() # Assumes relogin handles session saving if needed
                        self.logger.info("Steam会话已更新")
                        # Pickling session here might be redundant if relogin handles it,
                        # but kept for consistency with original plugin if it's a specific behavior.
                        # Ensure self.steam_client.username is valid.
                        if self.steam_client.username:
                            steam_session_path = os.path.join(SESSION_FOLDER, self.steam_client.username.lower() + ".pkl")
                            try:
                                with open(steam_session_path, "wb") as f:
                                    pickle.dump(self.steam_client._session, f) # Access underlying session if needed
                                self.logger.debug(f"Steam会话已保存至 {steam_session_path}")
                            except Exception as e_pickle:
                                self.logger.error(f"保存Steam会话失败: {e_pickle}")
                        else:
                            self.logger.warning("Steam用户名不可用，无法保存会话pickle文件。")

                self.logger.info('正在检查待处理的交易报价...')
                trade_summary_response = None
                with self.steam_client_mutex:
                    # Get non-merged offers to see raw structure
                    trade_summary_response = self.steam_client.get_trade_offers(merge=False) 
                
                if not trade_summary_response or "response" not in trade_summary_response:
                    self.logger.error("获取交易报价失败或响应格式不正确。跳过本次迭代。")
                    time.sleep(interval); continue

                trade_summary = trade_summary_response["response"]
                received_offers = trade_summary.get('trade_offers_received', [])
                
                self.logger.info(f"检测到有{len(received_offers)}个待处理的交易报价")

                if len(received_offers) > 0:
                    # No need to call get_trade_offers again, use the already fetched 'received_offers'
                    for trade_offer in received_offers:
                        offer_id = trade_offer.get("tradeofferid")
                        if not offer_id:
                            self.logger.warning("发现一个没有tradeofferid的报价, 跳过.")
                            continue

                        items_to_give = trade_offer.get("items_to_give", []) # Default to empty list
                        items_to_receive = trade_offer.get("items_to_receive", []) # Default to empty list

                        self.logger.debug(
                            f'\n报价[{offer_id}] '
                            f'\n支出: {len(items_to_give)} 个物品'
                            f'\n接收: {len(items_to_receive)} 个物品'
                        )
                        if not items_to_give: # If items_to_give is empty or None
                            self.logger.info(
                                f'检测到报价[{offer_id}] 属于礼物报价 (无需支出物品)，正在接受报价...'
                            )
                            try:
                                with self.steam_client_mutex:
                                    # Ensure accept_trade_offer can handle potential None from get_trade_offer
                                    self.steam_client.accept_trade_offer(offer_id) 
                                self.logger.info(f'报价[{offer_id}]接受成功！')
                            except Exception as e_accept:
                                # Use self.plugin_name for context
                                handle_caught_exception(e_accept, self.plugin_name, known=True) 
                                self.logger.error(f"Steam异常! 接受报价 {offer_id} 失败. 稍后再试...")
                        else:
                            self.logger.info(
                                f'检测到报价[{offer_id}] 需要支出物品，自动跳过处理'
                            )
            except Exception as e_main:
                # Use self.plugin_name for context
                handle_caught_exception(e_main, self.plugin_name)
                self.logger.error("发生未知错误！稍后再试...")
            
            self.logger.info(f"等待{interval}秒后再次检查...")
            time.sleep(interval)
