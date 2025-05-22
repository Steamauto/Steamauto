import os
import pickle
import time
import requests # Added
import json5

from utils.plugin_base import PluginBase # Added
from utils.buff_helper import get_valid_session_for_buff
from utils.logger import handle_caught_exception # Fine
from utils.static import (BUFF_COOKIES_FILE_PATH, SESSION_FOLDER, SUPPORT_GAME_TYPES) # Fine
from utils.file_utils import get_encoding # Updated

class BuffAutoComment(PluginBase):
    # buff_headers is a class attribute, can remain here or be instance attribute.
    # For consistency with session, making it instance attribute in __init__.

    def __init__(self, plugin_name: str, main_logger, config: dict, steam_client, steam_client_mutex):
        super().__init__(plugin_name, main_logger, config, steam_client, steam_client_mutex)
        # self.logger, self.config, self.steam_client, self.steam_client_mutex are set by PluginBase

        self.plugin_specific_config = self.config.get(self.plugin_name, {})
        if not self.plugin_specific_config:
            self.logger.warning(f"在主配置中未找到插件 {self.plugin_name} 的配置。将使用默认值或可能出现问题。")

        self.buff_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
        }
        self.session = requests.session()
        self.logger.info(f"插件 {self.plugin_name} 已初始化。")


    def init(self) -> bool:
        self.logger.debug(f"调用插件 {self.plugin_name} 的特定init方法。")
        # get_valid_session_for_buff now needs logger. self.logger is available from PluginBase.
        if get_valid_session_for_buff(self.steam_client, self.logger) == "": # Pass self.logger
            self.logger.error("初始化期间获取有效的BUFF会话失败。")
            return True # True indicates an error
        self.logger.info("初始化期间成功获取BUFF会话。")
        return False # False indicates success

    def get_buy_history(self, game: str) -> dict:
        local_history = {}
        history_file_path = os.path.join(SESSION_FOLDER, "buy_history_" + game + ".json")
        try:
            if os.path.exists(history_file_path):
                with open(history_file_path, "r", encoding=get_encoding(history_file_path)) as f:
                    local_history = json5.load(f)
        except Exception as e:
            self.logger.debug(f"读取本地购买记录失败, 错误信息: {str(e)}", exc_info=True)
        page_num = 1
        result = {}
        while True:
            self.logger.debug(f"正在获取 {game} 购买记录, 页数: {page_num}")
            url = ("https://buff.163.com/api/market/buy_order/history?page_num=" + str(page_num) +
                   "&page_size=300&game=" + game)
            response_json = self.session.get(url, headers=self.buff_headers).json()
            if response_json["code"] != "OK":
                self.logger.error("获取历史订单失败")
                break
            items = response_json["data"]["items"]
            should_break = False
            for item in items:
                if item['state'] != 'SUCCESS':
                    continue
                keys_to_form_dict_key = ["appid", "assetid", "classid", "contextid"]
                keys_list = []
                for key_item in keys_to_form_dict_key: # Renamed 'key' to 'key_item' to avoid conflict
                    keys_list.append(str(item["asset_info"][key_item]))
                key_str = "_".join(keys_list)
                if key_str not in result:  # 使用最新的价格
                    result[key_str] = item["price"]
                if key_str in local_history and item["price"] == local_history[key_str]:
                    self.logger.info("后面没有新的订单了, 无需继续获取")
                    should_break = True
                    break
            if len(items) < 300 or should_break:
                break
            page_num += 1
            self.logger.info("避免被封号, 休眠15秒")
            time.sleep(15)
        if local_history:
            for key_str_local, price_local in local_history.items(): # Iterate key-value pairs
                if key_str_local not in result:
                    result[key_str_local] = price_local
        if result:
            with open(history_file_path, "w", encoding="utf-8") as f:
                json5.dump(result, f, indent=4)
        return result

    def check_buff_account_state(self):
        response_json = self.session.get("https://buff.163.com/account/api/user/info", headers=self.buff_headers).json()
        if response_json["code"] == "OK":
            if "data" in response_json:
                if "nickname" in response_json["data"]:
                    return response_json["data"]["nickname"]
        self.logger.error("BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试! ")
        raise TypeError("BUFF account state invalid") # Raise a more specific error to be caught

    def get_all_buff_inventory(self, game="csgo"):
        self.logger.info(f"正在获取 {game} BUFF 库存...")
        page_num = 1
        page_size = 300
        sort_by = "time.desc"
        state = "all"
        force = 0
        force_wear = 0
        url = "https://buff.163.com/api/market/steam_inventory"
        total_items = []
        while True:
            params = {
                "page_num": page_num,
                "page_size": page_size,
                "sort_by": sort_by,
                "state": state,
                "force": force,
                "force_wear": force_wear,
                "game": game
            }
            self.logger.info("避免被封号, 休眠15秒")
            time.sleep(15)
            response_json = self.session.get(url, headers=self.buff_headers, params=params).json()
            if response_json["code"] == "OK":
                items = response_json["data"]["items"]
                total_items.extend(items)
                if len(items) < page_size:
                    break
                page_num += 1
            else:
                self.logger.error(f"获取BUFF库存失败: {str(response_json)}")
                break
        return total_items

    def exec(self):
        self.logger.info("BUFF自动备注已启动, 休眠60秒, 与其他插件错开运行时间")
        time.sleep(60)
        sleep_interval = self.plugin_specific_config.get("sleep_interval_hours", 2) * 60 * 60  # Default 2 hours

        try:
            self.logger.info("正在准备登录至BUFF...")
            # Ensure BUFF_COOKIES_FILE_PATH is defined or handled if empty
            if not os.path.exists(BUFF_COOKIES_FILE_PATH):
                self.logger.error(f"BUFF cookies文件未在 {BUFF_COOKIES_FILE_PATH} 找到。无法登录BUFF。")
                return 1 # Indicate error
            
            with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
                cookie_content = f.read().replace("session=", "").replace("\n", "").split(";")[0]
                if not cookie_content:
                    self.logger.error("BUFF cookie内容为空。无法登录。")
                    return 1
                self.session.cookies["session"] = cookie_content

            self.logger.info("已检测到cookies, 尝试登录")
            self.logger.info("已经登录至BUFF 用户名: " + self.check_buff_account_state())
        except TypeError: # Catch the specific error from check_buff_account_state
            # Error already logged by check_buff_account_state
            return 1 # Indicate error
        except Exception as e:
            handle_caught_exception(e, self.plugin_name)
            self.logger.error(f"BUFF账户登录检查失败: {e} 请检查buff_cookies.txt或稍后再试! ")
            return 1


        while True:
            try:
                # Steam session check and relogin
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("Steam会话已过期, 正在重新登录...")
                        self.steam_client.relogin() # Use relogin method
                        self.logger.info("Steam会话已更新")
                        # Saving session is typically handled by SteamClient or LoginExecutor,
                        # but if direct pickling is needed, ensure username is valid for path
                        steam_session_path = os.path.join(SESSION_FOLDER, self.steam_client.username.lower() + ".pkl")
                        if self.steam_client.username: # Ensure username is available
                             with open(steam_session_path, "wb") as f:
                                 pickle.dump(self.steam_client._session, f) # Access underlying session object
                        else:
                            self.logger.warning("Steam用户名不可用，无法保存会话pickle文件。")
            except Exception as e:
                handle_caught_exception(e, self.plugin_name, known=True)
                self.logger.info(f"Steam会话检查/重新登录失败。休眠{sleep_interval}秒")
                time.sleep(sleep_interval)
                continue # Restart the main loop

            try:
                for game_info in SUPPORT_GAME_TYPES: # game_info instead of game
                    game_name = game_info["game"]
                    app_id_val = game_info["app_id"] # app_id_val to avoid conflict with app_id in post_data

                    self.logger.info(f"正在获取 {game_name} 购买记录...")
                    trade_history = self.get_buy_history(game_name)
                    if not trade_history:
                        self.logger.info(f"{game_name} 无购买记录") # Changed to info as it's not an error
                        continue
                    self.logger.info("避免被封号, 休眠20秒")
                    time.sleep(20)
                    self.logger.info(f"正在获取 {game_name} BUFF 库存...")
                    game_inventory = self.get_all_buff_inventory(game=game_name)
                    if not game_inventory:
                        self.logger.info(f"{game_name} 无库存") # Changed to info
                        continue
                    assets = []
                    for item in game_inventory:
                        keys_to_form_dict_key = ["appid", "assetid", "classid", "contextid"]
                        keys_list = []
                        for key_item in keys_to_form_dict_key:
                            keys_list.append(str(item["asset_info"][key_item]))
                        key_str = "_".join(keys_list)
                        price = ''
                        if key_str in trade_history:
                            self.logger.debug(f"{key_str} 购买价格为: {trade_history[key_str]}")
                            price = str(trade_history[key_str]) # Ensure price is string for remark
                        else:
                            self.logger.debug(f"{key_str} 无购买价格")
                            continue
                        current_comment = ""
                        if "asset_extra" in item and "remark" in item["asset_extra"]:
                            current_comment = item["asset_extra"]["remark"]
                        
                        # Check if price is already the prefix of the comment
                        if current_comment.startswith(price):
                            self.logger.debug(f"{key_str} 已备注, 跳过 (备注: '{current_comment}')")
                            continue
                            
                        self.logger.debug(f"{key_str} 未备注, 开始备注 (当前备注: '{current_comment}')")
                        comment = price if not current_comment else price + " " + current_comment
                        
                        assets.append({
                            "assetid": item["asset_info"]["assetid"],
                            "remark": comment
                        })
                    if assets:
                        post_url = "https://buff.163.com/api/market/steam_asset_remark/change"
                        post_data = {
                            "appid": app_id_val, # Use app_id_val
                            "assets": assets
                        }
                        self.logger.info("避免被封号, 休眠20秒")
                        time.sleep(20)
                        self.logger.info("正在提交备注...")
                        # Ensure session is still valid / refresh CSRF if needed
                        self.session.get("https://buff.163.com/market/?game=" + game_name, headers=self.buff_headers) # Visit page to ensure CSRF
                        csrf_token = self.session.cookies.get("csrf_token")
                        if not csrf_token:
                            self.logger.error("获取BUFF CSRF token失败, 无法提交备注.")
                            continue # Skip this game iteration

                        headers = self.buff_headers.copy()
                        headers["X-CSRFToken"] = csrf_token
                        headers["Referer"] = "https://buff.163.com/market/?game=" + game_name
                        response_json = self.session.post(post_url, headers=headers, json=post_data).json()
                        if response_json["code"] == "OK":
                            self.logger.info("备注成功")
                        else:
                            self.logger.error(f"备注失败: {response_json}")
                    else:
                        self.logger.info("无需备注")
            except Exception as e:
                handle_caught_exception(e, self.plugin_name) # Use plugin_name
            
            self.logger.info(f"休眠{sleep_interval}秒")
            time.sleep(sleep_interval)
