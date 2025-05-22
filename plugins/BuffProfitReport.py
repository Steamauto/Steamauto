import os
import pickle
import time
import requests # Added
import apprise # Added
from _decimal import Decimal # Fine
from apprise import AppriseAsset, AppriseAttachment # Added
import json5 # Fine

from utils.plugin_base import PluginBase # Added
from utils.buff_helper import get_valid_session_for_buff # Fine
from utils.logger import handle_caught_exception # Fine
from utils.static import (BUFF_COOKIES_FILE_PATH, SESSION_FOLDER, SUPPORT_GAME_TYPES) # Fine
from utils.file_utils import get_encoding # Updated

class BuffProfitReport(PluginBase):
    # Class attribute for headers template, instance copy used in __init__
    buff_headers_template = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
    }

    def __init__(self, plugin_name: str, main_logger, config: dict, steam_client, steam_client_mutex):
        super().__init__(plugin_name, main_logger, config, steam_client, steam_client_mutex)
        
        self.plugin_specific_config = self.config.get(self.plugin_name, {})
        if not self.plugin_specific_config:
            self.logger.warning(f"在主配置中未找到插件 {self.plugin_name} 的配置。将使用默认值或可能出现问题。")

        self.buff_headers = self.buff_headers_template.copy()
        self.session = requests.session()
        self.asset = AppriseAsset()
        self.logger.info(f"插件 {self.plugin_name} 已初始化。")

    def init(self) -> bool:
        self.logger.debug(f"调用插件 {self.plugin_name} 的特定init方法。")
        if get_valid_session_for_buff(self.steam_client, self.logger) == "": # Pass self.logger
            self.logger.error("初始化期间获取有效的BUFF会话失败。")
            return True # Error
        self.logger.info("初始化期间成功获取BUFF会话。")
        return False # Success

    def get_all_buff_inventory(self, game="csgo"):
        self.logger.info(f"正在获取 {game} BUFF 库存...")
        page_num = 1
        page_size = 300 # Consider making this configurable
        sort_by = "time.desc" # Consider making this configurable
        state = "all"
        force = 0
        force_wear = 0
        url = "https://buff.163.com/api/market/steam_inventory"
        total_items = []
        
        sleep_interval = self.plugin_specific_config.get("api_request_interval_short", 15)

        while True:
            params = {
                "page_num": page_num, "page_size": page_size, "sort_by": sort_by,
                "state": state, "force": force, "force_wear": force_wear, "game": game
            }
            self.logger.info(f"避免被封号, 休眠{sleep_interval}秒")
            time.sleep(sleep_interval)
            response_json = self.session.get(url, headers=self.buff_headers, params=params).json()
            
            if response_json.get("code") == "OK":
                data = response_json.get("data", {})
                items = data.get("items", [])
                total_items.extend(items)
                if len(items) < page_size:
                    break
                page_num += 1
            else:
                self.logger.error(f"获取BUFF库存失败: {response_json}")
                break
        return total_items

    def get_sell_history(self, game: str) -> dict:
        page_size = self.plugin_specific_config.get("history_page_size", 100)
        page_num = 1
        result = {}
        local_sell_history = {}
        history_file_path = os.path.join(SESSION_FOLDER, f"sell_history_{game}_full.json")
        
        sleep_interval = self.plugin_specific_config.get("api_request_interval_short", 15)

        try:
            if os.path.exists(history_file_path):
                with open(history_file_path, "r", encoding=get_encoding(history_file_path)) as f:
                    local_sell_history = json5.load(f)
        except Exception as e:
            self.logger.error(f"读取本地历史订单失败: {e}", exc_info=True)
        
        while True:
            should_break = False
            self.logger.info(f"为了避免被封号, 休眠{sleep_interval}秒")
            time.sleep(sleep_interval)
            url = (f'https://buff.163.com/api/market/sell_order/history?page_num={page_num}'
                   f'&page_size={page_size}&game={game}')
            response_json = self.session.get(url, headers=self.buff_headers).json()

            if response_json.get("code") != "OK":
                self.logger.error(f"获取历史订单失败: {response_json}")
                break
            
            data = response_json.get("data", {})
            items = data.get("items", [])
            goods_infos = data.get("goods_infos", {})

            for item in items:
                if item.get("state") != "SUCCESS":
                    continue
                item_copy = item.copy()
                trade_id = str(item_copy.get("id")) # Ensure ID is string for dict key
                goods_id_str = str(item_copy.get("goods_id"))
                item_copy["item_details"] = goods_infos.get(goods_id_str, {}) # Attach goods_info
                result[trade_id] = item_copy
                if not should_break and trade_id in local_sell_history:
                    self.logger.info("后面没有新的订单了, 无需继续获取")
                    should_break = True
            
            if should_break or len(items) < page_size:
                break
            page_num += 1
            
        # Merge with local history
        for key, value in local_sell_history.items():
            if key not in result:
                result[key] = value
        
        if result: # Save updated full history
            try:
                with open(history_file_path, "w", encoding="utf-8") as f:
                    json5.dump(result, f, indent=4, ensure_ascii=False)
            except Exception as e:
                self.logger.error(f"保存售出历史记录失败: {e}", exc_info=True)
        return result

    def get_buy_history(self, game: str) -> dict:
        page_size = self.plugin_specific_config.get("history_page_size_large", 300) # Larger for buy history
        page_num = 1
        result = {}
        local_buy_history = {}
        history_file_path = os.path.join(SESSION_FOLDER, f"buy_history_{game}_full.json")
        
        sleep_interval = self.plugin_specific_config.get("api_request_interval_short", 15)
        max_history_age_days = self.plugin_specific_config.get("max_buy_history_age_days", 540) # 1.5 years

        try:
            if os.path.exists(history_file_path):
                with open(history_file_path, "r", encoding=get_encoding(history_file_path)) as f:
                    local_buy_history = json5.load(f)
        except Exception as e:
            self.logger.error(f"读取本地购买历史订单失败: {e}", exc_info=True)

        while True:
            self.logger.debug(f"正在获取 {game} 购买记录, 页数: {page_num}")
            url = (f"https://buff.163.com/api/market/buy_order/history?page_num={page_num}"
                   f"&page_size={page_size}&game={game}")
            response_json = self.session.get(url, headers=self.buff_headers).json()

            if response_json.get("code") != "OK":
                self.logger.error(f"获取购买历史订单失败: {response_json}")
                break

            data = response_json.get("data", {})
            items = data.get("items", [])
            goods_infos = data.get("goods_infos", {})
            should_break = False

            for item in items:
                item_copy = item.copy()
                trade_id = str(item_copy.get("id"))
                goods_id_str = str(item_copy.get("goods_id"))
                item_copy["item_details"] = goods_infos.get(goods_id_str, {})
                
                if not should_break and trade_id in local_buy_history:
                    self.logger.info("后面没有新的订单了 (基于本地历史), 无需继续获取")
                    should_break = True
                
                transact_time = item_copy.get("transact_time")
                if transact_time and (time.time() - transact_time > max_history_age_days * 24 * 60 * 60):
                    self.logger.info(f"订单 {trade_id} 早于 {max_history_age_days} 天, 停止获取更早的记录.")
                    should_break = True; break 
                
                if item_copy.get("state") == "SUCCESS":
                    result[trade_id] = item_copy
            
            if len(items) < page_size or should_break:
                break
            page_num += 1
            self.logger.info(f"避免被封号, 休眠{sleep_interval}秒")
            time.sleep(sleep_interval)

        for key, value in local_buy_history.items():
            if key not in result:
                result[key] = value
        
        if result:
            try:
                with open(history_file_path, "w", encoding="utf-8") as f:
                    json5.dump(result, f, indent=4, ensure_ascii=False)
            except Exception as e:
                 self.logger.error(f"保存购买历史记录失败: {e}", exc_info=True)
        return result

    def get_lowest_price(self, goods_id, game="csgo"):
        sleep_interval = self.plugin_specific_config.get("api_request_interval_long", 30)
        self.logger.info("获取BUFF商品最低价")
        self.logger.info(f"为了避免被封IP, 休眠{sleep_interval}秒")
        time.sleep(sleep_interval)
        
        url = (f"https://buff.163.com/api/market/goods/sell_order?goods_id={goods_id}"
               f"&page_num=1&page_size=24&allow_tradable_cooldown=1&sort_by=default&game={game}")
        response_json = self.session.get(url, headers=self.buff_headers).json()

        if response_json.get("code") == "OK":
            items = response_json.get("data", {}).get("items", [])
            if not items:
                self.logger.info("无商品在售")
                return Decimal("-1")
            return Decimal(items[0].get("price", "-1"))
        else:
            self.logger.error(f"获取BUFF商品最低价失败: {response_json}")
            return Decimal("-1")

    def check_buff_account_state(self): # Overridden for specific error message.
        response_json = self.session.get("https://buff.163.com/account/api/user/info", headers=self.buff_headers).json()
        if response_json.get("code") == "OK":
            data = response_json.get("data", {})
            nickname = data.get("nickname")
            if nickname:
                return nickname
        self.logger.error(f"BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试! Response: {response_json}")
        raise TypeError("BUFF account state invalid") # To be caught by exec

    def exec(self):
        initial_sleep = self.plugin_specific_config.get("initial_sleep", 90)
        self.logger.info(f"BUFF利润报告插件已启动, 休眠{initial_sleep}秒, 与其他插件错开运行时间")
        time.sleep(initial_sleep)
        
        send_report_time_str = self.plugin_specific_config.get("send_report_time", "20:30")
        apprise_servers = self.plugin_specific_config.get("servers", [])

        if not apprise_servers:
            self.logger.error("未配置Apprise服务器, 无法发送报告. 请检查配置文件.")
            return 1 # Error exit

        try:
            self.logger.info("正在准备登录至BUFF...")
            if not os.path.exists(BUFF_COOKIES_FILE_PATH):
                 self.logger.error(f"BUFF cookies file not found: {BUFF_COOKIES_FILE_PATH}"); return 1
            with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
                cookie_content = f.read().replace("session=", "").strip().split(";")[0]
                if not cookie_content: self.logger.error("BUFF Cookie is empty!"); return 1
                self.session.cookies["session"] = cookie_content
            
            self.logger.info("已检测到cookies, 尝试登录")
            buff_username = self.check_buff_account_state() # Raises TypeError on failure
            self.logger.info(f"已经登录至BUFF 用户名: {buff_username}")
        except TypeError: # Raised by check_buff_account_state
            # Error already logged
            return 1 # Error exit
        except Exception as e:
            handle_caught_exception(e, self.plugin_name)
            self.logger.error(f"BUFF账户登录初始化失败: {e}")
            return 1

        main_loop_interval = self.plugin_specific_config.get("main_loop_interval_seconds", 20)

        while True:
            try:
                # Steam Session Check (simplified, assumes relogin handles details)
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("Steam会话已过期, 正在重新登录...")
                        self.steam_client.relogin()
                        self.logger.info("Steam会话已更新")
                        # Consider if session pickling is still needed or handled by SteamClient
            except Exception as e:
                self.logger.error(f"Steam会话检查/更新出错: {e}", exc_info=True)
                self.logger.info(f"休眠{main_loop_interval}秒后重试")
                time.sleep(main_loop_interval)
                continue

            if time.strftime("%H:%M", time.localtime()) != send_report_time_str:
                time.sleep(main_loop_interval)
                continue
            
            # --- Report Generation Logic ---
            self.logger.info("开始生成利润报告...")
            grand_total_profit = Decimal('0.00')
            grand_total_profit_after_fee = Decimal('0.00')
            report_sections = []

            for game_details in SUPPORT_GAME_TYPES:
                game_name = game_details["game"]
                transaction_fee_rate = Decimal('0.975') if game_name == "csgo" else Decimal('0.982') # Example
                withdrawal_fee_rate = Decimal('0.99')

                self.logger.info(f"正在处理 {game_name} 数据...")
                buy_history = self.get_buy_history(game_name)
                time.sleep(self.plugin_specific_config.get("api_request_interval_long", 20))
                sell_history = self.get_sell_history(game_name)
                time.sleep(self.plugin_specific_config.get("api_request_interval_long", 20))
                current_inventory = self.get_all_buff_inventory(game=game_name)

                # 1. Profit from items currently in inventory (bought, not sold)
                inventory_profit_details = []
                inventory_total_profit = Decimal('0.00')
                inventory_total_profit_after_fee = Decimal('0.00')
                
                # Create a map of asset_id to buy_history item for quick lookup
                buy_history_map = {bh_item['asset_info']['assetid']: bh_item 
                                   for bh_id, bh_item in buy_history.items() 
                                   if 'asset_info' in bh_item and 'assetid' in bh_item['asset_info']}

                temp_inventory = list(current_inventory) # Copy for safe removal

                for inv_item in list(temp_inventory): # Iterate over copy
                    asset_id = inv_item.get("asset_info", {}).get("assetid")
                    if asset_id in buy_history_map:
                        bought_item_details = buy_history_map.pop(asset_id) # Remove from map to avoid re-processing
                        
                        buy_price = Decimal(bought_item_details['price'])
                        current_market_price = Decimal(inv_item.get('sell_min_price', '0')) # Current sell price
                        
                        profit = current_market_price - buy_price
                        profit_after_fee = (current_market_price * transaction_fee_rate * withdrawal_fee_rate) - buy_price
                        
                        inventory_profit_details.append(f"  - {inv_item.get('name', 'N/A')}: 预估利润 {profit:.2f} (手续费后 {profit_after_fee:.2f}) [买: {buy_price}, 现价: {current_market_price}]")
                        inventory_total_profit += profit
                        inventory_total_profit_after_fee += profit_after_fee
                        temp_inventory.remove(inv_item) # Remove processed item

                # Items in inventory without direct buy history (e.g. from remarks)
                for inv_item in temp_inventory:
                    remark_price_str = inv_item.get("asset_extra", {}).get("remark", "").split(" ")[0]
                    try: buy_price_from_remark = Decimal(remark_price_str)
                    except: continue # Skip if remark is not a valid price
                    
                    current_market_price = Decimal(inv_item.get('sell_min_price', '0'))
                    profit = current_market_price - buy_price_from_remark
                    profit_after_fee = (current_market_price * transaction_fee_rate * withdrawal_fee_rate) - buy_price_from_remark
                    inventory_profit_details.append(f"  - {inv_item.get('name', 'N/A')} (备注买价): 预估利润 {profit:.2f} (手续费后 {profit_after_fee:.2f}) [备注买: {buy_price_from_remark}, 现价: {current_market_price}]")
                    inventory_total_profit += profit
                    inventory_total_profit_after_fee += profit_after_fee

                if inventory_profit_details:
                    report_sections.append(f"\n--- {game_name} 库存预估利润 ---\n总计: {inventory_total_profit:.2f} (手续费后: {inventory_total_profit_after_fee:.2f})\n" + "\n".join(inventory_profit_details))
                grand_total_profit += inventory_total_profit
                grand_total_profit_after_fee += inventory_total_profit_after_fee


                # 2. Profit from items sold (had buy history)
                sold_profit_details = []
                sold_total_profit = Decimal('0.00')
                sold_total_profit_after_fee = Decimal('0.00')

                for sell_id, sold_item in sell_history.items():
                    asset_id = sold_item.get("asset_info", {}).get("assetid")
                    if asset_id in buy_history_map: # Check remaining items in buy_history_map
                        bought_item_details = buy_history_map.pop(asset_id)
                        
                        buy_price = Decimal(bought_item_details['price'])
                        sell_price = Decimal(sold_item['price'])
                        
                        profit = sell_price - buy_price
                        profit_after_fee = (sell_price * transaction_fee_rate * withdrawal_fee_rate) - buy_price
                        
                        sold_profit_details.append(f"  - {sold_item.get('item_details',{}).get('name','N/A')}: 利润 {profit:.2f} (手续费后 {profit_after_fee:.2f}) [买: {buy_price}, 卖: {sell_price}]")
                        sold_total_profit += profit
                        sold_total_profit_after_fee += profit_after_fee
                
                if sold_profit_details:
                    report_sections.append(f"\n--- {game_name} 已售利润 (有购买记录) ---\n总计: {sold_total_profit:.2f} (手续费后: {sold_total_profit_after_fee:.2f})\n" + "\n".join(sold_profit_details))
                grand_total_profit += sold_total_profit
                grand_total_profit_after_fee += sold_total_profit_after_fee

                # 3. Items bought but not in inventory and not in sell history (Missing/Transferred?)
                # These are items remaining in buy_history_map
                missing_items_details = []
                if buy_history_map: # Items bought but not accounted for
                    for asset_id, bought_item in buy_history_map.items():
                        item_name = bought_item.get('item_details',{}).get('name','N/A')
                        buy_price = Decimal(bought_item['price'])
                        missing_items_details.append(f"  - {item_name} (AssetID: {asset_id}): 买入价 {buy_price:.2f} (状态未知)")
                if missing_items_details:
                     report_sections.append(f"\n--- {game_name} 已购买但去向不明物品 ---\n" + "\n".join(missing_items_details))


            # --- Construct and Send Report ---
            summary_section = (f"=== BUFF利润总览 ===\n"
                               f"总预估利润 (所有游戏合计): {grand_total_profit:.2f} RMB\n"
                               f"总预估利润 (扣除所有手续费后): {grand_total_profit_after_fee:.2f} RMB\n")
            
            final_report_body = summary_section + "\n".join(report_sections)
            report_file_path = os.path.join(SESSION_FOLDER, "buff_profit_report.txt")
            try:
                with open(report_file_path, 'w', encoding="utf-8") as f:
                    f.write(final_report_body)
                
                apprise_obj = apprise.Apprise(asset=self.asset)
                for server_url in apprise_servers:
                    apprise_obj.add(server_url)
                
                if not apprise_obj.notify(
                    title='BUFF每日利润统计报告',
                    body='BUFF每日利润统计报告 (详情见附件)', 
                    attach=AppriseAttachment(report_file_path)
                ):
                    self.logger.error("发送Apprise通知失败。")
                else:
                    self.logger.info("利润报告已发送.")
            except Exception as e:
                handle_caught_exception(e, self.plugin_name)
                self.logger.error(f"生成或发送BUFF利润报告失败: {e}", exc_info=True)
            
            # Sleep until the next day just after send_report_time to avoid multiple reports
            # This will make it sleep for roughly 24 hours.
            self.logger.info(f"报告已处理. 下次检查时间: 明天 {send_report_time_str}")
            time.sleep(60 * 60 * 23 + 60 * 30) # Sleep for ~23.5 hours to aim for next day's report time

# Note: This is a simplified profit calculation. Real profit needs to consider:
# - Specific transaction fees at the time of sale.
# - BUFF's cut.
# - Steam market price fluctuations if items are bought/sold there too.
# - Items that are traded, not bought/sold for cash.
# - Currency conversion if applicable.
# The current logic assumes all transactions are cash on BUFF.
# The "扣除手续费" part is a rough estimation.
# For accurate accounting, a more detailed transaction ledger system would be needed.
