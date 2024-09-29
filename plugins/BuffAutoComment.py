# plugins/BuffAutoComment.py

import os
import pickle
import time
from typing import List, Dict, Any

import json5
from utils.logger import handle_caught_exception, PluginLogger
from utils.static import BUFF_COOKIES_FILE_PATH, SESSION_FOLDER, SUPPORT_GAME_TYPES
from utils.tools import get_encoding, exit_code

from BuffApi import BuffAccount


class BuffAutoComment:
    def __init__(self, logger: PluginLogger, steam_client: Any, steam_client_mutex: Any, config: Dict[str, Any]):
        self.logger = logger
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        # 初始化 BuffAccount
        buff_cookie = self._read_buff_cookie()
        self.buff_account = BuffAccount(steam_client=self.steam_client)

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
            handle_caught_exception(e, "BuffAutoComment")
            self.logger.error(f"读取 {BUFF_COOKIES_FILE_PATH} 时发生错误: {e}")
            exit_code.set(1)
            raise

    def run(self) -> None:
        """插件的主执行方法"""
        self.logger.info("BUFF自动备注插件已启动. 请稍候...")

        self.logger.info("开始执行BUFF自动备注任务...")

        sleep_interval = 60 * 60 * 2  # 2小时

        while True:
            try:
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("Steam会话已过期, 正在重新登录...")
                        self.steam_client._session.cookies.clear()
                        self.steam_client.login(
                            self.steam_client.username,
                            self.steam_client._password,
                            json5.dumps(self.steam_client.steam_guard),
                        )
                        self.logger.info("Steam会话已更新")
                        steam_session_path = os.path.join(SESSION_FOLDER, self.steam_client.username.lower() + ".pkl")
                        with open(steam_session_path, "wb") as f:
                            pickle.dump(self.steam_client.session, f)

                for game in SUPPORT_GAME_TYPES:
                    game_name = game["game"]
                    app_id = game["app_id"]

                    self.logger.info(f"正在获取{game_name} 购买记录...")
                    page_size = self.config.get("buff_auto_comment", {}).get("page_size", 300)
                    trade_history = self.buff_account.get_buy_history(game_name, page_size=page_size)
                    if not trade_history:
                        self.logger.error(f"{game_name} 无购买记录")
                        continue

                    self.logger.info("避免被封号, 休眠20秒")
                    time.sleep(20)

                    self.logger.info(f"正在获取{game_name} BUFF 库存...")
                    game_inventory = self.buff_account.get_all_buff_inventory(game=game_name)
                    if not game_inventory:
                        self.logger.error(f"{game_name} 无库存")
                        continue

                    assets = []
                    for item in game_inventory:
                        key_str = self.buff_account.form_key_str(item)
                        price = trade_history.get(key_str, '')
                        if not price:
                            self.logger.debug(f"{key_str} 无购买价格")
                            continue

                        current_comment = item.get("asset_extra", {}).get("remark", "")
                        if current_comment.startswith(price):
                            self.logger.debug(f"{key_str} 已备注, 跳过")
                            continue

                        self.logger.debug(f"{key_str} 未备注, 开始备注")
                        comment = f"{price} {current_comment}" if current_comment else str(price)
                        assets.append({
                            "assetid": item["asset_info"]["assetid"],
                            "remark": comment
                        })

                    if assets:
                        self.logger.info("避免被封号, 休眠20秒")
                        time.sleep(20)

                        self.logger.info("正在提交备注...")
                        success = self.buff_account.update_asset_remarks(app_id, assets)
                        if success:
                            self.logger.info("备注成功")
                        else:
                            self.logger.error("备注失败")
                    else:
                        self.logger.info("无需备注")
            except Exception as e:
                handle_caught_exception(e, "BuffAutoComment")
                self.logger.info(f"出现错误, 休眠{sleep_interval}秒后重试")
                time.sleep(sleep_interval)
                continue

            self.logger.info(f"休眠{sleep_interval}秒后继续执行备注任务")
            time.sleep(sleep_interval)
