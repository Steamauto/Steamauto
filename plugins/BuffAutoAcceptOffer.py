import time

from BuffApi import BuffAccount
from utils.buff_helper import get_valid_session_for_buff
from utils.steam_client import accept_trade_offer # Assuming this is a standalone helper
from utils.tools import exit_code # exit_code is a class from utils.tools
from utils.plugin_base import PluginBase # Import PluginBase
from utils.logger import handle_caught_exception # Keep for specific error handling

# Global logger removed, self.logger will be used from PluginBase

class BuffAutoAcceptOffer(PluginBase):
    def __init__(self, plugin_name: str, main_logger, config: dict, steam_client, steam_client_mutex):
        super().__init__(plugin_name, main_logger, config, steam_client, steam_client_mutex)
        # self.logger is now set by PluginBase
        # self.config is now set by PluginBase (full config)
        # self.steam_client is now set by PluginBase
        # self.steam_client_mutex is now set by PluginBase

        # Plugin-specific configuration
        self.plugin_specific_config = self.config.get(self.plugin_name, {})
        if not self.plugin_specific_config:
            self.logger.warning(f"在主配置中未找到插件 {self.plugin_name} 的特定配置。将使用默认值或可能出现问题。")

        # Existing specific initializations for BuffAutoAcceptOffer:
        self.SUPPORT_GAME_TYPES = [{"game": "csgo", "app_id": 730}]
        self.order_info = {}
        self.buff_account = None # Will be initialized in exec

        self.logger.info(f"插件 {self.plugin_name} 已初始化。")

    def init(self) -> bool:
        self.logger.debug(f"调用插件 {self.plugin_name} 的特定init方法。")
        # Perform any checks here. If a check fails, return True (error).
        # Example: Check if required config keys are present
        if not self.plugin_specific_config.get("interval"):
            self.logger.error("插件配置中缺少 'interval'。")
            return True # Indicate an error
        return super().init() # Calls PluginBase.init(), which returns False

    def require_buyer_send_offer(self):
        try:
            self.logger.info('正在开启只允许买家发起报价功能...')
            result = self.buff_account.set_force_buyer_send_offer()
            if result:
                self.logger.info("已开启买家发起交易报价功能")
            else:
                self.logger.error("开启买家发起交易报价功能失败")
        except Exception as e:
            self.logger.error(f"开启买家发起交易报价功能失败: {str(e)}")

    def check_buff_account_state(self):
        try:
            username = self.buff_account.get_user_nickname()
            if username:
                # 检查是否能正常访问steam_trade接口
                trades = self.buff_account.get_steam_trade()
                if trades is None:
                    self.logger.error("BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试!")
                    return ""
                return username
        except Exception as e:
            self.logger.error(f"检查BUFF账户状态失败: {str(e)}")

        self.logger.error("BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试!")
        return ""

    def format_item_info(self, trade):
        """格式化物品信息用于显示在交易接受描述中"""
        result = "发货平台：网易BUFF\n"

        for good_id, good_item in trade["goods_infos"].items():
            result += f"发货商品：{good_item['name']}"
            if trade.get('items_to_trade'):
                if len(trade["items_to_trade"]) > 1:
                    result += f" 等{len(trade['items_to_trade'])}个物品"
            else:
                result += " (求购报价，数量未知)"

            if trade["tradeofferid"] in self.order_info:
                price = float(self.order_info[trade["tradeofferid"]]["price"])
                result += f"\n售价：{price} 元"

            break  # 只处理第一个物品，因为批量购买的物品通常是相同的

        return result

    def exec(self):
        self.logger.info(f"插件 {self.plugin_name} 开始执行，请稍候...")

        session = get_valid_session_for_buff(self.steam_client, self.logger) # Pass self.logger
        if not session:
            self.logger.error("获取有效的BUFF会话失败。插件即将退出。")
            exit_code.set(1)
            return 1
            
        self.buff_account = BuffAccount(session)

        try:
            user_info = self.buff_account.get_user_info()
            if not user_info or "steamid" not in user_info:
                self.logger.error("获取BUFF绑定的SteamID失败")
                exit_code.set(1)
                return 1

            steamid_buff = user_info["steamid"]
        except Exception as e:
            self.logger.error(f"获取BUFF绑定的SteamID失败: {str(e)}")
            exit_code.set(1)
            return 1

        if self.steam_client.get_steam64id_from_cookies() != steamid_buff:
            self.logger.error("当前登录账号与BUFF绑定的Steam账号不一致!")
            exit_code.set(1)
            return 1

        self.logger.info(f"已经登录至BUFF 用户名: {user_info['nickname']}")
        if not user_info.get('force_buyer_send_offer', False): # Use .get for safety
            self.logger.warning("当前账号未开启只允许买家发起报价功能，正在自动开启...")
            self.require_buyer_send_offer()
        else:
            self.logger.info("当前账号已开启只允许买家发起报价功能")

        ignored_offer = []
        interval = self.plugin_specific_config.get("interval", 60) # Default to 60 if not found
        dota2_support = self.plugin_specific_config.get("dota2_support", False)

        if 'sell_protection' in self.plugin_specific_config:
            self.logger.warning('你正在使用旧版本配置文件字段 "sell_protection"，BUFF自动发货插件已经重写并精简功能，建议检查并更新配置文件！')

        if dota2_support:
            self.SUPPORT_GAME_TYPES.append({"game": "dota2", "app_id": 570})

        while True:
            try:
                self.logger.info("正在检查BUFF账户登录状态...")
                username = self.check_buff_account_state()
                if not username: # Simplified check
                    self.logger.info("BUFF账户登录状态失效, 尝试重新登录...")
                    session = get_valid_session_for_buff(self.steam_client, self.logger) # Pass self.logger
                    if not session:
                        self.logger.error("BUFF账户登录状态失效, 无法自动重新登录! 插件即将退出。")
                        exit_code.set(1)
                        return 1 # Exit plugin execution
                    self.buff_account = BuffAccount(session)
                
                self.logger.info("为了避免访问接口过于频繁，休眠5秒...")
                time.sleep(5)
                
                self.logger.info("正在进行BUFF待发货/待收货饰品检查...")
                notification = self.buff_account.get_notification()

                if not notification or not isinstance(notification, dict):
                    self.logger.error("获取通知失败或格式无效。稍后重试。")
                    time.sleep(interval)
                    continue
                
                to_deliver_order = notification.get("to_deliver_order", {})
                to_confirm_sell = notification.get("to_confirm_sell", {})


                # Process response check for errors
                if isinstance(to_deliver_order, dict):
                    csgo_count = int(to_deliver_order.get("csgo", 0))
                    dota2_count = int(to_deliver_order.get("dota2", 0)) if dota2_support else 0
                    total_count = csgo_count + dota2_count

                    if total_count > 0:
                        self.logger.info(f"检测到{total_count}个待发货请求!")
                        self.logger.info(f"CSGO待发货: {csgo_count}个")
                        if dota2_support:
                            self.logger.info(f"DOTA2待发货: {dota2_count}个")
                else:
                    self.logger.error("Buff接口返回 'to_deliver_order' 数据异常! 请检查网络连接或稍后再试!")


                if any(list(to_deliver_order.values()) + list(to_confirm_sell.values())):
                    trades = self.buff_account.get_steam_trade()
                    self.logger.info("为了避免访问接口过于频繁，休眠5秒...")
                    time.sleep(5)

                    if trades is None: # Check explicitly for None
                        self.logger.error("获取Steam交易失败，稍后重试")
                        time.sleep(interval) # Use configured interval before retrying
                        continue

                    for index, game_info in enumerate(self.SUPPORT_GAME_TYPES):
                        response_data = self.buff_account.get_sell_order_to_deliver(game_info["game"], game_info["app_id"])
                        if response_data and "items" in response_data:
                            trade_supply = response_data["items"]
                            goods_infos_map = response_data.get("goods_infos", {}) # Ensure goods_infos exists
                            for trade_offer in trade_supply:
                                if trade_offer.get("tradeofferid"): # Check if tradeofferid exists and is not empty
                                    self.order_info[trade_offer["tradeofferid"]] = trade_offer
                                    # Ensure 'trades' is a list before using 'any'
                                    if isinstance(trades, list) and not any(trade_offer["tradeofferid"] == trade.get("tradeofferid") for trade in trades if isinstance(trade, dict)):
                                        goods_id_from_offer = str(trade_offer.get("goods_id"))
                                        # Find matching goods_info
                                        matched_goods_info = goods_infos_map.get(goods_id_from_offer)
                                        if matched_goods_info:
                                            trade_offer["goods_infos"] = {goods_id_from_offer: matched_goods_info}
                                        else:
                                            trade_offer["goods_infos"] = {} # Ensure key exists
                                        trades.append(trade_offer)
                        
                        if index != len(self.SUPPORT_GAME_TYPES) - 1:
                            self.logger.info("为了避免访问接口过于频繁，休眠5秒...")
                            time.sleep(5)

                    unprocessed_count = len(trades) if isinstance(trades, list) else 0
                    self.logger.info(f"查找到 {unprocessed_count} 个待处理的BUFF报价")

                    if unprocessed_count > 0:
                        for i, trade in enumerate(trades):
                            offer_id = trade.get("tradeofferid")
                            if not offer_id: # Skip if no offer_id
                                self.logger.warning(f"索引 {i} 处的交易缺少 'tradeofferid'。已跳过。")
                                continue

                            self.logger.info(f"正在处理第 {i+1} 个交易报价 报价ID：{offer_id}")
                            if offer_id not in ignored_offer:
                                try:
                                    self.logger.info("正在接受报价...")
                                    desc = self.format_item_info(trade)
                                    if accept_trade_offer(self.steam_client, self.steam_client_mutex, offer_id, desc=desc):
                                        ignored_offer.append(offer_id)
                                        self.logger.info("接受完成! 已经将此交易报价加入忽略名单!")

                                    if i != len(trades) - 1: # Check index against current length
                                        self.logger.info("为了避免频繁访问Steam接口, 等待5秒后继续...")
                                        time.sleep(5)
                                except Exception as e: # Catch specific errors if possible
                                    self.logger.error(f"处理交易报价 {offer_id} 时出错: {str(e)}", exc_info=True)
                                    self.logger.info("出现错误, 稍后再试!")
                            else:
                                self.logger.info(f"该报价 {offer_id} 已经被处理过, 跳过.")
                else:
                    self.logger.info("没有待处理的交易报价")
            except Exception as e:
                handle_caught_exception(e, self.plugin_name) # Use plugin_name for context
                self.logger.info("出现未知错误, 稍后再试!")

            self.logger.info(f"将在{interval}秒后再次检查待发货订单信息!")
            time.sleep(interval)
