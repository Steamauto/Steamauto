# plugins/BuffAutoOnSale.py

import datetime
import os
import pickle
import random
import time
from typing import Any, Dict, List

import apprise
import json5
import requests
from apprise import AppriseAsset

from utils.logger import handle_caught_exception, PluginLogger
from utils.static import (
    APPRISE_ASSET_FOLDER,
    BUFF_COOKIES_FILE_PATH,
    SESSION_FOLDER,
    SUPPORT_GAME_TYPES
)
from utils.tools import get_encoding, exit_code

from BuffApi import BuffAccount


class BuffAutoOnSale:
    def __init__(self, logger: PluginLogger, steam_client: Any, steam_client_mutex: Any, config: Dict[str, Any]):
        self.logger = logger
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config

        # 初始化 BuffAccount
        buff_cookie = self._read_buff_cookie()
        self.buff_account = BuffAccount(steam_client, buff_cookie)

    def _read_buff_cookie(self) -> str:
        """读取BUFF的cookie"""
        try:
            with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
                buff_cookie = f.read().strip()
                self.logger.info("已检测到BUFF cookies")
                return buff_cookie
        except FileNotFoundError:
            self.logger.error(f"未找到 {BUFF_COOKIES_FILE_PATH}, 请检查文件路径!")
            exit_code.set(1)
            raise
        except Exception as e:
            handle_caught_exception(e, "BuffAutoOnSale")
            self.logger.error(f"读取 {BUFF_COOKIES_FILE_PATH} 时发生错误: {e}")
            exit_code.set(1)
            raise

    def run(self) -> None:
        """插件的主执行方法"""

        self.logger.info("BUFF自动上架插件已启动. 请稍候...")
        self.logger.info("开始执行BUFF自动上架任务...")

        sleep_interval = int(self.config["buff_auto_on_sale"].get("interval", 7200))  # 默认2小时

        black_list_time = self.config["buff_auto_on_sale"].get("blacklist_time", [])
        white_list_time = self.config["buff_auto_on_sale"].get("whitelist_time", [])
        random_chance = self.config["buff_auto_on_sale"].get("random_chance", 1.0) * 100  # 转换为百分比
        force_refresh = self.config["buff_auto_on_sale"].get("force_refresh", False)
        description = self.config["buff_auto_on_sale"].get("description", "")
        use_range_price = self.config["buff_auto_on_sale"].get("use_range_price", False)

        while True:
            try:
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("[BuffAutoOnSale] Steam会话已过期, 正在重新登录...")
                        self.steam_client._session.cookies.clear()
                        self.steam_client.login(
                            self.steam_client.username,
                            self.steam_client._password,
                            json5.dumps(self.steam_client.steam_guard)
                        )
                        self.logger.info("[BuffAutoOnSale] Steam会话已更新")
                        steam_session_path = os.path.join(SESSION_FOLDER, self.steam_client.username.lower() + ".pkl")
                        with open(steam_session_path, "wb") as f:
                            pickle.dump(self.steam_client.session, f)

                now = datetime.datetime.now()
                if now.hour in black_list_time:
                    self.logger.info(f"[BuffAutoOnSale] 当前时间在黑名单时间内, 休眠{sleep_interval}秒")
                    time.sleep(sleep_interval)
                    continue
                if white_list_time and now.hour not in white_list_time:
                    self.logger.info(f"[BuffAutoOnSale] 当前时间不在白名单时间内, 休眠{sleep_interval}秒")
                    time.sleep(sleep_interval)
                    continue
                if random.randint(1, 100) > random_chance:
                    self.logger.info(f"[BuffAutoOnSale] 未命中随机概率, 休眠{sleep_interval}秒")
                    time.sleep(sleep_interval)
                    continue
            except Exception as e:
                handle_caught_exception(e, "BuffAutoOnSale")
                self.logger.info(f"[BuffAutoOnSale] 休眠{sleep_interval}秒后重试")
                time.sleep(sleep_interval)
                continue

            try:
                while True:
                    items_count_this_loop = 0
                    for game in SUPPORT_GAME_TYPES:
                        game_name = game["game"]
                        app_id = game["app_id"]
                        self.logger.info(f"[BuffAutoOnSale] 正在检查 {game_name} 库存...")
                        inventory = self.buff_account.get_buff_inventory(
                            state="cansell",
                            sort_by="price.desc",
                            game=game_name,
                            app_id=app_id,
                            force_refresh=force_refresh
                        )
                        items = inventory.get("items", [])
                        items_count_this_loop += len(items)
                        if items:
                            self.logger.info(f"[BuffAutoOnSale] 检查到 {game_name} 库存有 {len(items)} 件可出售商品, 正在上架...")
                            assets = self.buff_account.prepare_assets_for_sale(
                                items=items,
                                game=game_name,
                                app_id=app_id,
                                description=description,
                                use_range_price=use_range_price
                            )
                            if assets:
                                self.buff_account.put_item_on_sale(assets=assets, game=game_name, app_id=app_id)
                            self.logger.info("[BuffAutoOnSale] BUFF商品上架成功! ")
                        else:
                            self.logger.info(f"[BuffAutoOnSale] 检查到 {game_name} 库存为空, 跳过上架")
                        self.logger.info("[BuffAutoOnSale] 休眠30秒, 防止请求过快被封IP")
                        time.sleep(30)
                    if items_count_this_loop == 0:
                        self.logger.info("[BuffAutoOnSale] 库存为空, 本批次上架结束!")
                        break
            except Exception as e:
                handle_caught_exception(e, "BuffAutoOnSale")
                self.logger.error(f"[BuffAutoOnSale] BUFF商品上架失败, 错误信息: {e}", exc_info=True)

            self.logger.info(f"[BuffAutoOnSale] 休眠{sleep_interval}秒后继续执行上架任务")
            time.sleep(sleep_interval)
