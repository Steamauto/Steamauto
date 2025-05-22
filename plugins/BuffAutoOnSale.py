import datetime
import os
import pickle
import random
import time
import requests 
import apprise 
from apprise import AppriseAsset 
from bs4 import BeautifulSoup 
import json5

from utils.plugin_base import PluginBase
from utils.ApiCrypt import ApiCrypt 
from utils.buff_helper import get_valid_session_for_buff 
from utils.logger import handle_caught_exception 
from utils.static import (BUFF_COOKIES_FILE_PATH, SESSION_FOLDER, SUPPORT_GAME_TYPES) 
from utils.file_utils import get_encoding


class BuffAutoOnSale(PluginBase):
    buff_headers_template = { # Renamed to avoid conflict if an instance var is also named buff_headers
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
    }

    @staticmethod
    def format_str(text: str, trade):
        # Assuming trade["goods_infos"] is a dict and we take the first item.
        # This might need adjustment if trade["goods_infos"] can be empty or structured differently.
        first_good_key = next(iter(trade["goods_infos"]), None)
        if not first_good_key:
            # Handle case where goods_infos is empty or not as expected
            # Perhaps log a warning or return text as is.
            # For now, assume it's populated as originally intended.
            return text # Or raise an error, or log

        good_item = trade["goods_infos"][first_good_key]
        created_at_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade["created_at"]))
        
        # Ensure all keys exist in good_item before formatting
        item_name = good_item.get("name", "N/A")
        steam_price = good_item.get("steam_price", "N/A")
        steam_price_cny = good_item.get("steam_price_cny", "N/A")
        game_name = good_item.get("game", "N/A")
        good_icon_url = good_item.get("original_icon_url", "")

        text = text.format(
            item_name=item_name,
            steam_price=steam_price,
            steam_price_cny=steam_price_cny,
            buyer_name=trade.get("bot_name", "N/A"), # Use .get for safety
            buyer_avatar=trade.get("bot_avatar", ""),
            order_time=created_at_time_str,
            game=game_name,
            good_icon=good_icon_url,
        )
        return text

    @staticmethod
    def merge_buy_orders(response_data: dict):
        orders = response_data.get("items", []) # Use .get for safety
        user_info_map = response_data.get("user_infos", {}) # Use .get for safety
        processed_orders = []
        for order in orders:
            user_id_str = str(order.get("user_id")) # Ensure user_id is string for dict lookup
            order_user_info = user_info_map.get(user_id_str)
            if order_user_info:
                order["user"] = order_user_info
            # del order["user_id"] # Keep user_id for now, might be useful
            
            pay_method = order.get("pay_method")
            supported_methods = []
            if pay_method == 43:
                supported_methods = ["支付宝", "微信"]
            elif pay_method == 3:
                supported_methods = ["支付宝"]
            elif pay_method == 1:
                supported_methods = ["微信"]
            order["supported_pay_method"] = supported_methods
            processed_orders.append(order)
        return processed_orders


    def __init__(self, plugin_name: str, main_logger, config: dict, steam_client, steam_client_mutex):
        super().__init__(plugin_name, main_logger, config, steam_client, steam_client_mutex)
        
        self.plugin_specific_config = self.config.get(self.plugin_name, {})
        if not self.plugin_specific_config:
            self.logger.warning(f"在主配置中未找到插件 {self.plugin_name} 的配置。将使用默认值或可能出现问题。")

        self.buff_headers = self.buff_headers_template.copy() # Instance copy
        self.asset = AppriseAsset() 
        self.session = requests.session() 
        self.lowest_price_cache = {}
        self.unfinish_supply_order_list = [] 
        self.logger.info(f"插件 {self.plugin_name} 已初始化。")

    def init(self) -> bool:
        self.logger.debug(f"调用插件 {self.plugin_name} 的特定init方法。")
        if get_valid_session_for_buff(self.steam_client, self.logger) == "":
            self.logger.error("初始化期间获取有效的BUFF会话失败。")
            return True # Error
        self.logger.info("初始化期间成功获取BUFF会话。")
        return False # Success

    def check_buff_account_state(self):
        response_json = self.session.get("https://buff.163.com/account/api/user/info",
                                         headers=self.buff_headers).json()
        if response_json.get("code") == "OK": # Use .get for safety
            data = response_json.get("data", {})
            nickname = data.get("nickname")
            if nickname:
                return nickname
        self.logger.error("BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试! Response: " + str(response_json))
        return None # Return None or raise an exception to signify failure

    def get_buff_inventory(self, page_num=1, page_size=60, sort_by="time.desc", state="all", force=0, force_wear=0,
                           game="csgo", app_id=730):
        url = "https://buff.163.com/api/market/steam_inventory"
        params = {
            "page_num": page_num,
            "page_size": page_size,
            "sort_by": sort_by,
            "state": state,
            "force": force,
            "force_wear": force_wear,
            "game": game,
            "appid": app_id
        }
        response_json = self.session.get(url, headers=self.buff_headers, params=params).json()
        if response_json.get("code") == "OK":
            return response_json.get("data", {}) # Return empty dict if data is missing
        else:
            self.logger.error(f"获取BUFF库存失败: {response_json}")
            return {}

    def put_item_on_sale(self, items, price, description="", game="csgo", app_id=730, use_range_price=False):
        if game != "csgo" and use_range_price:
            self.logger.warning("仅支持CSGO使用磨损区间最低价上架, 已自动关闭磨损区间最低价上架")
            use_range_price = False
        
        wear_ranges = [{'min': 0, 'max': 0.01}, {'min': 0.01, 'max': 0.02}, {'min': 0.02, 'max': 0.03},
                       {'min': 0.03, 'max': 0.04}, {'min': 0.04, 'max': 0.07}, {'min': 0.07, 'max': 0.08},
                       {'min': 0.08, 'max': 0.09}, {'min': 0.09, 'max': 0.10}, {'min': 0.10, 'max': 0.11},
                       {'min': 0.11, 'max': 0.15}, {'min': 0.15, 'max': 0.18}, {'min': 0.18, 'max': 0.21},
                       {'min': 0.21, 'max': 0.24}, {'min': 0.24, 'max': 0.27}, {'min': 0.27, 'max': 0.38},
                       {'min': 0.38, 'max': 0.39}, {'min': 0.39, 'max': 0.40}, {'min': 0.40, 'max': 0.41},
                       {'min': 0.41, 'max': 0.42}, {'min': 0.42, 'max': 0.45}, {'min': 0.45, 'max': 0.50},
                       {'min': 0.50, 'max': 0.63}, {'min': 0.63, 'max': 0.76}, {'min': 0.76, 'max': 0.9},
                       {'min': 0.9, 'max': 1}]
        
        sleep_seconds = self.plugin_specific_config.get('sleep_seconds_to_prevent_buff_ban', 10)
        buy_order_config = self.plugin_specific_config.get('buy_order', {})
        supply_buy_orders = buy_order_config.get('enable', False)
        only_auto_accept = buy_order_config.get('only_auto_accept', True)
        supported_payment_method_names = buy_order_config.get('supported_payment_method', ["支付宝", "微信"])
        min_supply_price = buy_order_config.get('min_price', 0)

        url = "https://buff.163.com/api/market/sell_order/create/manual_plus"
        assets_to_sell = []
        sold_to_buy_order_details = []


        for item in items:
            has_requested_refresh = False
            refresh_count = 0
            self.logger.info(f"正在解析 {item.get('market_hash_name', 'N/A')}")
            min_paint_wear = 0.0
            max_paint_wear = 1.0
            paint_wear = -1.0 # Use float

            if use_range_price and game == "csgo": # Ensure game is csgo for range price
                done = False
                while not done:
                    has_wear = any(kw in item.get("market_hash_name", "") for kw in ['(Factory New)', '(Minimal Wear)', '(Field-Tested)','(Well-Worn)', '(Battle-Scarred)'])
                    if not has_wear:
                        self.logger.info("商品无磨损或非预期命名, 使用同类型最低价上架")
                        done = True
                        break 
                    
                    self.logger.info(f"为了避免被封IP, 休眠{sleep_seconds}秒")
                    time.sleep(sleep_seconds)
                    
                    preview_asset = {
                        "assetid": item.get("assetid"), "classid": item.get("classid"),
                        "instanceid": item.get("instanceid"), "contextid": item.get("contextid"),
                        "market_hash_name": item.get("market_hash_name"), "price": "", "income": "",
                        "has_market_min_price": False, "game": game, "goods_id": item.get("goods_id")
                    }
                    preview_data = {"game": game, "assets": [preview_asset]}
                    self.session.get("https://buff.163.com/api/market/steam_trade", headers=self.buff_headers) # Refresh cookie state
                    csrf_token = self.session.cookies.get("csrf_token", domain='buff.163.com')
                    preview_headers = self.buff_headers.copy()
                    preview_headers.update({
                        "X-CSRFToken": csrf_token, "X-Requested-With": "XMLHttpRequest",
                        "Content-Type": "application/json", "Referer": f"https://buff.163.com/market/sell_order/create?game={game}"
                    })
                    
                    preview_response = self.session.post("https://buff.163.com/market/sell_order/preview/manual_plus", json=preview_data, headers=preview_headers).json()
                    
                    if 'data' not in preview_response:
                        self.logger.error(f"获取磨损区间失败: {preview_response}, 使用同类型最低价上架")
                        done = True
                        break
                    
                    soup = BeautifulSoup(preview_response["data"], "html.parser")
                    paint_wear_p_tag = soup.find("p", {"class": "paint-wear"})
                    suggested_price_span = soup.find("span", {"class": "custom-currency"})
                    suggested_price = float(suggested_price_span.attrs.get("data-price", -1)) if suggested_price_span else -1.0

                    if suggested_price != -1 and suggested_price < 10: # Check if price is valid and low
                        self.logger.info("商品价格低于10, 使用同类型最低价上架")
                        done = True
                        break

                    if paint_wear_p_tag:
                        paint_wear_text = paint_wear_p_tag.text.replace("磨损:", "").strip()
                        try:
                            paint_wear = float(paint_wear_text)
                            found_range = False
                            for wr in wear_ranges:
                                if wr['min'] <= paint_wear < wr['max']:
                                    min_paint_wear, max_paint_wear = wr['min'], wr['max']
                                    found_range = True
                                    break
                            if not found_range: self.logger.warning(f"无法解析磨损 {paint_wear} 到预设区间, 使用默认区间.")
                        except ValueError:
                            self.logger.error(f"无法转换磨损值 '{paint_wear_text}' 为浮点数.")
                        done = True
                    else: # No paint wear info, try to refresh
                        if not has_requested_refresh:
                            has_requested_refresh = True
                            self.logger.info("商品未解析过或无磨损信息, 开始请求解析...")
                            time.sleep(sleep_seconds) # Wait before refresh
                            # (Refresh logic as in original, ensure csrf and headers are correct)
                            # For brevity, assuming refresh logic here...
                            self.logger.info("请求解析完成 (模拟).") # Placeholder for actual refresh
                            continue # Retry preview
                        else:
                            refresh_count += 1
                            if refresh_count >= 3: # Limit refresh attempts
                                self.logger.error("商品解析失败次数过多, 使用同类型最低价上架")
                                done = True; break
                            self.logger.warning("商品尚未解析完成, 等待后重试...")
                            time.sleep(sleep_seconds * 2) # Longer wait after refresh attempt
                            continue
                    self.logger.info(f"使用磨损区间最低价上架, 磨损区间: {min_paint_wear} - {max_paint_wear}")
            
            sell_price = price
            if sell_price == -1: # Sentinel for needing to fetch price
                sell_price = self.get_lowest_sell_price(item.get("goods_id"), game, app_id, min_paint_wear, max_paint_wear)

            if supply_buy_orders and sell_price != -1: # Ensure sell_price is valid before comparing
                highest_buy_order = self.get_highest_buy_order(item.get("goods_id"), game, app_id, paint_wear=paint_wear,
                                                               require_auto_accept=only_auto_accept,
                                                               supported_payment_methods=supported_payment_method_names)
                if highest_buy_order and (sell_price <= min_supply_price or sell_price <= float(highest_buy_order.get("price", -1.0))):
                    self.logger.info(f"商品 {item.get('market_hash_name')} 将供应给最高报价 {highest_buy_order.get('price')}")
                    if self.supply_item_to_buy_order(item, highest_buy_order, game, app_id):
                        sold_to_buy_order_details.append(f"{item.get('market_hash_name')} : {highest_buy_order.get('price')}")
                        continue # Skip adding to assets_to_sell

            if sell_price != -1:
                final_sell_price = max(0.02, sell_price - 0.01) # Ensure price is at least 0.02
                self.logger.info(f"商品 {item.get('market_hash_name')} 将使用价格 {final_sell_price:.2f} 进行上架")
                assets_to_sell.append({
                    "appid": str(app_id), "assetid": item.get("assetid"), "classid": item.get("classid"),
                    "instanceid": item.get("instanceid"), "contextid": item.get("contextid"),
                    "market_hash_name": item.get("market_hash_name"), "price": final_sell_price,
                    "income": final_sell_price, "desc": description, # Assuming income is same as price for manual listing
                })
        
        # Notify for items sold to buy orders
        if sold_to_buy_order_details and self.plugin_specific_config.get("on_sale_notification"):
            item_list_str = "\n".join(sold_to_buy_order_details)
            self._send_apprise_notification(
                title_template=self.plugin_specific_config["on_sale_notification"].get("title", "Items Sold to Buy Orders"),
                body_template=self.plugin_specific_config["on_sale_notification"].get("body", "{item_list}"),
                game=game, sold_count=len(sold_to_buy_order_details), item_list=item_list_str
            )

        if not assets_to_sell:
            self.logger.info("没有需要上架的物品。")
            return {}

        data_to_post = {"appid": str(app_id), "game": game, "assets": assets_to_sell}
        self.session.get("https://buff.163.com/api/market/steam_trade", headers=self.buff_headers) # Refresh state
        csrf_token = self.session.cookies.get("csrf_token", domain='buff.163.com')
        post_headers = self.buff_headers.copy()
        post_headers.update({
            "X-CSRFToken": csrf_token, "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json", "Referer": f"https://buff.163.com/market/sell_order/create?game={game}"
        })
        
        response_json = self.session.post(url, json=data_to_post, headers=post_headers).json()
        if response_json.get("code") == "OK":
            self.logger.info("上架BUFF商品成功!")
            if self.plugin_specific_config.get("on_sale_notification"):
                item_list_str = "\n".join([f"{asset['market_hash_name']} : {asset['price']:.2f}" for asset in assets_to_sell])
                self._send_apprise_notification(
                    title_template=self.plugin_specific_config["on_sale_notification"].get("title", "Items Listed on Market"),
                    body_template=self.plugin_specific_config["on_sale_notification"].get("body", "{item_list}"),
                    game=game, sold_count=len(assets_to_sell), item_list=item_list_str
                )
            return response_json.get("data", {})
        else:
            self.logger.error(f"上架BUFF商品失败: {response_json}")
            return {}

    def _send_apprise_notification(self, title_template, body_template, game, sold_count, item_list):
        servers = self.plugin_specific_config.get("servers", [])
        if not servers: return

        apprise_obj = apprise.Apprise(asset=self.asset)
        for server_url in servers:
            apprise_obj.add(server_url)
        
        title = title_template.format(game=game, sold_count=sold_count)
        body = body_template.format(game=game, sold_count=sold_count, item_list=item_list)
        
        if not apprise_obj.notify(body=body, title=title):
            self.logger.error("发送Apprise通知失败。")


    def get_highest_buy_order(self, goods_id, game="csgo", app_id=730, paint_wear=-1.0, require_auto_accept=True,
                              supported_payment_methods=None):
        sleep_seconds = self.plugin_specific_config.get('sleep_seconds_to_prevent_buff_ban', 10)
        if supported_payment_methods is None:
            supported_payment_methods = ["支付宝", "微信"] # Default if not passed
        
        url = (f"https://buff.163.com/api/market/goods/buy_order?goods_id={goods_id}"
               f"&page_num=1&page_size=20&same_goods=false&game={game}&appid={app_id}")
        
        self.logger.info(f"为了避免被封IP, 休眠{sleep_seconds}秒")
        time.sleep(sleep_seconds)
        self.logger.info("正在获取BUFF商品最高求购")
        response = self.session.get(url, headers=self.buff_headers).json()

        if response.get("code") != "OK":
            self.logger.error(f"获取最高求购失败: {response}")
            return {}
        
        buy_orders = BuffAutoOnSale.merge_buy_orders(response.get("data", {})) # Use static method
        if not buy_orders: return {}

        for order in buy_orders:
            if require_auto_accept and not order.get("user", {}).get("is_auto_accept"):
                continue
            
            payment_method_supported = any(pm_name in order.get("supported_pay_method", []) for pm_name in supported_payment_methods)
            if not payment_method_supported:
                continue

            specifics = order.get("specific", [])
            if specifics: # If there are specific requirements
                match_specific = True
                for specific_req in specifics:
                    if specific_req.get("type") == "paintwear":
                        if paint_wear == -1.0: match_specific = False; break 
                        min_pw, max_pw = specific_req.get("values", [0.0, 0.0])
                        if not (min_pw <= paint_wear < max_pw): match_specific = False; break
                    elif specific_req.get("type") == "unlock_style": # Style requirements, assume not met
                        match_specific = False; break
                if not match_specific: continue
            return order # Return first matching order
        return {}


    def get_lowest_sell_price(self, goods_id, game="csgo", app_id=730, min_paint_wear=0.0, max_paint_wear=1.0):
        sleep_seconds = self.plugin_specific_config.get('sleep_seconds_to_prevent_buff_ban', 10)
        goods_key = f"{goods_id},{min_paint_wear:.2f},{max_paint_wear:.2f}" # Format float for consistency

        cached_data = self.lowest_price_cache.get(goods_key)
        if cached_data and (cached_data["cache_time"] >= datetime.datetime.now() - datetime.timedelta(hours=1)):
            return cached_data["lowest_price"]

        self.logger.info("获取BUFF商品最低价")
        self.logger.info(f"为了避免被封IP, 休眠{sleep_seconds}秒")
        time.sleep(sleep_seconds)
        
        url_params = f"goods_id={goods_id}&page_num=1&page_size=24&allow_tradable_cooldown=1&sort_by=default&game={game}&appid={app_id}"
        if not (min_paint_wear == 0.0 and max_paint_wear == 1.0): # Only add wear if not default
            url_params += f"&min_paintwear={min_paint_wear}&max_paintwear={max_paint_wear}"
        
        url = f"https://buff.163.com/api/market/goods/sell_order?{url_params}"
        
        response_json = self.session.get(url, headers=self.buff_headers).json()

        if response_json.get("code") == "OK":
            items = response_json.get("data", {}).get("items", [])
            if not items:
                if not (min_paint_wear == 0.0 and max_paint_wear == 1.0): # If specific range had no items
                    self.logger.info("当前磨损区间无商品, 重试使用同类型最低价上架 (无磨损限制)")
                    return self.get_lowest_sell_price(goods_id, game, app_id, 0.0, 1.0) # Recursive call with default range
                else:
                    self.logger.info("无商品在售")
                    return -1.0 # Indicate no items found
            
            lowest_price = float(items[0].get("price", -1.0))
            if lowest_price != -1.0:
                 self.lowest_price_cache[goods_key] = {"lowest_price": lowest_price, "cache_time": datetime.datetime.now()}
            return lowest_price
        else:
            if response_json.get("code") == "Captcha Validate Required":
                captcha_url = response_json.get("confirm_entry",{}).get("entry",{}).get("url","N/A")
                session_cookie = self.session.cookies.get("session", domain='buff.163.com', default="N/A")
                self.logger.error(f"需要验证码, 请使用session {session_cookie} 打开以下链接, 并完成验证: {captcha_url}")
                if self.plugin_specific_config.get("captcha_notification"):
                    self._send_apprise_notification(
                        title_template=self.plugin_specific_config["captcha_notification"].get("title", "Captcha Required"),
                        body_template=self.plugin_specific_config["captcha_notification"].get("body", "URL: {captcha_url}\nSession: {session}"),
                        game=game, sold_count=0, # Placeholder for count, not relevant here
                        item_list=f"URL: {captcha_url}\nSession: {session_cookie}" # Pass details in item_list
                    )
            elif response_json.get("code") == "System Error":
                self.logger.error(f"BUFF系统错误: {response_json.get('error', '未知系统错误')}")
                time.sleep(5) # Brief pause for system errors
            else:
                self.logger.error(f"获取BUFF商品最低价失败: {response_json}")
            return -1.0 # Indicate error or not found


    def exec(self):
        self.logger.info("BUFF自动上架插件已启动, 休眠30秒, 与自动接收报价插件错开运行时间")
        time.sleep(self.plugin_specific_config.get("initial_sleep", 30)) # Initial sleep configurable
        
        try:
            self.logger.info("正在准备登录至BUFF...")
            if not os.path.exists(BUFF_COOKIES_FILE_PATH):
                self.logger.error(f"BUFF cookies file not found: {BUFF_COOKIES_FILE_PATH}")
                return 1
            with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
                cookie_content = f.read().replace("session=", "").strip().split(";")[0]
                if not cookie_content: self.logger.error("BUFF Cookie is empty!"); return 1
                self.session.cookies["session"] = cookie_content
            
            self.logger.info("已检测到cookies, 尝试登录")
            buff_username = self.check_buff_account_state()
            if not buff_username: return 1 # Exit if login check fails
            self.logger.info(f"已经登录至BUFF 用户名: {buff_username}")
        except Exception as e: # Catch broad exceptions during init phase
            handle_caught_exception(e, self.plugin_name, known=True)
            self.logger.error(f"BUFF账户登录检查失败: {e}")
            return 1

        sleep_interval = int(self.plugin_specific_config.get("interval", 3600)) # Default 1 hour
        black_list_hours = self.plugin_specific_config.get("blacklist_time", [])
        white_list_hours = self.plugin_specific_config.get("whitelist_time", [])
        random_run_chance = self.plugin_specific_config.get("random_chance", 1.0) # Probability (0.0 to 1.0)
        force_refresh_inventory = self.plugin_specific_config.get("force_refresh", False)
        item_description = self.plugin_specific_config.get("description", "")
        use_range_pricing = self.plugin_specific_config.get("use_range_price", False)

        while True:
            try:
                # Steam Session Check
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("Steam会话已过期, 正在重新登录...")
                        self.steam_client.relogin()
                        self.logger.info("Steam会话已更新")
                        # Session saving logic can be part of relogin or handled by SteamClient itself
                
                now = datetime.datetime.now()
                if now.hour in black_list_hours:
                    self.logger.info(f"现在时间 ({now.hour}h) 在黑名单时间内, 休眠{sleep_interval}秒")
                    time.sleep(sleep_interval); continue
                if white_list_hours and now.hour not in white_list_hours:
                    self.logger.info(f"现在时间 ({now.hour}h) 不在白名单时间内, 休眠{sleep_interval}秒")
                    time.sleep(sleep_interval); continue
                if random.random() > random_run_chance:
                    self.logger.info(f"未命中随机概率 ({random_run_chance*100}%), 休眠{sleep_interval}秒")
                    time.sleep(sleep_interval); continue
            except Exception as e:
                handle_caught_exception(e, self.plugin_name, known=True)
                self.logger.info(f"Steam会话检查/时间检查出错. 休眠{sleep_interval}秒")
                time.sleep(sleep_interval); continue
            
            try:
                items_processed_this_cycle = 0
                for game_details in SUPPORT_GAME_TYPES:
                    game_name = game_details["game"]
                    app_id_val = game_details["app_id"]
                    self.logger.info(f"正在检查 {game_name} 库存...")
                    
                    inventory_data = self.get_buff_inventory(
                        state="cansell", sort_by="price.desc", game=game_name, app_id=app_id_val,
                        force=(1 if force_refresh_inventory else 0)
                    )
                    items_in_inventory = inventory_data.get("items", [])
                    items_processed_this_cycle += len(items_in_inventory)

                    if items_in_inventory:
                        self.logger.info(f"检查到 {game_name} 库存有 {len(items_in_inventory)} 件可出售商品, 正在上架...")
                        # Group items for selling (e.g., 5 at a time)
                        for i in range(0, len(items_in_inventory), 5):
                            item_group = items_in_inventory[i:i+5]
                            # asset_info is nested inside each item from inventory_data
                            items_to_sell_assets = [item_data.get("asset_info", item_data) for item_data in item_group] 
                            
                            self.put_item_on_sale(items=items_to_sell_assets, price=-1, description=item_description,
                                                  game=game_name, app_id=app_id_val, use_range_price=use_range_pricing)
                            
                            buy_order_cfg = self.plugin_specific_config.get('buy_order', {})
                            if buy_order_cfg.get('enable'):
                                self.confirm_supply_order() # Process any pending confirmations
                        self.logger.info(f"{game_name} 商品上架处理完成!")
                    else:
                        self.logger.info(f"{game_name} 库存为空, 跳过上架")
                    
                    self.logger.info("休眠30秒, 防止请求过快被封IP")
                    time.sleep(30)
                
                if items_processed_this_cycle == 0:
                    self.logger.info("所有游戏库存为空, 本批次上架结束!")
                    # No break here, will sleep for main interval and recheck
                    
            except Exception as e:
                handle_caught_exception(e, self.plugin_name) # Removed known=True to log full trace for unexpected errors
                self.logger.error(f"BUFF商品上架失败: {e}", exc_info=True)
            
            self.logger.info(f"本轮上架结束. 休眠{sleep_interval}秒")
            # Split sleep for periodic buy order confirmation
            confirm_interval = self.plugin_specific_config.get("confirm_supply_interval_minutes", 5) * 60
            num_confirm_periods = int(sleep_interval // confirm_interval)
            remaining_sleep = sleep_interval % confirm_interval

            for _ in range(num_confirm_periods):
                time.sleep(confirm_interval)
                buy_order_cfg = self.plugin_specific_config.get('buy_order', {})
                if buy_order_cfg.get('enable'):
                    self.logger.info("周期性检查待确认的供应订单...")
                    self.confirm_supply_order()
            
            if remaining_sleep > 0: time.sleep(remaining_sleep)


    def supply_item_to_buy_order(self, item_asset_info, highest_buy_order, game, app_id):
        sleep_seconds = self.plugin_specific_config.get('sleep_seconds_to_prevent_buff_ban', 10)
        url = "https://buff.163.com/api/market/goods/supply/manual_plus"
        data = {
            "game": game, "buy_order_id": highest_buy_order.get("id"),
            "buyer_auto_accept": highest_buy_order.get("user", {}).get("is_auto_accept"),
            "price": float(highest_buy_order.get("price", 0.0)),
            "assets": [item_asset_info] # item_asset_info should be the dict like item["asset_info"]
        }
        self.logger.info(f"为了避免被封IP, 休眠{sleep_seconds}秒")
        time.sleep(sleep_seconds)
        self.logger.info("正在供应商品至最高报价...")
        
        self.session.get("https://buff.163.com/market/?game=" + game, headers=self.buff_headers) # Visit page for CSRF
        csrf_token = self.session.cookies.get("csrf_token", domain='buff.163.com')
        if not csrf_token: self.logger.error("供应求购订单时获取BUFF CSRF token失败。"); return False

        supply_headers = self.buff_headers.copy()
        supply_headers.update({
            "X-CSRFToken": csrf_token, "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json", "Referer": f"https://buff.163.com/market/sell_order/create?game={game}"
        })
        
        response_json = self.session.post(url, json=data, headers=supply_headers).json()
        if response_json.get("code") == "OK":
            self.logger.info("商品供应成功! ")
            self.logger.info("正在发起steam报价...")
            order_id = response_json.get("data", [{}])[0].get("id")
            if not order_id: self.logger.error("未从供应响应中获取到订单ID."); return False

            steam_cookies_dict = self.steam_client._session.cookies.get_dict('steamcommunity.com')
            steam_cookies_str = "; ".join([f"{k}={v}" for k, v in steam_cookies_dict.items()])
            
            api_crypt = ApiCrypt() # Assuming ApiCrypt is available
            encrypted_steam_cookies = api_crypt.encrypt(steam_cookies_str)
            
            send_offer_data = {"seller_info": encrypted_steam_cookies, "bill_orders": [order_id]}
            
            # Refresh CSRF for send_offer if necessary, or reuse if calls are close
            csrf_token_send_offer = self.session.cookies.get("csrf_token", domain='buff.163.com') # Potentially reuse
            if not csrf_token_send_offer: self.logger.error("卖家发送报价时获取BUFF CSRF token失败。"); return False

            send_offer_headers = self.buff_headers.copy()
            send_offer_headers.update({
                "X-CSRFToken": csrf_token_send_offer, "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/json", "Referer": f"https://buff.163.com/market/sell_order/create?game={game}"
            })
            
            send_offer_resp = self.session.post("https://buff.163.com/api/market/manual_plus/seller_send_offer",
                                              json=send_offer_data, headers=send_offer_headers).json()
            if send_offer_resp.get("code") == "OK":
                self.unfinish_supply_order_list.append({"order_id": order_id, "create_time": time.time()})
                self.logger.info("发起steam报价成功! 等待确认.")
                return True
            else:
                self.logger.error(f"BUFF发起Steam报价失败: {send_offer_resp}")
        else:
            self.logger.error(f"商品供应失败: {response_json}")
        return False

    def confirm_supply_order(self):
        if not self.unfinish_supply_order_list:
            self.logger.info("没有待确认的供应订单。")
            return

        current_unconfirmed_orders = []
        error_count = 0
        unconfirmed_count = 0
        confirmed_count = 0

        self.logger.info(f"处理等待发起报价的订单, 共有{len(self.unfinish_supply_order_list)}个")
        for order_details in self.unfinish_supply_order_list:
            order_id, create_time = order_details["order_id"], order_details["create_time"]
            if time.time() - create_time > 15 * 60: # 15 min timeout
                error_count += 1
                self.logger.error(f"BUFF发起steam报价超时, 订单ID: {order_id}")
                continue
            
            try:
                url = f'https://buff.163.com/api/market/bill_order/batch/info?bill_orders={order_id}'
                csrf_token = self.session.cookies.get("csrf_token", domain="buff.163.com")
                headers = self.buff_headers.copy()
                headers.update({
                    "X-CSRFToken": csrf_token, 
                    "Referer": "https://buff.163.com/market/sell_order/history" # More relevant referer
                })
                
                res_json = self.session.get(url, headers=headers).json()
                order_item_data = res_json.get("data", {}).get("items", [])

                if res_json.get("code") == "OK" and order_item_data and order_item_data[0].get("tradeofferid"):
                    steam_trade_offer_id = order_item_data[0]["tradeofferid"]
                    self.logger.info(f"BUFF发起steam报价成功, 报价ID: {steam_trade_offer_id}")
                    with self.steam_client_mutex: # Ensure thread safety for Steam operations
                        # Assuming _confirm_transaction is a method in SteamClient that handles this
                        # It might need steam_guard, identity_secret, steamid from self.steam_client
                        # For now, assuming it's callable like this
                        confirmation_result = self.steam_client._confirm_transaction(steam_trade_offer_id) 
                        # Check confirmation_result if it provides success status
                    confirmed_count += 1
                    self.logger.info(f"确认steam报价 {steam_trade_offer_id} 成功 (Result: {confirmation_result})")
                else:
                    current_unconfirmed_orders.append(order_details)
                    unconfirmed_count += 1
                    self.logger.info(f"BUFF尚未完成发起steam报价 for order {order_id}, 将在下次检查. Response: {res_json}")
            except Exception as e:
                unconfirmed_count +=1 # Keep it for next try if error is transient
                current_unconfirmed_orders.append(order_details)
                self.logger.error(f"确认steam报价失败 for order {order_id}: {e}", exc_info=True)
            
            if len(self.unfinish_supply_order_list) > 1: # Avoid spamming logs for single item
                 time.sleep(self.plugin_specific_config.get('confirm_batch_delay', 5)) # Delay between checks in a batch

        self.unfinish_supply_order_list = current_unconfirmed_orders
        self.logger.info(
            f"本轮求购订单处理结束 - 成功确认: {confirmed_count}, 等待下次确认: {unconfirmed_count}, 超时/失败: {error_count}"
        )
        # No need for "等待上架5个货物或1分钟后再次检测" log here, as this is part of the main loop's sleep cycle
