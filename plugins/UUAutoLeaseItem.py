import datetime
import time
import json5 # Keep if used for config or other JSON files; not directly in this plugin's logic
import numpy as np
import schedule
import uuyoupinapi
from uuyoupinapi import models as uuyoupin_models # Alias for clarity

from utils.plugin_base import PluginBase
from utils.logger import handle_caught_exception # PluginLogger import removed
from utils.notifier import send_notification
from utils.string_utils import is_subsequence # Updated import
from utils.tools import exit_code
from utils.uu_helper import get_valid_token_for_uu
# from utils.models import LeaseAsset # Removed as it seems unused

# Module-level logger removed

class UUAutoLeaseItem(PluginBase):
    def __init__(self, plugin_name: str, main_logger, config: dict, steam_client, steam_client_mutex):
        super().__init__(plugin_name, main_logger, config, steam_client, steam_client_mutex)
        
        self.plugin_specific_config = self.config.get(self.plugin_name, {})
        if not self.plugin_specific_config:
            self.logger.warning(f"在主配置中未找到插件 {self.plugin_name} 的配置。将使用默认值或可能出现问题。")

        self.timeSleep = self.plugin_specific_config.get("time_sleep", 10) 
        self.inventory_list = [] # Seems to be populated in auto_lease
        self.lease_price_cache = {}
        self.compensation_type = self.plugin_specific_config.get("compensation_type", 0)
        
        self.uuyoupin = None # Will be initialized in init or exec
        self.logger.info(f"插件 {self.plugin_name} 已初始化。")

    @property
    def leased_inventory_list(self) -> list: # list of uuyoupin_models.UULeasedItem
        if self.uuyoupin:
            return self.uuyoupin.get_uu_leased_inventory()
        self.logger.warning("尝试获取租赁库存列表时，悠悠有品客户端尚未初始化。")
        return []

    def init(self) -> bool:
        self.logger.debug(f"调用插件 {self.plugin_name} 的特定init方法。")
        token = get_valid_token_for_uu(self.logger) # Pass self.logger
        if not token:
            self.logger.error("悠悠有品登录在初始化期间失败！插件将无法有效运行。")
            return True # Error
        
        # Initialize UU client here if token is obtained and it's needed before exec
        # Or, ensure exec also fetches/uses this token.
        # For now, let's assume exec will handle client init with a fresh token.
        self.logger.info("初始化期间成功获取悠悠有品令牌 (令牌未在init中存储于self)。")
        return False # Success

    def get_lease_price(self, template_id, min_price=0.0, max_price=20000.0, cnt=15):
        # Using float for prices consistently
        min_price = float(min_price)
        max_price = float(max_price) if max_price != 0 else 20000.0 # Ensure max_price is float

        if not self.uuyoupin:
            self.logger.error("在get_lease_price中，悠悠有品客户端尚未初始化。")
            return {"LeaseUnitPrice": 0.0, "LongLeaseUnitPrice": 0.0, "LeaseDeposit": 0.0}

        cached_entry = self.lease_price_cache.get(template_id)
        if cached_entry and (datetime.datetime.now() - cached_entry["cache_time"] <= datetime.timedelta(minutes=20)):
            self.logger.info(
                f"物品 {cached_entry['commodity_name']} 使用缓存价格设置，"
                f"短租价格：{cached_entry['lease_unit_price']:.2f}，长租价格：{cached_entry['long_lease_unit_price']:.2f}，押金：{cached_entry['lease_deposit']:.2f}"
            )
            return {
                "LeaseUnitPrice": cached_entry['lease_unit_price'],
                "LongLeaseUnitPrice": cached_entry['long_lease_unit_price'],
                "LeaseDeposit": cached_entry['lease_deposit'],
            }
        
        rsp_list = self.uuyoupin.get_market_lease_price(template_id, min_price=min_price, max_price=max_price, cnt=cnt)
        commodity_name = ""
        lease_unit_price_list, long_lease_unit_price_list, lease_deposit_list = [], [], []

        if rsp_list: # Check if list is not empty
            commodity_name = rsp_list[0].CommodityName # Assuming rsp_list[0] exists
            for i, item in enumerate(rsp_list):
                if item.LeaseUnitPrice and i < min(10, len(rsp_list)): # len(rsp_list) instead of rsp_cnt
                    lease_unit_price_list.append(float(item.LeaseUnitPrice))
                    if item.LeaseDeposit: # Ensure LeaseDeposit is not None
                        lease_deposit_list.append(float(item.LeaseDeposit))
                if item.LongLeaseUnitPrice: # Ensure LongLeaseUnitPrice is not None
                    long_lease_unit_price_list.append(float(item.LongLeaseUnitPrice))
        
        # Default values if lists are empty
        lease_unit_price_calculated = 0.0
        long_lease_unit_price_calculated = 0.0
        lease_deposit_calculated = 0.0

        if lease_unit_price_list:
            lease_unit_price_calculated = max(float(np.mean(lease_unit_price_list)) * 0.97, float(lease_unit_price_list[0]), 0.01)
        if long_lease_unit_price_list:
            long_lease_unit_price_calculated = max(min(lease_unit_price_calculated * 0.98, float(np.mean(long_lease_unit_price_list)) * 0.95), float(long_lease_unit_price_list[0]), 0.01)
        elif lease_unit_price_calculated > 0 : # If no long lease prices, derive from short lease
             long_lease_unit_price_calculated = max(lease_unit_price_calculated - 0.01, 0.01)

        if lease_deposit_list:
            lease_deposit_calculated = max(float(np.mean(lease_deposit_list)) * 0.98, float(min(lease_deposit_list)))

        self.logger.info(f"短租参考价格列表：{lease_unit_price_list}，长租参考价格列表：{long_lease_unit_price_list}")
        
        final_lup = round(lease_unit_price_calculated, 2)
        final_llup = round(min(long_lease_unit_price_calculated, final_lup if final_lup > 0 else float('inf')), 2) # Ensure long is not more than short
        final_ld = round(lease_deposit_calculated, 2)

        if self.plugin_specific_config.get('enable_fix_lease_ratio', False) and min_price > 0:
            ratio = self.plugin_specific_config.get('fix_lease_ratio', 0.01) # Default ratio e.g. 1%
            final_lup = max(final_lup, min_price * ratio)
            final_llup = max(final_llup, final_lup * 0.98 if final_lup > 0 else 0.01) # Ensure long is derived from potentially updated short
            self.logger.info(f"物品 {commodity_name}，启用比例定价，市场价 {min_price}，租金比例 {ratio}")

        self.logger.info(
            f"物品 {commodity_name}，"
            f"短租价格：{final_lup:.2f}，长租价格：{final_llup:.2f}，押金：{final_ld:.2f}"
        )
        if final_lup != 0: # Only cache if a valid price was determined
            self.lease_price_cache[template_id] = {
                "commodity_name": commodity_name,
                "lease_unit_price": final_lup,
                "long_lease_unit_price": final_llup,
                "lease_deposit": final_ld,
                "cache_time": datetime.datetime.now(),
            }
        return {"LeaseUnitPrice": final_lup, "LongLeaseUnitPrice": final_llup, "LeaseDeposit": final_ld}


    def auto_lease(self):
        self.logger.info("悠悠有品出租自动上架任务开始.")
        self.operate_sleep() # Initial sleep before operations

        if not self.uuyoupin:
            self.logger.error("在auto_lease中，悠悠有品客户端尚未初始化。无法继续。")
            return 1 # Indicate error

        try:
            lease_item_list_to_api = [] # List of uuyoupin_models.UUOnLeaseShelfItem
            self.uuyoupin.send_device_info() # Refresh device info
            self.logger.info("正在获取悠悠有品库存...")
            
            # Fetch full inventory, assuming it might be large and needs refresh
            self.inventory_list = self.uuyoupin.get_inventory(refresh=True) 
            if self.inventory_list is None: # Check if API call failed
                self.logger.error("获取悠悠有品库存失败。")
                return 1

            filter_price_threshold = self.plugin_specific_config.get("filter_price", 0.0)
            filter_name_substrings = self.plugin_specific_config.get("filter_name", [])
            default_lease_max_days = self.plugin_specific_config.get("lease_max_days", 7) # Default 7 days

            for item_data in self.inventory_list: # item_data is dict from UU API
                if item_data.get("AssetInfo") is None: continue # Skip items without AssetInfo

                asset_id = str(item_data.get("SteamAssetId"))
                template_info = item_data.get("TemplateInfo", {})
                template_id = template_info.get("Id")
                short_name = item_data.get("ShotName", "") # ShotName in API, not ShortName
                market_price = float(template_info.get("MarkPrice", 0.0))

                if (market_price < filter_price_threshold or
                    not item_data.get("Tradable", False) or # Check Tradable status
                    item_data.get("AssetStatus") != 0 or # Check AssetStatus
                    any(s and is_subsequence(s, short_name) for s in filter_name_substrings if s)): # Ensure s is not empty
                    continue
                
                self.operate_sleep() # Sleep between API calls for lease price

                price_rsp = self.get_lease_price(template_id, min_price=market_price, max_price=market_price * 2)
                if price_rsp["LeaseUnitPrice"] == 0: continue # Skip if no valid lease price found
                
                lease_item_api_model = uuyoupin_models.UUOnLeaseShelfItem(
                    AssetId=asset_id,
                    IsCanLease=True, IsCanSold=False, # Explicitly set
                    LeaseMaxDays=default_lease_max_days,
                    LeaseUnitPrice=price_rsp["LeaseUnitPrice"],
                    LongLeaseUnitPrice=price_rsp["LongLeaseUnitPrice"],
                    LeaseDeposit=str(price_rsp["LeaseDeposit"]), # API expects string
                    CompensationType=self.compensation_type 
                )
                if default_lease_max_days <= 8: # Logic from original
                    lease_item_api_model.LongLeaseUnitPrice = None
                lease_item_list_to_api.append(lease_item_api_model)

            self.logger.info(f"共 {len(lease_item_list_to_api)} 件物品可以出租。")
            self.operate_sleep()

            if lease_item_list_to_api:
                success_count = self.uuyoupin.put_items_on_lease_shelf(lease_item_list_to_api)
                if success_count > 0: self.logger.info(f"成功上架 {success_count} 个物品。")
                else: self.logger.error("上架失败！请查看日志获得详细信息。")
                if len(lease_item_list_to_api) - success_count > 0:
                    self.logger.error(f"有 {len(lease_item_list_to_api) - success_count} 个商品上架失败。")
            return 0 # Success
        except Exception as e:
            self.logger.error(f"悠悠有品出租出现错误: {e}", exc_info=True)
            # Check for login failure specifically if possible (e.g. specific exception type or message)
            if "登录" in str(e) or "token" in str(e).lower(): # Basic check
                 send_notification('检测到悠悠有品登录已经失效,请重新登录', title='悠悠有品登录失效')
                 self.logger.error("检测到悠悠有品登录已经失效,请重新登录。插件将尝试在下次运行时重新登录。")
                 # Don't set exit_code here, let schedule handle retries or main loop decide.
            handle_caught_exception(e, self.plugin_name)
            return 1 # Indicate error for this run

    def auto_change_price(self):
        self.logger.info("悠悠出租自动修改价格任务开始.")
        self.operate_sleep(15) # Initial sleep

        if not self.uuyoupin:
            self.logger.error("在auto_change_price中，悠悠有品客户端尚未初始化。无法继续。")
            return 1

        try:
            self.uuyoupin.send_device_info()
            self.logger.info("正在获取悠悠有品出租已上架物品...")
            # leased_inventory_list is a property calling self.uuyoupin.get_uu_leased_inventory()
            # The items in this list are uuyoupin_models.UULeasedItem
            current_leased_items = self.leased_inventory_list 
            if current_leased_items is None: # API call failed
                self.logger.error("获取已上架出租物品失败.")
                return 1
            
            items_to_update_api = [] # List of uuyoupin_models.UULeasedItem

            filter_name_substrings = self.plugin_specific_config.get("filter_name", [])
            default_lease_max_days = self.plugin_specific_config.get("lease_max_days", 7)

            for item in current_leased_items: # item is uuyoupin_models.UULeasedItem
                template_id = item.templateid # Correct attribute name from model
                short_name = item.short_name
                current_market_price = float(item.price) # Assuming 'price' is the market price

                if any(s and is_subsequence(s, short_name) for s in filter_name_substrings if s):
                    continue

                price_rsp = self.get_lease_price(template_id, min_price=current_market_price, max_price=current_market_price * 2)
                if price_rsp["LeaseUnitPrice"] == 0: continue

                # Update item directly if it's a mutable object and API expects the same model type back
                item.LeaseUnitPrice = price_rsp["LeaseUnitPrice"]
                item.LongLeaseUnitPrice = price_rsp["LongLeaseUnitPrice"]
                item.LeaseDeposit = str(price_rsp["LeaseDeposit"]) # Ensure string for API
                item.LeaseMaxDays = default_lease_max_days
                if default_lease_max_days <= 8:
                    item.LongLeaseUnitPrice = None
                
                items_to_update_api.append(item) # Add the modified item

            self.logger.info(f"{len(items_to_update_api)} 件物品可以更新出租价格。")
            self.operate_sleep()

            if items_to_update_api:
                # Pass compensation_type to change_leased_price
                success_count = self.uuyoupin.change_leased_price(items_to_update_api, compensation_type=self.compensation_type)
                self.logger.info(f"成功修改 {success_count} 件物品出租价格。")
                if len(items_to_update_api) - success_count > 0:
                    self.logger.error(f"{len(items_to_update_api) - success_count} 件物品出租价格修改失败。")
            else:
                self.logger.info(f"没有物品可以修改价格。")
            return 0
        except Exception as e:
            self.logger.error(f"悠悠有品自动修改价格出现错误: {e}", exc_info=True)
            if "登录" in str(e) or "token" in str(e).lower():
                 send_notification('检测到悠悠有品登录已经失效,请重新登录', title='悠悠有品登录失效')
                 self.logger.error("检测到悠悠有品登录已经失效,请重新登录。")
            handle_caught_exception(e, self.plugin_name)
            return 1

    def auto_set_zero_cd(self):
        self.logger.info("悠悠有品出租自动设置0cd任务开始.")
        self.operate_sleep()

        if not self.uuyoupin:
            self.logger.error("在auto_set_zero_cd中，悠悠有品客户端尚未初始化。无法继续。")
            return 1
            
        try:
            zero_cd_valid_list = self.uuyoupin.get_zero_cd_list()
            if zero_cd_valid_list is None:
                self.logger.error("获取0CD列表失败.")
                return 1

            enable_zero_cd_order_ids = []
            filter_name_substrings = self.plugin_specific_config.get("filter_name", [])

            for order_data in zero_cd_valid_list: # order_data is dict from API
                name = order_data.get("commodityInfo", {}).get("name", "")
                if any(s and is_subsequence(s, name) for s in filter_name_substrings if s):
                    continue
                enable_zero_cd_order_ids.append(int(order_data["orderId"]))
            
            self.logger.info(f"共 {len(enable_zero_cd_order_ids)} 件物品可以设置为0cd。")
            if enable_zero_cd_order_ids:
                self.uuyoupin.enable_zero_cd(enable_zero_cd_order_ids)
                self.logger.info("0CD设置请求已发送。") # API may not return explicit success count
            return 0
        except Exception as e:
            self.logger.error(f"自动设置0CD出现错误: {e}", exc_info=True)
            handle_caught_exception(e, self.plugin_name)
            return 1

    def exec(self):
        self.logger.info(f"插件 {self.plugin_name} 开始执行。")
        self.logger.info(f"以下物品名称包含的关键词将不会出租：{self.plugin_specific_config.get('filter_name', [])}")
        
        # Initialize UU client at the start of exec
        token = get_valid_token_for_uu(self.logger)
        if not token:
            self.logger.error("由于登录失败，插件将自动退出。")
            exit_code.set(1) # Critical failure for exec
            return 1
        self.uuyoupin = uuyoupinapi.UUAccount(token)

        # Initial runs
        self.pre_check_price() # This calls get_lease_price which needs self.uuyoupin
        self.auto_lease()
        self.auto_set_zero_cd()

        run_time_auto_lease = self.plugin_specific_config.get('run_time', "03:00") # Default time
        interval_change_price_minutes = self.plugin_specific_config.get('interval', 60) # Default 60 minutes
        run_time_zero_cd = self.plugin_specific_config.get('zero_cd_run_time', "23:30") # Default time

        self.logger.info(f"[自动上架] 等待到 {run_time_auto_lease} 开始执行。")
        self.logger.info(f"[自动修改价格] 每隔 {interval_change_price_minutes} 分钟执行一次。")
        self.logger.info(f"[设置0cd] 等待到 {run_time_zero_cd} 开始执行。")

        schedule.every().day.at(run_time_auto_lease).do(self.auto_lease)
        schedule.every(interval_change_price_minutes).minutes.do(self.auto_change_price)
        schedule.every().day.at(run_time_zero_cd).do(self.auto_set_zero_cd)

        while True:
            schedule.run_pending()
            time.sleep(1) # Standard schedule loop sleep

    def operate_sleep(self, sleep_duration=None): # Renamed parameter for clarity
        actual_sleep = sleep_duration if sleep_duration is not None else self.timeSleep
        self.logger.debug(f"休眠 {actual_sleep} 秒...")
        time.sleep(actual_sleep)

    def pre_check_price(self): # Needs self.uuyoupin to be initialized
        if not self.uuyoupin:
            self.logger.error("在pre_check_price中，悠悠有品客户端尚未初始化。无法继续。")
            return
        # Example template_id and price for pre-check, consider making these configurable
        pre_check_template_id = self.plugin_specific_config.get("pre_check_template_id", 44444) # Example
        pre_check_min_price = self.plugin_specific_config.get("pre_check_min_price", 1000.0)
        self.get_lease_price(pre_check_template_id, pre_check_min_price)
        self.logger.info("价格预检查完成。请检查押金获取是否有问题，如有请终止程序，否则开始运行该插件。")
        self.operate_sleep()
