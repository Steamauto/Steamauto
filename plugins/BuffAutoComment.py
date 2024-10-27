import os
import pickle
import time

import json5
import requests

from utils.buff_helper import get_valid_session_for_buff
from utils.logger import handle_caught_exception
from utils.static import (BUFF_COOKIES_FILE_PATH, SESSION_FOLDER,
                          SUPPORT_GAME_TYPES)
from utils.tools import get_encoding


class BuffAutoComment:
    buff_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
    }

    def __init__(self, logger, steam_client, steam_client_mutex, config):
        self.logger = logger
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        self.session = requests.session()

    def init(self) -> bool:
        if get_valid_session_for_buff(self.steam_client, self.logger) == "":
            return True
        return False

    def get_buy_history(self, game: str) -> dict:
        local_history = {}
        history_file_path = os.path.join(SESSION_FOLDER, "buy_history_" + game + ".json")
        try:
            if os.path.exists(history_file_path):
                with open(history_file_path, "r", encoding=get_encoding(history_file_path)) as f:
                    local_history = json5.load(f)
        except Exception as e:
            self.logger.debug("[BuffAutoComment] 读取本地购买记录失败, 错误信息: " + str(e), exc_info=True)
        page_num = 1
        result = {}
        while True:
            self.logger.debug("[BuffAutoComment] 正在获取" + game + " 购买记录, 页数: " + str(page_num))
            url = ("https://buff.163.com/api/market/buy_order/history?page_num=" + str(page_num) +
                   "&page_size=300&game=" + game)
            response_json = self.session.get(url, headers=self.buff_headers).json()
            if response_json["code"] != "OK":
                self.logger.error("[BuffAutoComment] 获取历史订单失败")
                break
            items = response_json["data"]["items"]
            should_break = False
            for item in items:
                if item['state'] != 'SUCCESS':
                    continue
                keys_to_form_dict_key = ["appid", "assetid", "classid", "contextid"]
                keys_list = []
                for key in keys_to_form_dict_key:
                    keys_list.append(str(item["asset_info"][key]))
                key_str = "_".join(keys_list)
                if key_str not in result:  # 使用最新的价格
                    result[key_str] = item["price"]
                if key_str in local_history and item["price"] == local_history[key_str]:
                    self.logger.info("[BuffAutoComment] 后面没有新的订单了, 无需继续获取")
                    should_break = True
                    break
            if len(items) < 300 or should_break:
                break
            page_num += 1
            self.logger.info("[BuffAutoComment] 避免被封号, 休眠15秒")
            time.sleep(15)
        if local_history:
            for key in local_history:
                if key not in result:
                    result[key] = local_history[key]
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
        self.logger.error("[BuffAutoComment] BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试! ")
        raise TypeError

    def get_all_buff_inventory(self, game="csgo"):
        self.logger.info("[BuffAutoComment] 正在获取 " + game + " BUFF 库存...")
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
            self.logger.info("[BuffAutoComment] 避免被封号, 休眠15秒")
            time.sleep(15)
            response_json = self.session.get(url, headers=self.buff_headers, params=params).json()
            if response_json["code"] == "OK":
                items = response_json["data"]["items"]
                total_items.extend(items)
                if len(items) < page_size:
                    break
                page_num += 1
            else:
                self.logger.error(response_json)
                break
        return total_items

    def exec(self):
        self.logger.info("[BuffAutoComment] BUFF自动备注已启动, 休眠60秒, 与其他插件错开运行时间")
        time.sleep(60)
        sleep_interval = 60 * 60 * 2  # 2小时
        try:
            self.logger.info("[BuffAutoComment] 正在准备登录至BUFF...")
            with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
                self.session.cookies["session"] = f.read().replace("session=", "").replace("\n", "").split(";")[0]
            self.logger.info("[BuffAutoComment] 已检测到cookies, 尝试登录")
            self.logger.info("[BuffAutoComment] 已经登录至BUFF 用户名: " + self.check_buff_account_state())
        except TypeError as e:
            handle_caught_exception(e, known=True)
            self.logger.error("[BuffAutoComment] BUFF账户登录检查失败, 请检查buff_cookies.txt或稍后再试! ")
            return
        while True:
            try:
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("[BuffAutoComment] Steam会话已过期, 正在重新登录...")
                        self.steam_client._session.cookies.clear()
                        self.steam_client.login(
                            self.steam_client.username, self.steam_client._password, json5.dumps(self.steam_client.steam_guard)
                        )
                        self.logger.info("[BuffAutoComment] Steam会话已更新")
                        steam_session_path = os.path.join(SESSION_FOLDER, self.steam_client.username.lower() + ".pkl")
                        with open(steam_session_path, "wb") as f:
                            pickle.dump(self.steam_client.session, f)
            except Exception as e:
                handle_caught_exception(e, "[BuffAutoComment]", known=True)
                self.logger.info("[BuffAutoComment] 休眠" + str(sleep_interval) + "秒")
                time.sleep(sleep_interval)
                continue
            try:
                for game in SUPPORT_GAME_TYPES:
                    self.logger.info("[BuffAutoComment] 正在获取" + game["game"] + " 购买记录...")
                    trade_history = self.get_buy_history(game["game"])
                    if not trade_history:
                        self.logger.error("[BuffAutoComment] " + game["game"] + " 无购买记录")
                        continue
                    self.logger.info("[BuffAutoComment] 避免被封号, 休眠20秒")
                    time.sleep(20)
                    self.logger.info("[BuffAutoComment] 正在获取" + game["game"] + " BUFF 库存...")
                    game_inventory = self.get_all_buff_inventory(game=game["game"])
                    if not game_inventory:
                        self.logger.error("[BuffAutoComment] " + game["game"] + " 无库存")
                        continue
                    assets = []
                    for item in game_inventory:
                        keys_to_form_dict_key = ["appid", "assetid", "classid", "contextid"]
                        keys_list = []
                        for key in keys_to_form_dict_key:
                            keys_list.append(str(item["asset_info"][key]))
                        key_str = "_".join(keys_list)
                        price = ''
                        if key_str in trade_history:
                            self.logger.debug("[BuffAutoComment] " + key_str + " 购买价格为: " + str(trade_history[key_str]))
                            price = trade_history[key_str]
                        else:
                            self.logger.debug("[BuffAutoComment] " + key_str + " 无购买价格")
                            continue
                        current_comment = ""
                        if "asset_extra" in item and "remark" in item["asset_extra"]:
                            current_comment = item["asset_extra"]["remark"]
                        if current_comment.startswith(price):
                            self.logger.debug("[BuffAutoComment] " + key_str + " 已备注, 跳过")
                            continue
                        self.logger.debug("[BuffAutoComment] " + key_str + " 未备注, 开始备注")
                        comment = price + " " + current_comment
                        if current_comment == "":
                            comment = price
                        assets.append({
                            "assetid": item["asset_info"]["assetid"],
                            "remark": comment
                        })
                    if assets:
                        post_url = "https://buff.163.com/api/market/steam_asset_remark/change"
                        post_data = {
                            "appid": game["app_id"],
                            "assets": assets
                        }
                        self.logger.info("[BuffAutoComment] 避免被封号, 休眠20秒")
                        time.sleep(20)
                        self.logger.info("[BuffAutoComment] 正在提交备注...")
                        self.session.get("https://buff.163.com/api/market/steam_trade", headers=self.buff_headers)
                        csrf_token = self.session.cookies.get("csrf_token")
                        headers = self.buff_headers.copy()
                        headers["X-CSRFToken"] = csrf_token
                        headers["Referer"] = "https://buff.163.com/market/?game=" + game["game"]
                        response_json = self.session.post(post_url, headers=headers, json=post_data).json()
                        if response_json["code"] == "OK":
                            self.logger.info("[BuffAutoComment] 备注成功")
                        else:
                            self.logger.error("[BuffAutoComment] 备注失败")
                    else:
                        self.logger.info("[BuffAutoComment] 无需备注")
            except Exception as e:
                handle_caught_exception(e, "[BuffAutoComment]", known=True)
            self.logger.info("[BuffAutoComment] 休眠" + str(sleep_interval) + "秒")
            time.sleep(sleep_interval)
