import copy
import datetime
import json
import os
import time
from threading import Thread
from typing import Dict, List, Union

from BuffApi import BuffAccount
from BuffApi.models import BuffOnSaleAsset
from PyECOsteam import ECOsteamClient, models as PyECOsteam_models # Aliased to avoid conflict
from steampy.client import SteamClient
# Removed: from utils import static (ECOSTEAM_RSAKEY_FILE and STEAM_64_ID will be handled differently)
from utils.buff_helper import get_valid_session_for_buff
from utils.logger import LogFilter, handle_caught_exception # PluginLogger removed
from utils.models import Asset, LeaseAsset, ModelEncoder
from utils.notifier import send_notification
from utils.static import ECOSTEAM_RSAKEY_FILE # Correct import for constant
from utils.steam_client import accept_trade_offer, get_cs2_inventory
from utils.tools import exit_code # Correct import
from utils.file_utils import get_encoding # Correct import
from utils.uu_helper import get_valid_token_for_uu
from uuyoupinapi import UUAccount
from utils.plugin_base import PluginBase # Added

# Globals removed: sync_sell_shelf_enabled, sync_lease_shelf_enabled, uu_queue, eco_queue, loggers


class ECOsteamPlugin(PluginBase):

    class tasks: # Nested class for tasks
        def __init__(self, client, steamid, logger_instance): # Added logger_instance
            self.sell_queue = []
            self.sell_change_queue = []
            self.lease_queue = []
            self.lease_change_queue = []
            self.client = client
            self.steamid = steamid
            self.logger = logger_instance # Use passed logger
            if isinstance(self.client, ECOsteamClient):
                self.platform = "ECOsteam"
            elif isinstance(self.client, UUAccount):
                self.platform = "悠悠有品"
            else:
                self.platform = "UnknownPlatform"


        def sell_add(self, assets: List[Asset]):
            self.sell_queue.extend(assets) # Use extend for list

        def sell_change(self, assets: List[Asset]):
            self.sell_change_queue.extend(assets)

        def sell_remove(self, assetId: str): # Not directly used in provided exec, but keep for completeness
            self.sell_queue = [asset for asset in self.sell_queue if asset.assetid != assetId]

        def lease_add(self, assets: List[LeaseAsset]):
            self.lease_queue.extend(assets)

        def lease_change(self, assets: List[LeaseAsset]):
            self.lease_change_queue.extend(assets)

        def lease_remove(self, assetId: str): # Not directly used, but keep
            self.lease_queue = [asset for asset in self.lease_queue if asset.assetid != assetId]
            
        def process(self):
            self.logger.debug(f"{self.platform} 出售队列：{json.dumps(self.sell_queue, cls=ModelEncoder, ensure_ascii=False)}")
            self.logger.debug(f"{self.platform} 租赁队列：{json.dumps(self.lease_queue, cls=ModelEncoder, ensure_ascii=False)}")
            self.logger.debug(f"{self.platform} 出售改价队列：{json.dumps(self.sell_change_queue, cls=ModelEncoder, ensure_ascii=False)}")
            self.logger.debug(f"{self.platform} 租赁改价队列：{json.dumps(self.lease_change_queue, cls=ModelEncoder, ensure_ascii=False)}")

            if self.sell_queue or self.lease_queue or self.sell_change_queue or self.lease_change_queue:
                self.logger.info(f"{self.platform} 平台任务队列开始执行")
            else:
                self.logger.info(f"{self.platform} 平台任务队列为空，不需要处理")
                return # No tasks to process

            # Process sell and lease additions together
            if self.sell_queue or self.lease_queue:
                self.logger.info(f'即将向 {self.platform} 出售货架上架 {len(self.sell_queue)} 个商品')
                self.logger.info(f'即将向 {self.platform} 租赁货架上架 {len(self.lease_queue)} 个商品')
                success_count, failure_count = 0, 0
                if isinstance(self.client, ECOsteamClient):
                    success_count, failure_count = self.client.PublishRentAndSaleGoods(self.steamid, 1, self.sell_queue, self.lease_queue)
                elif isinstance(self.client, UUAccount):
                    success_count, failure_count = self.client.onshelf_sell_and_lease(self.sell_queue, self.lease_queue)
                
                self.sell_queue = [] # Clear queue after processing
                self.lease_queue = [] 

                if failure_count != 0: self.logger.error(f'上架失败 {failure_count} 个商品到 {self.platform}')
                self.logger.info(f'上架成功 {success_count} 个商品到 {self.platform}')

            # Process sell and lease changes together
            if self.sell_change_queue or self.lease_change_queue:
                self.logger.info(f'即将在 {self.platform} 出售货架改价 {len(self.sell_change_queue)} 个商品')
                self.logger.info(f'即将在 {self.platform} 租赁货架改价 {len(self.lease_change_queue)} 个商品')
                success_count, failure_count = 0, 0
                if isinstance(self.client, ECOsteamClient):
                    success_count, failure_count = self.client.PublishRentAndSaleGoods(self.steamid, 2, self.sell_change_queue, self.lease_change_queue)
                elif isinstance(self.client, UUAccount):
                    success_count, failure_count = self.client.change_price_sell_and_lease(self.sell_change_queue, self.lease_change_queue)

                self.sell_change_queue = [] # Clear queue
                self.lease_change_queue = []

                if failure_count != 0: self.logger.error(f'改价失败 {failure_count} 个商品在 {self.platform}')
                self.logger.info(f'改价成功 {success_count} 个商品在 {self.platform}')


    @staticmethod
    def compare_shelves(A: List[Asset], B: List[Asset], ratio: float, logger_instance) -> Union[bool, dict[str, list[Asset]]]:
        result = {"add": [], "delete": [], "change": []}
        ratio = round(ratio, 2)

        A_clean = [asset for asset in A if isinstance(asset, Asset)]
        B_clean = [asset for asset in B if isinstance(asset, Asset)]
        if len(A_clean) != len(A): logger_instance.debug("A列表可能存在未下架的物品 (已清理)")
        if len(B_clean) != len(B): logger_instance.debug("B列表可能存在未下架的物品 (已清理)")
        
        A = A_clean
        B = B_clean

        A_dict = {item.assetid: item for item in A}
        B_dict = {item.assetid: item for item in B}

        for assetid, item_A in A_dict.items():
            if assetid not in B_dict:
                adjusted_item = item_A.model_copy()
                adjusted_item.price = round(adjusted_item.price / ratio, 2)
                result["add"].append(adjusted_item)
            else: # Item exists in both, check for price change
                item_B = B_dict[assetid]
                if abs(round(item_A.price / item_B.price - ratio, 2)) > 0.01:
                    adjusted_item_B = item_B.model_copy() # Change price on B's perspective for the target platform
                    adjusted_item_B.price = round(item_A.price / ratio, 2)
                    result["change"].append(adjusted_item_B)
        
        for assetid, item_B in B_dict.items():
            if assetid not in A_dict:
                result["delete"].append(item_B)
        return result

    @staticmethod
    def compare_lease_shelf(A: List[LeaseAsset], B: List[LeaseAsset], ratio: float, logger_instance) -> Dict[str, List[LeaseAsset]]:
        result = {"add": [], "delete": [], "change": []}
        ratio = round(ratio, 2)

        A_clean = [asset for asset in A if isinstance(asset, LeaseAsset)] # Ensure LeaseAsset
        B_clean = [asset for asset in B if isinstance(asset, LeaseAsset)]
        if len(A_clean) != len(A): logger_instance.debug("租赁货架A列表存在非LeaseAsset物品 (已清理)")
        if len(B_clean) != len(B): logger_instance.debug("租赁货架B列表存在非LeaseAsset物品 (已清理)")

        A = A_clean
        B = B_clean
        
        A_dict = {item.assetid: item for item in A}
        B_dict = {item.assetid: item for item in B}

        for assetid, item_A in A_dict.items():
            if assetid not in B_dict:
                # For lease, when adding to B, B should match A's terms but adjusted by ratio
                adjusted_item_A_for_B = item_A.model_copy()
                adjusted_item_A_for_B.LeaseUnitPrice = round(item_A.LeaseUnitPrice / ratio, 2)
                if item_A.LongLeaseUnitPrice:
                    adjusted_item_A_for_B.LongLeaseUnitPrice = round(item_A.LongLeaseUnitPrice / ratio, 2)
                # LeaseDeposit and LeaseMaxDays are usually direct copies from main platform, not ratio adjusted.
                result["add"].append(adjusted_item_A_for_B)
            else:
                item_B = B_dict[assetid]
                changes_needed = False
                if item_A.LeaseDeposit != item_B.LeaseDeposit: changes_needed = True
                if item_A.LeaseMaxDays != item_B.LeaseMaxDays: changes_needed = True
                if round(abs(item_A.LeaseUnitPrice / item_B.LeaseUnitPrice - ratio), 2) >= 0.01: changes_needed = True
                
                if item_A.LongLeaseUnitPrice is not None and item_B.LongLeaseUnitPrice is not None:
                    if round(abs(item_A.LongLeaseUnitPrice / item_B.LongLeaseUnitPrice - ratio), 2) >= 0.01: changes_needed = True
                elif item_A.LongLeaseUnitPrice != item_B.LongLeaseUnitPrice: # One is None, other is not
                    changes_needed = True

                if changes_needed:
                    adjusted_item_B = item_B.model_copy() # We are changing B to match A (after ratio)
                    adjusted_item_B.LeaseDeposit = item_A.LeaseDeposit
                    adjusted_item_B.LeaseMaxDays = item_A.LeaseMaxDays
                    adjusted_item_B.LeaseUnitPrice = round(item_A.LeaseUnitPrice / ratio, 2)
                    adjusted_item_B.LongLeaseUnitPrice = round(item_A.LongLeaseUnitPrice / ratio, 2) if item_A.LongLeaseUnitPrice is not None else None
                    result["change"].append(adjusted_item_B)

        for assetid, item_B in B_dict.items():
            if assetid not in A_dict:
                result["delete"].append(item_B)
        return result


    def __init__(self, plugin_name: str, main_logger, config: dict, steam_client, steam_client_mutex):
        super().__init__(plugin_name, main_logger, config, steam_client, steam_client_mutex)
        
        self.plugin_specific_config = self.config.get(self.plugin_name, {}) # e.g., self.config.get("ecosteam", {})
        if not self.plugin_specific_config:
             self.logger.warning(f"在主配置中未找到插件 {self.plugin_name} 的配置。将使用默认值或可能出现问题。")

        self.sync_sell_shelf_enabled = False
        self.sync_lease_shelf_enabled = False
        self.uu_queue = None
        self.eco_queue = None
        
        self.ignored_offer = []
        self.client = None # ECOsteamClient
        self.buff_client = None
        self.uu_client = None
        self.lease_main_platform = None # Set in auto_sync_shelves

        self.logger.info(f"插件 {self.plugin_name} 已初始化。")


    def init(self) -> bool:
        self.logger.debug(f"调用插件 {self.plugin_name} 的特定init方法。")
        if not os.path.exists(ECOSTEAM_RSAKEY_FILE):
            try:
                with open(ECOSTEAM_RSAKEY_FILE, "w", encoding="utf-8") as f:
                    f.write("")
                self.logger.warning(f"{ECOSTEAM_RSAKEY_FILE} 未找到，已创建一个空文件。请配置以使用ECOsteam功能。")
                # Not returning True here as the plugin might have other functions (e.g. only sync with Buff/UU)
                # Actual client init in exec will fail if key is needed and missing/empty.
            except Exception as e:
                self.logger.error(f"在 {ECOSTEAM_RSAKEY_FILE} 创建空的RSA密钥文件失败: {e}")
                return True # Error creating file
        
        # Check if RSA key file is empty
        try:
            if os.path.getsize(ECOSTEAM_RSAKEY_FILE) == 0:
                 self.logger.warning(f"{ECOSTEAM_RSAKEY_FILE} 为空。如果需要使用ECOsteam平台，请配置此文件。")
        except OSError as e: # File might not exist if creation failed or was deleted
            self.logger.error(f"无法访问RSA密钥文件 {ECOSTEAM_RSAKEY_FILE}: {e}")
            # If ECOsteam is essential, this could be a True (error) return.
            # For now, allow plugin to load, exec will handle client init failure.

        return super().init() # False for success

    def _get_current_steam_id(self) -> Union[str, None]:
        """Safely gets current Steam ID, returns None if error."""
        try:
            with self.steam_client_mutex:
                if self.steam_client and hasattr(self.steam_client, 'get_steam64id_from_cookies'):
                    return self.steam_client.get_steam64id_from_cookies()
            self.logger.error("Steam客户端不可用或未登录，无法获取SteamID。")
        except Exception as e:
            self.logger.error(f"获取SteamID时出错: {e}")
        return None

    def exec(self):
        self.logger.info(f"插件 {self.plugin_name} 开始执行。")
        
        partner_id = self.plugin_specific_config.get("partnerId")
        rsa_key_path = ECOSTEAM_RSAKEY_FILE # From static imports
        qps = self.plugin_specific_config.get("qps", 1) # Default QPS

        if not partner_id:
            self.logger.error("ECOsteam partnerId未配置。ECOsteam相关功能将被禁用。")
        elif not os.path.exists(rsa_key_path) or os.path.getsize(rsa_key_path) == 0:
            self.logger.error(f"路径 {rsa_key_path} 的ECOsteam RSA密钥丢失或为空。ECOsteam相关功能将被禁用。")
        else:
            try:
                with open(rsa_key_path, "r", encoding=get_encoding(rsa_key_path)) as f:
                    rsa_key_content = f.read()
                if "PUBLIC" in rsa_key_content:
                    self.logger.error("RSA密钥文件似乎包含公钥。ECOsteam需要私钥！")
                    self.client = None # Ensure client is not initialized
                else:
                    LogFilter.add_sensitive_data(partner_id) # Add partnerId to sensitive data for logging
                    self.client = ECOsteamClient(partner_id, rsa_key_content, qps=qps)
                    user_info_resp = self.client.GetTotalMoney().json() # Test call
                    user_data = user_info_resp.get("ResultData", {})
                    if user_data.get("UserName"):
                        self.logger.info(f'ECOsteam:登录成功，用户ID为{user_data["UserName"]}，当前余额为{user_data["Money"]}元')
                    else:
                        self.logger.error(f"ECOsteam:登录失败 (可能key或partnerId错误). Response: {user_info_resp}")
                        self.client = None # Failed login
            except Exception as e:
                self.logger.error(f"ECOsteam:登录失败！请检查{rsa_key_path}和partnerId是否正确！")
                handle_caught_exception(e, self.plugin_name, known=True)
                self.client = None # Failed init

        current_steam_id = self._get_current_steam_id()
        if not current_steam_id:
            self.logger.error("无法获取当前SteamID，插件部分功能可能无法正常运行。")
            # Depending on features, might return 1 here.

        if self.client and current_steam_id: # Only proceed if ECOsteam client initialized and steam_id available
            try:
                accounts_list = self.client.QuerySteamAccountList().json().get("ResultData", [])
                if not any(account.get("SteamId") == current_steam_id for account in accounts_list):
                    self.logger.error(f"当前登录的Steam账号{current_steam_id}不在ECOsteam绑定账号列表内！ECOsteam相关功能将受限。")
                    # self.client = None # Optionally disable client if account not bound
                elif len(accounts_list) > 1:
                    self.logger.warning(f"检测到你的ECOsteam绑定了多个Steam账号。插件的所有ECOsteam操作仅对SteamID为{current_steam_id}的账号生效！")
            except Exception as e:
                 self.logger.error(f"检查ECOsteam绑定账号时出错: {e}")


        threads = []
        if self.plugin_specific_config.get("auto_accept_offer", {}).get("enable", False): # Check if feature enabled
            threads.append(Thread(target=self.auto_accept_offer, args=(current_steam_id,))) # Pass steam_id
        
        sync_sell_config = self.plugin_specific_config.get("auto_sync_sell_shelf", {})
        sync_lease_config = self.plugin_specific_config.get("auto_sync_lease_shelf", {})

        if sync_sell_config.get("enable") or sync_lease_config.get("enable"):
            threads.append(Thread(target=self.auto_sync_shelves, args=(current_steam_id,)))

        if not threads:
            self.logger.info("没有启用的功能 (自动发货/同步货架). 插件将不执行任何操作.")
            return 0

        for thread in threads:
            thread.daemon = True # Ensure threads don't block program exit
            thread.start()
        for thread in threads: # Wait for all threads to complete if they are short-lived.
            thread.join()       # If they are infinite loops, this join will block main thread.
                                # This implies the threads should handle their own looping and termination.
                                # Or the plugin's exec is expected to run indefinitely.
        
        self.logger.info(f"插件 {self.plugin_name} exec方法设置完成。工作线程正在运行。")
        return 0 # Main exec thread can exit if worker threads are daemonized and meant to run forever.


    def get_shelf(self, platform_name: str, current_steam_id: str, inventory: dict):
        assets = []
        self.logger.info(f"[{platform_name.upper()}] 正在获取上架物品信息...")
        
        if platform_name == "eco":
            if not self.client: self.logger.error("ECOsteam client未初始化."); return []
            result = self.client.getFullSellGoodsList(current_steam_id) # Pass current_steam_id
            for item in result: # Assuming result is a list of dicts
                asset = Asset(assetid=item["AssetId"], orderNo=item["GoodsNum"], price=float(item["Price"]))
                inventory_item_details = inventory.get(asset.assetid)
                if inventory_item_details:
                    asset.appid = inventory_item_details["appid"]
                    asset.classid = inventory_item_details["classid"]
                    # ... copy other fields ...
                    asset.market_hash_name = inventory_item_details["market_hash_name"]
                    assets.append(asset)
                else:
                    self.logger.warning(f"[ECO] 检测到上架物品 {item.get('GoodsName', asset.assetid)} 不在Steam库存中！将标记为待下架（逻辑在sync中处理）。")
                    # Mark for removal by returning its identifier, not Asset object
                    assets.append(asset.orderNo) # Or some other way to flag for removal
            
        elif platform_name == "buff":
            if not self.buff_client: self.logger.error("Buff client未初始化."); return []
            # Simplified, assumes get_on_sale returns items with asset_info
            buff_data = self.buff_client.get_on_sale().json().get("data", {})
            buff_items = buff_data.get("items", [])
            # Handle pagination if necessary
            for item in buff_items:
                asset_info = item.get("asset_info", {})
                asset = Asset(assetid=asset_info.get("assetid"), orderNo=item.get("id"), price=float(item.get("price",0)))
                inventory_item_details = inventory.get(asset.assetid)
                if inventory_item_details:
                    asset.appid = inventory_item_details["appid"]
                    # ... copy other fields ...
                    asset.market_hash_name = inventory_item_details["market_hash_name"]
                    assets.append(asset)
                else:
                    goods_name = buff_data.get("goods_infos", {}).get(str(item.get("goods_id")), {}).get("market_hash_name", asset.assetid)
                    self.logger.warning(f"[BUFF] 检测到上架物品 {goods_name} 不在Steam库存中！将标记为待下架。")
                    assets.append(asset.orderNo)

        elif platform_name == "uu":
            if not self.uu_client: self.logger.error("UU client未初始化."); return []
            uu_items = self.uu_client.get_sell_list() # Assumes returns list of dicts
            for item in uu_items:
                asset = Asset(assetid=str(item.get("steamAssetId")), orderNo=item.get("id"), price=float(item.get("sellAmount",0)))
                inventory_item_details = inventory.get(asset.assetid)
                if inventory_item_details:
                    asset.appid = inventory_item_details["appid"]
                    # ... copy other fields ...
                    asset.market_hash_name = inventory_item_details["market_hash_name"]
                    assets.append(asset)
                else:
                    self.logger.warning(f"[UU] 检测到上架物品 {item.get('name', asset.assetid)} 不在Steam库存中！将标记为待下架。")
                    assets.append(asset.orderNo)
        
        self.logger.info(f"[{platform_name.upper()}] 共上架{len(assets)}个物品 (有效/待检查)")
        return assets


    def auto_accept_offer(self, current_steam_id: str): # Added current_steam_id
        if not self.client:
            self.logger.error("[AutoAccept] ECOsteam client 未初始化, 无法执行自动发货.")
            return
        if not current_steam_id:
            self.logger.error("[AutoAccept] SteamID 未提供, 无法执行自动发货.")
            return

        interval = self.plugin_specific_config.get("auto_accept_offer", {}).get("interval", 60)
        while True:
            try:
                self.logger.info("[AutoAccept] 正在检查待发货列表...")
                today = datetime.datetime.today()
                last_month = today - datetime.timedelta(days=30) # Check last 30 days
                
                # Use PyECOsteam_models for ECO specific models if needed, else general dicts
                wait_deliver_orders = self.client.getFullSellerOrderList(
                    last_month.strftime("%Y-%m-%d"), 
                    today.strftime("%Y-%m-%d"), # Only up to today, not tomorrow
                    DetailsState=8, # Waiting for seller to deliver
                    SteamId=current_steam_id
                )
                self.logger.info(f"[AutoAccept] 检测到{len(wait_deliver_orders)}个待发货订单！")

                for order in wait_deliver_orders:
                    if '等待发送报价' in order.get('CancelReason', ''):
                        self.logger.warning(f"[AutoAccept] 订单号{order['OrderNum']}等待发送报价，暂时跳过处理")
                        continue
                    
                    self.logger.debug(f'[AutoAccept] 正在获取订单号{order["OrderNum"]}的详情！')
                    # Ensure a small delay if making many API calls
                    time.sleep(self.plugin_specific_config.get("api_request_interval_short", 0.3)) 
                    detail_resp = self.client.GetSellerOrderDetail(OrderNum=order["OrderNum"]).json()
                    detail = detail_resp.get("ResultData", {})

                    tradeOfferId = detail.get("TradeOfferId")
                    goodsName = detail.get("GoodsName", "未知商品")
                    sellingPrice = detail.get("TotalMoney", "N/A")
                    buyerNickName = detail.get("BuyerNickname", "N/A")

                    if not tradeOfferId:
                        self.logger.warning(f"[AutoAccept] 商品{goodsName}无法获取到交易报价号(可能ECO服务器正在发送报价)，暂时跳过处理")
                        continue
                    
                    if tradeOfferId not in self.ignored_offer:
                        self.logger.info(f"[AutoAccept] 正在发货商品{goodsName}，报价号{tradeOfferId}...")
                        if accept_trade_offer(self.steam_client, self.steam_client_mutex, tradeOfferId, 
                                              desc=f"发货平台：ECOsteam\n发货饰品：{goodsName}\n饰品价格：{sellingPrice}\n买家昵称：{buyerNickName}"):
                            self.logger.info(f"[AutoAccept] 已经成功发货商品{goodsName}，报价号{tradeOfferId}")
                            self.ignored_offer.append(tradeOfferId)
                        # Optional: Add delay between accepts if needed
                    else:
                        self.logger.info(f"[AutoAccept] 已经自动忽略报价号{tradeOfferId}，商品名{goodsName}，因为它已经被程序处理过！")
            except Exception as e:
                handle_caught_exception(e, self.plugin_name)
                self.logger.error("[AutoAccept] 发生未知错误，请稍候再试！")
            
            self.logger.info(f"[AutoAccept] 等待{interval}秒后继续检查待发货列表...")
            time.sleep(interval)


    def auto_sync_shelves(self, current_steam_id: str): # Added current_steam_id
        if not current_steam_id:
            self.logger.error("[SyncShelves] SteamID 未提供, 无法执行货架同步.")
            return

        # --- Initialization of clients (Buff, UU) and queues ---
        sync_sell_config = self.plugin_specific_config.get("auto_sync_sell_shelf", {})
        sync_lease_config = self.plugin_specific_config.get("auto_sync_lease_shelf", {})

        self.sync_sell_shelf_enabled = sync_sell_config.get("enable", False)
        self.sync_lease_shelf_enabled = sync_lease_config.get("enable", False)

        if not self.sync_sell_shelf_enabled and not self.sync_lease_shelf_enabled:
            self.logger.info("[SyncShelves] 销售和租赁货架同步均未启用。")
            return

        # Initialize ECO queue if ECO client is available
        if self.client:
            self.eco_queue = self.tasks(self.client, current_steam_id, self.logger)
        else:
            self.logger.warning("[SyncShelves] ECOsteam client未初始化，ECO相关同步将不可用。")

        # Initialize UU client and queue if UU is enabled in any sync config
        uu_needed = False
        if self.sync_sell_shelf_enabled and "uu" in sync_sell_config.get("enabled_platforms", []):
            uu_needed = True
        if self.sync_lease_shelf_enabled and (sync_lease_config.get("main_platform") == "uu" or "uu" in sync_lease_config.get("enabled_platforms", [])): # Assuming enabled_platforms for lease too
            uu_needed = True
        
        if uu_needed:
            self.logger.info("[SyncShelves] 正在联系UULoginSolver获取有效的session...")
            token = get_valid_token_for_uu()
            if token:
                self.uu_client = UUAccount(token)
                self.uu_queue = self.tasks(self.uu_client, current_steam_id, self.logger)
                self.logger.info("[SyncShelves] UUAccount 初始化成功.")
            else:
                self.logger.warning("[SyncShelves] 无法获取有效的悠悠token，悠悠有品平台相关同步将不可用.")
                if "uu" in sync_sell_config.get("enabled_platforms", []): sync_sell_config["enabled_platforms"].remove("uu")
                # Similar logic if lease_config has enabled_platforms for UU

        # Initialize Buff client if Buff is enabled in sell sync config
        if self.sync_sell_shelf_enabled and "buff" in sync_sell_config.get("enabled_platforms", []):
            self.logger.info("[SyncShelves] 正在联系BuffLoginSolver获取有效的session...")
            buff_session_cookie = get_valid_session_for_buff(self.steam_client, self.logger) # Pass self.logger
            if not buff_session_cookie:
                self.logger.warning("[SyncShelves] 无法获取有效的BUFF session，BUFF平台相关同步将不可用.")
                if "buff" in sync_sell_config.get("enabled_platforms", []): sync_sell_config["enabled_platforms"].remove("buff")
            else:
                self.buff_client = BuffAccount(buff_session_cookie)
                self.logger.info("[SyncShelves] BuffAccount 初始化成功.")
        
        # Validate main_platform for lease sync
        if self.sync_lease_shelf_enabled:
            self.lease_main_platform = sync_lease_config.get("main_platform")
            if self.lease_main_platform not in ["uu", "eco"]:
                self.logger.error("[SyncShelves] 租赁货架主平台配置必须为uu或eco！请检查配置文件！租赁同步已禁用。")
                self.sync_lease_shelf_enabled = False
            elif self.lease_main_platform == "uu" and not self.uu_client:
                self.logger.error("[SyncShelves] 租赁主平台配置为UU，但UU client未初始化。租赁同步已禁用。")
                self.sync_lease_shelf_enabled = False
            elif self.lease_main_platform == "eco" and not self.client:
                 self.logger.error("[SyncShelves] 租赁主平台配置为ECO，但ECO client未初始化。租赁同步已禁用。")
                 self.sync_lease_shelf_enabled = False


        sync_interval = self.plugin_specific_config.get("sync_interval", 300) # Default 5 mins
        while True:
            if self.sync_sell_shelf_enabled:
                self.sync_sell_shelves(current_steam_id, sync_sell_config)
            if self.sync_lease_shelf_enabled:
                self.sync_lease_shelves(current_steam_id, sync_lease_config)
            
            if self.eco_queue: self.eco_queue.process()
            if self.uu_queue: self.uu_queue.process()
            # Buff does not use a queue system in this plugin for adding/changing.
            
            self.logger.info(f'[SyncShelves] 等待 {sync_interval} 秒后重新检查多平台上架物品')
            time.sleep(sync_interval)


    def sync_lease_shelves(self, current_steam_id: str, lease_config: dict): # Pass config
        self.logger.info("[SyncLease] 开始同步租赁货架...")
        shelves = {'eco': [], 'uu': []} # Initialize with empty lists

        if self.client: # ECOsteam
            shelves['eco'] = self.client.getFulRentGoodsList(current_steam_id)
            self.logger.debug(f'[SyncLease] ECO租赁货架：{json.dumps(shelves["eco"], cls=ModelEncoder, ensure_ascii=False)}')
            self.logger.info(f"[SyncLease] ECOsteam共上架{len(shelves['eco'])}个租赁物品")
        else: self.logger.warning("[SyncLease] ECOsteam client 未初始化, 无法获取ECO租赁货架.")

        if self.uu_client: # UUYoupin
            shelves['uu'] = self.uu_client.get_uu_leased_inventory()
            self.logger.debug(f'[SyncLease] 悠悠租赁货架：{json.dumps(shelves["uu"], cls=ModelEncoder, ensure_ascii=False)}')
            self.logger.info(f"[SyncLease] 悠悠有品共上架{len(shelves['uu'])}个租赁物品")
        else: self.logger.warning("[SyncLease] UU client 未初始化, 无法获取UU租赁货架.")

        main_platform = self.lease_main_platform # Should be 'eco' or 'uu'
        other_platform = "uu" if main_platform == "eco" else "eco"
        
        if not shelves[main_platform] and not shelves[other_platform]:
            self.logger.info("[SyncLease] 双平台租赁货架均为空或无法获取，跳过比较。")
            return

        self.logger.info(f'[SyncLease] 当前同步主平台为{main_platform.upper()}')
        self.logger.debug(f'[SyncLease] 即将比较{main_platform.upper()}平台和{other_platform.upper()}平台的租赁上架物品')
        
        main_platform_ratio = lease_config.get("ratio", {}).get(main_platform, 1.0)
        other_platform_ratio = lease_config.get("ratio", {}).get(other_platform, 1.0)
        if other_platform_ratio == 0: self.logger.error("[SyncLease] 其他平台比率为零，导致除零错误。"); return
        
        effective_ratio = main_platform_ratio / other_platform_ratio

        difference = ECOsteamPlugin.compare_lease_shelf( # Call as static method
            shelves[main_platform], shelves[other_platform], effective_ratio, self.logger # Pass logger
        )
        self.logger.debug(f"[SyncLease] 当前被同步平台：{other_platform.upper()}\nDifference: {json.dumps(difference, cls=ModelEncoder, ensure_ascii=False)}")

        if difference.get("add") or difference.get("delete") or difference.get("change"):
            self.logger.warning(f"[SyncLease] {other_platform.upper()}平台需要更新租赁上架商品/价格")
            # ... (Rest of the logic for add, delete, change for UU and ECO, using self.uu_queue and self.eco_queue) ...
            # Ensure that the queue for the 'other_platform' is used.
            target_queue = self.uu_queue if other_platform == "uu" else self.eco_queue
            target_client = self.uu_client if other_platform == "uu" else self.client

            if not target_client or not target_queue:
                self.logger.error(f"[SyncLease] {other_platform.upper()} client或queue未初始化，无法处理差异。")
                return

            # Add items
            if difference["add"]:
                target_queue.lease_add(difference["add"])
                self.logger.info(f"[SyncLease] 已添加{len(difference['add'])}个商品到{other_platform.upper()}租赁上架队列")

            # Delete items
            if difference["delete"]:
                self.logger.info(f"[SyncLease] 即将在{other_platform.upper()}租赁货架下架{len(difference['delete'])}个商品")
                asset_ids_to_delete = [item.orderNo for item in difference["delete"]] # Assuming orderNo is the ID for deletion
                if other_platform == "uu" and self.uu_client:
                    resp = self.uu_client.off_shelf(asset_ids_to_delete).json() # Ensure this is correct method
                    if resp.get('Code') == 0: self.logger.info(f"[SyncLease] 从UU下架{len(difference['delete'])}个成功！")
                    else: self.logger.error(f"[SyncLease] 从UU下架过程中出现失败！错误信息：{resp.get('Msg')}")
                elif other_platform == "eco" and self.client:
                    # ECO needs List[GoodsNum], where GoodsNum might be {AssetId, SteamGameId}
                    # Assuming LeaseAsset has appid and assetid
                    goods_nums_to_delete = [PyECOsteam_models.GoodsNum(AssetId=item.assetid, SteamGameId=str(item.appid)) for item in difference["delete"]]
                    batches = [goods_nums_to_delete[i:i+100] for i in range(0, len(goods_nums_to_delete), 100)]
                    success_del_count = 0
                    for batch in batches:
                        try:
                            resp = self.client.OffshelfRentGoods(batch).json()
                            if resp.get('ResultCode') == '0': success_del_count += len(batch)
                            else: self.logger.error(f"[SyncLease] 从ECO下架租赁商品过程中出现失败！错误信息：{resp.get('ResultMsg')}")
                        except Exception as e: handle_caught_exception(e, self.plugin_name); self.logger.error("[SyncLease] 从ECO下架时发生未知错误")
                    self.logger.info(f"[SyncLease] 从ECO下架{success_del_count}个商品成功！")
            
            # Change items
            if difference["change"]:
                target_queue.lease_change(difference["change"])
                self.logger.info(f"[SyncLease] 已添加{len(difference['change'])}个商品到{other_platform.upper()}租赁改价队列")
        else:
            self.logger.info(f"[SyncLease] {other_platform.upper()}平台租赁货架已与主平台{main_platform.upper()}保持同步。")


    def sync_sell_shelves(self, current_steam_id: str, sell_config: dict): # Pass config
        self.logger.info("[SyncSell] 开始同步销售货架...")
        main_platform = sell_config.get("main_platform")
        enabled_platforms = sell_config.get("enabled_platforms", [])
        ratios = sell_config.get("ratio", {})
        
        if not main_platform or main_platform not in enabled_platforms:
            self.logger.error("[SyncSell] 主平台配置错误或未启用，无法同步销售货架。")
            return

        shelves = {}
        
        self.logger.info("[SyncSell] 正在从Steam获取库存信息...")
        inventory = get_cs2_inventory(self.steam_client, self.steam_client_mutex)
        if not inventory:
            self.logger.error("[SyncSell] Steam库存获取失败, 暂时跳过同步.")
            return
        self.logger.info(f"[SyncSell] Steam库存中共有{len(inventory)}个物品")

        for platform_name in enabled_platforms:
            client_attr_map = {"eco": "client", "buff": "buff_client", "uu": "uu_client"}
            if hasattr(self, client_attr_map.get(platform_name)) and getattr(self, client_attr_map.get(platform_name)):
                shelves[platform_name] = self.get_shelf(platform_name, current_steam_id, inventory)
                # Offshelf logic (simplified, assuming get_shelf returns list of orderNo for items not in inventory)
                offshelf_ids = [item_id for item_id in shelves[platform_name] if not isinstance(item_id, Asset)]
                valid_assets_on_shelf = [asset for asset in shelves[platform_name] if isinstance(asset, Asset)]
                shelves[platform_name] = valid_assets_on_shelf # Keep only valid assets for comparison

                if offshelf_ids:
                    self.logger.warning(f"[SyncSell] 检测到{platform_name.upper()}平台上架的{len(offshelf_ids)}个物品不在Steam库存中！即将下架！")
                    # ... (Offshelf logic for each platform as in original, using platform client) ...
                    if platform_name == "eco" and self.client:
                        # Assuming offshelf_ids are GoodsNum strings. This part needs careful mapping from what get_shelf returns for "to be offshelved"
                        # For simplicity, if get_shelf returns orderNo for offshelf, we need to map it correctly.
                        # This part needs robust handling of what get_shelf returns.
                        # Let's assume for now offshelf_ids are correct identifiers for ECO's OffshelfGoods
                        goods_to_offshelf_eco = [PyECOsteam_models.GoodsNum(GoodsNum=gid, SteamGameId='730') for gid in offshelf_ids]
                        s_eco, f_eco = self.client.OffshelfGoods(goods_to_offshelf_eco)
                        self.logger.info(f'[SyncSell] ECO下架成功: {s_eco}, 失败: {f_eco}')
                    elif platform_name == "buff" and self.buff_client:
                        s_buff, f_buff_dict = self.buff_client.cancel_sale(offshelf_ids) # Assumes offshelf_ids are order_ids
                        self.logger.info(f"[SyncSell] Buff下架成功: {s_buff}, 失败: {len(f_buff_dict)}")
                    elif platform_name == "uu" and self.uu_client:
                        resp_uu = self.uu_client.off_shelf(offshelf_ids).json() # Assumes offshelf_ids are order_ids
                        if resp_uu.get('Code') == 0: self.logger.info(f"[SyncSell] UU下架{len(offshelf_ids)}个成功！")
                        else: self.logger.error(f"[SyncSell] UU下架失败: {resp_uu.get('Msg')}")
                    
                    # Re-fetch shelf after off-shelving
                    if hasattr(self, client_attr_map.get(platform_name)) and getattr(self, client_attr_map.get(platform_name)):
                         shelves[platform_name] = self.get_shelf(platform_name, current_steam_id, inventory)
                         shelves[platform_name] = [asset for asset in shelves[platform_name] if isinstance(asset, Asset)]


            else:
                self.logger.warning(f"[SyncSell] {platform_name.upper()} client 未初始化, 无法获取其销售货架.")
                shelves[platform_name] = [] # Ensure key exists with empty list

        for platform_name in enabled_platforms:
            if platform_name == main_platform: continue # Don't sync main platform to itself

            if not shelves.get(main_platform) and not shelves.get(platform_name):
                self.logger.info(f"[SyncSell] {main_platform.upper()} 和 {platform_name.upper()} 货架均为空或无法获取，跳过比较。")
                continue

            self.logger.debug(f'[SyncSell] 即将比较{main_platform.upper()}平台和{platform_name.upper()}平台的上架物品')
            
            main_platform_ratio = ratios.get(main_platform, 1.0)
            other_platform_ratio = ratios.get(platform_name, 1.0)
            if other_platform_ratio == 0: self.logger.error(f"[SyncSell] {platform_name.upper()} 平台比率为零。"); continue
            
            effective_ratio = main_platform_ratio / other_platform_ratio

            difference = ECOsteamPlugin.compare_shelves( # Call as static method
                shelves[main_platform], shelves[platform_name], effective_ratio, self.logger # Pass logger
            )
            self.logger.debug(f"[SyncSell] 当前被同步平台：{platform_name.upper()}\nDifference: {json.dumps(difference, cls=ModelEncoder, ensure_ascii=False)}")

            if difference.get("add") or difference.get("delete") or difference.get("change"):
                self.logger.warning(f"[SyncSell] {platform_name.upper()}平台需要更新上架商品/价格")
                try:
                    self.solve_platform_difference(platform_name, difference, current_steam_id) # Pass current_steam_id
                except Exception as e:
                    handle_caught_exception(e, self.plugin_name)
                    self.logger.error("[SyncSell] 处理平台差异时发生未知错误")
            else:
                self.logger.info(f"[SyncSell] {platform_name.upper()}平台销售货架已与主平台{main_platform.upper()}保持同步。")


    def solve_platform_difference(self, platform_name: str, difference: dict, current_steam_id: str): # Added current_steam_id
        target_queue = None
        target_client = None

        if platform_name == "eco": target_queue, target_client = self.eco_queue, self.client
        elif platform_name == "uu": target_queue, target_client = self.uu_queue, self.uu_client
        elif platform_name == "buff": target_client = self.buff_client # Buff doesn't use queue here
        
        if not target_client and platform_name != "buff": # Buff client check is separate
             self.logger.error(f"[SolveDiff] {platform_name.upper()} client未初始化，无法处理差异。")
             return
        if not target_queue and platform_name != "buff":
             self.logger.error(f"[SolveDiff] {platform_name.upper()} queue未初始化，无法处理差异。")
             return

        # Add items
        if difference.get("add"):
            if platform_name == "buff" and self.buff_client:
                buff_assets = [BuffOnSaleAsset.from_Asset(asset) for asset in difference["add"]]
                self.logger.info(f"[SolveDiff] 即将上架{len(buff_assets)}个商品到BUFF")
                # Buff API direct call, handle batching
                for batch in [buff_assets[i:i+200] for i in range(0, len(buff_assets), 200)]:
                    s_buff, f_buff_dict = self.buff_client.on_sale(batch)
                    # Log success/failure for each assetid in f_buff_dict
                    self.logger.info(f"[SolveDiff] BUFF上架成功: {len(s_buff)}, 失败: {len(f_buff_dict)}")
                    if f_buff_dict: self.logger.error(f"[SolveDiff] BUFF上架失败详情: {f_buff_dict}")
                    if len(buff_assets)>200 and batch != buff_assets[-200:]: time.sleep(3) # Delay between large batches
            elif target_queue: # For ECO, UU
                target_queue.sell_add(difference["add"])
                self.logger.info(f"[SolveDiff] 已添加{len(difference['add'])}个商品到{platform_name.upper()}出售上架队列")
        
        # Delete items
        items_to_delete = difference.get("delete", [])
        if items_to_delete:
            asset_ids_or_orderNos_to_delete = [asset.orderNo for asset in items_to_delete] # Assuming orderNo is used
            self.logger.info(f"[SolveDiff] 即将在{platform_name.upper()}平台下架{len(asset_ids_or_orderNos_to_delete)}个商品")
            if platform_name == "eco" and self.client:
                # ECO requires List[GoodsNum(GoodsNum=orderNo, SteamGameId='730')]
                goods_nums_del = [PyECOsteam_models.GoodsNum(GoodsNum=ono, SteamGameId='730') for ono in asset_ids_or_orderNos_to_delete]
                s_eco_del, f_eco_del = self.client.OffshelfGoods(goods_nums_del)
                self.logger.info(f"[SolveDiff] ECO下架成功: {s_eco_del}, 失败: {f_eco_del}")
            elif platform_name == "buff" and self.buff_client:
                s_buff_del, f_buff_del_dict = self.buff_client.cancel_sale(asset_ids_or_orderNos_to_delete)
                self.logger.info(f"[SolveDiff] Buff下架成功: {s_buff_del}, 失败: {len(f_buff_del_dict)}")
                if f_buff_del_dict: self.logger.error(f"[SolveDiff] Buff下架失败详情: {f_buff_del_dict}")
            elif platform_name == "uu" and self.uu_client:
                resp_uu_del = self.uu_client.off_shelf(asset_ids_or_orderNos_to_delete).json()
                if resp_uu_del.get('Code') == 0: self.logger.info(f"[SolveDiff] UU下架{len(asset_ids_or_orderNos_to_delete)}个成功！")
                else: self.logger.error(f"[SolveDiff] UU下架失败: {resp_uu_del.get('Msg')}")
        
        # Change items
        items_to_change = difference.get("change", [])
        if items_to_change:
            if platform_name == "buff" and self.buff_client:
                buff_change_orders = [{"sell_order_id": asset.orderNo, "price": asset.price, "desc": ""} for asset in items_to_change]
                self.logger.info(f"[SolveDiff] 即将在BUFF平台修改{len(buff_change_orders)}个商品的价格")
                s_buff_chg, f_buff_chg_dict = self.buff_client.change_price(buff_change_orders)
                self.logger.info(f"[SolveDiff] Buff改价成功: {s_buff_chg}, 失败: {len(f_buff_chg_dict)}")
                if f_buff_chg_dict: self.logger.error(f"[SolveDiff] Buff改价失败详情: {f_buff_chg_dict}")
            elif target_queue: # For ECO, UU
                target_queue.sell_change(items_to_change)
                self.logger.info(f"[SolveDiff] 已添加{len(items_to_change)}个商品到{platform_name.upper()}出售改价队列")

```
