import datetime
import random
import time
import schedule
import uuyoupinapi

from utils.plugin_base import PluginBase # Added
from utils.logger import handle_caught_exception # PluginLogger removed, global logger import removed
from utils.notifier import send_notification
from utils.tools import exit_code
from utils.uu_helper import get_valid_token_for_uu

# Module-level sale_price_cache removed

class UUAutoSellItem(PluginBase):
    def __init__(self, plugin_name: str, main_logger, config: dict, steam_client, steam_client_mutex):
        super().__init__(plugin_name, main_logger, config, steam_client, steam_client_mutex)
        
        self.plugin_specific_config = self.config.get(self.plugin_name, {})
        if not self.plugin_specific_config:
            self.logger.warning(f"在主配置中未找到插件 {self.plugin_name} 的配置。将使用默认值或可能出现问题。")

        self.timeSleep = self.plugin_specific_config.get("time_sleep_on_operate", 10.0) # Default from original operate_sleep
        self.inventory_list = []
        self.buy_price_cache = {}
        self.sale_price_cache = {} # Moved from module level to instance level
        self.sale_inventory_list = None 
        
        self.uuyoupin = None # Will be initialized in exec
        self.logger.info(f"插件 {self.plugin_name} 已初始化。")

    def init(self) -> bool:
        self.logger.debug(f"调用插件 {self.plugin_name} 的特定init方法。")
        # Token check is done at the beginning of exec in original, keeping that pattern.
        # If token is essential for *any* operation and should be checked early, it could be done here.
        # For now, returning False (success) as per original logic of init doing nothing critical.
        return False # Success

    def get_uu_sale_inventory(self):
        if not self.uuyoupin:
            self.logger.error("在get_uu_sale_inventory中，悠悠有品客户端尚未初始化。")
            return []
        try:
            sale_inventory_list = self.uuyoupin.get_sell_list()
            self.logger.info(f"已上架物品数量 {len(sale_inventory_list)}")
            self.sale_inventory_list = sale_inventory_list # Store it
            return sale_inventory_list
        except Exception as e:
            self.logger.error(f"获取UU上架物品失败! 错误: {e}", exc_info=True)
            return []

    def get_market_sale_price(self, item_id, cnt=10, good_name=None): # good_name seems unused
        if not self.uuyoupin:
            self.logger.error("在get_market_sale_price中，悠悠有品客户端尚未初始化。")
            return 0.0

        cached_data = self.sale_price_cache.get(item_id) # Use self.sale_price_cache
        if cached_data and (datetime.datetime.now() - cached_data["cache_time"] <= datetime.timedelta(minutes=5)):
            self.logger.info(f"{cached_data['commodity_name']} 使用缓存结果，出售价格： {cached_data['sale_price']:.2f}")
            return cached_data["sale_price"]

        sale_price_rsp = self.uuyoupin.get_market_sale_list_with_abrade(item_id).json()
        sale_price = 0.0
        commodity_name = ""

        if sale_price_rsp.get("Code") == 0:
            rsp_list = sale_price_rsp.get("Data", [])
            if not rsp_list:
                self.logger.warning(f"市场上没有指定筛选条件的物品 (item_id: {item_id})")
                return sale_price # 0.0
            
            commodity_name = rsp_list[0].get("commodityName", "")
            sale_price_list = [float(item["price"]) for item in rsp_list if item.get("price")]
            sale_price_list = sale_price_list[:min(cnt, len(sale_price_list))] # Take up to 'cnt' prices

            if len(sale_price_list) == 1:
                sale_price = sale_price_list[0]
            elif len(sale_price_list) > 1:
                sale_price_list.sort()
                min_price_in_list = sale_price_list[0]
                # Logic for price selection based on sorted list
                if sale_price_list[1] < min_price_in_list * 1.05:
                    sale_price = min_price_in_list
                else:
                    sale_price = sale_price_list[1]
            elif sale_price_list: # Only one price was valid
                 sale_price = sale_price_list[0]


            self.logger.info(f"物品名称：{commodity_name}，计算出售价格：{sale_price:.2f}, 参考价格列表：{sale_price_list}")
        else:
            self.logger.error(f"查询出售价格失败 (item_id: {item_id})，返回结果：{sale_price_rsp.get('Code')}，内容：{sale_price_rsp}")

        sale_price = round(sale_price, 2)
        if sale_price != 0: # Cache only if a valid price was found
            self.sale_price_cache[item_id] = { # Use self.sale_price_cache
                "commodity_name": commodity_name,
                "sale_price": sale_price,
                "cache_time": datetime.datetime.now(),
            }
        return sale_price

    def sell_item(self, items_to_sell_api_format): # items should be in API expected format
        if not self.uuyoupin:
            self.logger.error("在sell_item中，悠悠有品客户端尚未初始化。")
            return -1
        if not items_to_sell_api_format:
            self.logger.info("没有物品可以出售")
            return 0

        try:
            # API expects: {"GameId": "730", "itemInfos": item_infos_list}
            rsp = self.uuyoupin.call_api(
                "POST", "/api/commodity/Inventory/SellInventoryWithLeaseV2",
                data={"GameId": "730", "itemInfos": items_to_sell_api_format} 
            ).json()

            if rsp.get("Code") == 0:
                success_count = len(items_to_sell_api_format) # Assuming API processes all or none in this call
                self.logger.info(f"成功上架 {success_count} 个物品")
                return success_count
            else:
                self.logger.error(f"上架失败，返回结果：{rsp.get('Code')}，内容：{rsp}")
                return -1
        except Exception as e:
            self.logger.error(f"调用 SellInventoryWithLeaseV2 上架失败: {e}", exc_info=True)
            return -1

    def change_sale_price(self, items_to_change_api_format): # items should be in API expected format
        if not self.uuyoupin:
            self.logger.error("在change_sale_price中，悠悠有品客户端尚未初始化。")
            return -1
        if not items_to_change_api_format:
            self.logger.info("没有物品可以修改价格")
            return 0
        try:
            # API expects: {"Commoditys": item_infos_list}
            rsp = self.uuyoupin.call_api(
                "PUT", "/api/commodity/Commodity/PriceChangeWithLeaseV2",
                data={"Commoditys": items_to_change_api_format}
            ).json()

            if rsp.get("Code") == 0:
                success_count = 0; fail_count = 0
                data_section = rsp.get('Data', {})
                
                # Original code had complex logic for success/fail count. Simplify based on typical API responses.
                # If 'Data' itself indicates overall success/failure or contains a list of results:
                if isinstance(data_section, dict) and 'Commoditys' in data_section: # If results are per commodity
                    for comm_result in data_section['Commoditys']:
                        if comm_result.get('IsSuccess') == 1: success_count +=1
                        else: 
                            fail_count += 1
                            self.logger.error(f"修改商品 {comm_result.get('CommodityId','未知ID')} 价格失败: {comm_result.get('Message','未知错误')}")
                elif isinstance(data_section, dict) and 'SuccessCount' in data_section : # If API gives summary counts
                    success_count = data_section.get('SuccessCount',0)
                    fail_count = data_section.get('FailCount',0)
                elif isinstance(data_section,list): # If data is a list of results (less common for PUT summary)
                     success_count = len([item for item in data_section if item.get('IsSuccess') == 1])
                     fail_count = len(data_section) - success_count
                else: # Fallback if structure is unexpected, assume all attempted were successful if Code is 0
                    success_count = len(items_to_change_api_format)

                self.logger.info(f"尝试修改 {len(items_to_change_api_format)} 个物品价格，成功 {success_count} 个，失败 {fail_count} 个")
                return success_count
            else:
                self.logger.error(f"修改出售价格失败，返回结果：{rsp.get('Code')}，内容：{rsp}")
                return -1
        except Exception as e:
            self.logger.error(f"调用 PriceChangeWithLeaseV2 修改价格失败: {e}", exc_info=True)
            return -1

    def auto_sell(self):
        self.logger.info("悠悠有品出售自动上架任务开始.")
        self.operate_sleep()

        if not self.uuyoupin:
            self.logger.error("在auto_sell中，悠悠有品客户端尚未初始化。无法继续。")
            return 1 # Error

        try:
            items_to_put_on_sale_api_format = [] # List of dicts for API
            self.uuyoupin.send_device_info()
            self.logger.info("正在获取悠悠有品库存...")
            
            self.inventory_list = self.uuyoupin.get_inventory(refresh=True)
            if self.inventory_list is None: self.logger.error("获取库存失败."); return 1

            filter_names = self.plugin_specific_config.get("name", []) # Names to process
            blacklist_words = self.plugin_specific_config.get('blacklist_words', [])
            take_profit_enabled = self.plugin_specific_config.get('take_profile', False)
            take_profit_ratio = self.plugin_specific_config.get('take_profile_ratio', 1.15) # Default 15% profit
            use_price_adj = self.plugin_specific_config.get('use_price_adjustment', True)
            price_adj_threshold = self.plugin_specific_config.get('price_adjustment_threshold', 1.0)
            max_on_sale_price = self.plugin_specific_config.get('max_on_sale_price', 0)


            for item_data in self.inventory_list:
                if item_data.get("AssetInfo") is None: continue

                asset_id_str = str(item_data.get("SteamAssetId"))
                template_info = item_data.get("TemplateInfo", {})
                item_template_id = str(template_info.get("Id")) # UU uses "Id" for templateId
                short_name = template_info.get("CommodityName", "") # CommodityName seems more appropriate
                
                buy_price_str = item_data.get('AssetBuyPrice', '0').replace('购￥', '')
                try: buy_price = float(buy_price_str)
                except ValueError: buy_price = 0.0
                
                self.buy_price_cache[item_template_id] = buy_price # Cache buy price by template_id

                if not item_data.get("Tradable", False) or item_data.get("AssetStatus") != 0: continue
                if not any((s and s in short_name) for s in filter_names if s): continue # Skip if not in target names
                if any(bw and bw in short_name for bw in blacklist_words if bw): # Skip if blacklisted
                    self.logger.info(f"物品 {short_name} 命中黑名单，将不会上架")
                    continue

                try:
                    market_sale_price = self.get_market_sale_price(item_template_id) # Pass template_id
                except Exception as e_price:
                    handle_caught_exception(e_price, self.plugin_name)
                    self.logger.error(f'获取 {short_name} 的市场价格失败: {e_price}，暂时跳过')
                    continue

                final_sale_price = market_sale_price
                if take_profit_enabled and buy_price > 0:
                    target_profit_price = buy_price * take_profit_ratio
                    final_sale_price = max(market_sale_price, target_profit_price)
                    self.logger.info(f"止盈计算: 购入价 {buy_price:.2f}, 市场价 {market_sale_price:.2f}, 止盈目标价 {target_profit_price:.2f} -> 最终参考价 {final_sale_price:.2f}")
                
                if final_sale_price == 0: continue # Cannot sell for 0

                if use_price_adj and final_sale_price > price_adj_threshold:
                    final_sale_price = max(price_adj_threshold, final_sale_price - 0.01)
                
                final_sale_price = round(final_sale_price, 2)

                if max_on_sale_price > 0 and final_sale_price > max_on_sale_price:
                    self.logger.info(f"物品 {short_name} 的计算价格 {final_sale_price:.2f} 超过了设定的最高上架价格 {max_on_sale_price:.2f}，跳过")
                    continue
                
                self.logger.info(f'即将上架：{short_name} 计算价格：{final_sale_price:.2f}')
                items_to_put_on_sale_api_format.append({
                    "AssetId": asset_id_str, "IsCanLease": False, "IsCanSold": True,
                    "Price": final_sale_price, "Remark": ""
                })

            self.logger.info(f"筛选后，准备上架 {len(items_to_put_on_sale_api_format)} 件物品...")
            self.operate_sleep()
            if items_to_put_on_sale_api_format:
                self.sell_item(items_to_put_on_sale_api_format)
            self.logger.info("上架任务完成")
            return 0
        except Exception as e:
            self.logger.error(f"悠悠有品出售自动上架出现错误: {e}", exc_info=True)
            if "登录" in str(e) or "token" in str(e).lower():
                 send_notification('检测到悠悠有品登录已经失效,请重新登录', title='悠悠有品登录失效')
                 self.logger.error("检测到悠悠有品登录已经失效. 插件下次运行时将尝试重新登录.")
            handle_caught_exception(e, self.plugin_name)
            return 1


    def auto_change_price(self):
        self.logger.info("悠悠有品出售自动修改价格任务开始.")
        self.operate_sleep()

        if not self.uuyoupin:
            self.logger.error("在auto_change_price中，悠悠有品客户端尚未初始化。无法继续。")
            return 1
            
        try:
            self.uuyoupin.send_device_info()
            self.logger.info("正在获取悠悠有品出售已上架物品...")
            self.get_uu_sale_inventory() # Populates self.sale_inventory_list

            items_to_change_price_api_format = []
            if not self.sale_inventory_list:
                self.logger.info("没有可用于改价的在售物品.")
                return 0

            filter_names = self.plugin_specific_config.get("name", [])
            blacklist_words = self.plugin_specific_config.get('blacklist_words', [])
            take_profit_enabled = self.plugin_specific_config.get('take_profile', False)
            take_profit_ratio = self.plugin_specific_config.get('take_profile_ratio', 1.15)
            use_price_adj = self.plugin_specific_config.get('use_price_adjustment', True)
            price_adj_threshold = self.plugin_specific_config.get('price_adjustment_threshold', 1.0)
            max_on_sale_price = self.plugin_specific_config.get('max_on_sale_price', 0)


            for item_on_shelf in self.sale_inventory_list: # item_on_shelf is dict from API
                commodity_id_str = str(item_on_shelf.get("id")) # This is CommodityId for PriceChange API
                template_id_str = str(item_on_shelf.get("templateId"))
                short_name = item_on_shelf.get("name", "")
                current_sell_price = float(item_on_shelf.get("sellAmount", 0.0))

                if not any((s and s in short_name) for s in filter_names if s): continue
                if any(bw and bw in short_name for bw in blacklist_words if bw):
                    self.logger.info(f"改价跳过(黑名单)：{short_name}")
                    continue
                
                buy_price = self.buy_price_cache.get(template_id_str, 0.0) # Get cached buy price
                market_sale_price = self.get_market_sale_price(template_id_str) # Get current market price

                final_sale_price = market_sale_price
                if take_profit_enabled and buy_price > 0:
                    target_profit_price = buy_price * take_profit_ratio
                    final_sale_price = max(market_sale_price, target_profit_price)
                    self.logger.info(f"止盈计算(改价): {short_name} - 购入价 {buy_price:.2f}, 市场价 {market_sale_price:.2f}, 止盈目标 {target_profit_price:.2f} -> 参考价 {final_sale_price:.2f}")

                if final_sale_price == 0: continue

                if use_price_adj and final_sale_price > price_adj_threshold:
                    final_sale_price = max(price_adj_threshold, final_sale_price - 0.01)
                
                final_sale_price = round(final_sale_price, 2)

                if max_on_sale_price > 0 and final_sale_price > max_on_sale_price:
                    self.logger.info(f"物品 {short_name} 计算价格 {final_sale_price:.2f} 超过最高限价 {max_on_sale_price:.2f}，不改价或考虑下架")
                    continue
                
                # Only change price if new price is different enough (e.g., more than 0.01)
                if abs(final_sale_price - current_sell_price) > 0.005: # Threshold to avoid tiny changes
                    self.logger.info(f'准备改价：{short_name} - 原价：{current_sell_price:.2f}, 新价：{final_sale_price:.2f}')
                    items_to_change_price_api_format.append({
                        "CommodityId": commodity_id_str, "IsCanLease": False, "IsCanSold": True,
                        "Price": final_sale_price, "Remark": ""
                    })
                else:
                    self.logger.info(f"物品 {short_name} 价格已在合理范围 ({current_sell_price:.2f} vs {final_sale_price:.2f})，无需修改。")


            self.logger.info(f"筛选后，准备为 {len(items_to_change_price_api_format)} 件物品修改价格...")
            self.operate_sleep()
            if items_to_change_price_api_format:
                self.change_sale_price(items_to_change_price_api_format)
            self.logger.info("自动修改价格任务完成.")
            return 0
        except Exception as e:
            self.logger.error(f"悠悠有品自动修改价格出现错误: {e}", exc_info=True)
            if "登录" in str(e) or "token" in str(e).lower():
                 send_notification('检测到悠悠有品登录已经失效,请重新登录', title='悠悠有品登录失效')
                 self.logger.error("检测到悠悠有品登录已经失效.")
            handle_caught_exception(e, self.plugin_name)
            return 1

    def exec(self):
        self.logger.info(f"插件 {self.plugin_name} 开始执行。")
        
        token = get_valid_token_for_uu(self.logger)
        if not token:
            self.logger.error("由于登录失败，插件将自动退出。")
            exit_code.set(1) # Critical failure for exec
            return 1
        self.uuyoupin = uuyoupinapi.UUAccount(token)
        
        self.logger.info(f"以下物品名称包含的关键词将会自动出售：{self.plugin_specific_config.get('name',[])}")
        
        # Initial run
        self.auto_sell()

        run_time_auto_sell = self.plugin_specific_config.get('run_time', "04:00") # Default time
        interval_change_price_minutes = self.plugin_specific_config.get('interval', 60) # Default 60 minutes

        self.logger.info(f"[自动出售] 等待到 {run_time_auto_sell} 开始执行。")
        self.logger.info(f"[自动修改价格] 每隔 {interval_change_price_minutes} 分钟执行一次。")

        schedule.every().day.at(run_time_auto_sell).do(self.auto_sell)
        schedule.every(interval_change_price_minutes).minutes.do(self.auto_change_price)

        while True:
            schedule.run_pending()
            time.sleep(1) # Standard schedule loop sleep

    def operate_sleep(self, sleep_duration=None):
        if sleep_duration is None:
            # Use random sleep from config if available, else default random
            min_sleep = self.plugin_specific_config.get("random_sleep_min_seconds", 5)
            max_sleep = self.plugin_specific_config.get("random_sleep_max_seconds", 15)
            actual_sleep = random.randint(min_sleep, max_sleep)
        else:
            actual_sleep = sleep_duration
        self.logger.info(f"操作间隔，休眠 {actual_sleep} 秒")
        time.sleep(actual_sleep)

    def get_take_profile_price(self, buy_price): # Corrected method name
        take_profile_ratio = self.plugin_specific_config.get('take_profile_ratio', 0.15) # Default 15%
        return buy_price * (1 + take_profile_ratio)
