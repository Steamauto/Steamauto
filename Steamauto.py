import importlib
import importlib.util
import inspect
import os
import re
import shutil
import signal
import sys
import threading
import time
from typing import no_type_check

import json5
from colorama import Fore, Style

import utils.static as static
from utils.config_manager import ConfigManager
from utils.plugin_manager import PluginManager
from steampy.client import SteamClient
from utils.code_updater import attempt_auto_update_github
from utils.logger import handle_caught_exception, logger
from utils.notifier import send_notification
from utils.static import (
    BUILD_INFO,
    CONFIG_FILE_PATH,
    CONFIG_FOLDER,
    CURRENT_VERSION,
    DEFAULT_CONFIG_JSON,
    DEFAULT_STEAM_ACCOUNT_JSON,
    INTERNAL_PLUGINS,
    PLUGIN_FOLDER,
    SESSION_FOLDER,
    STEAM_ACCOUNT_INFO_FILE_PATH,
)
from utils.old_version_patches import patch
from utils.steam_client import login_to_steam, steam_client_mutex
from utils.tools import exit_code, jobHandler, pause # calculate_sha256, get_encoding removed
from utils.file_utils import calculate_sha256, get_encoding # Added

config_manager = ConfigManager() # Global instance, used by Application

# Application class encapsulates the main program flow
class Application:
    def __init__(self):
        self.config_manager = config_manager # Use the global config_manager
        self.logger = logger             # Use the global logger
        self.steam_client_mutex = steam_client_mutex # Use the global steam_client_mutex from utils.steam_client
        
        self.steam_client = None
        self.plugin_manager = None
        self.plugins_enabled = []
        
        self._tried_exit = False # For exit signal handling

    def _handle_global_exception(self, exc_type, exc_value, exc_traceback):
        self.logger.exception(
            "程序发生致命错误，请将此界面截图，并提交最新的log文件到https://github.com/jiajiaxd/Steamauto/issues",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        self.logger.error("由于出现致命错误，程序即将退出...")
        pause() # Global pause function

    def _handle_exit_signal(self, signal_num=None, frame=None):
        if not self._tried_exit:
            self._tried_exit = True
            self.logger.warning("正在退出...若无响应，请再按一次Ctrl+C或者直接关闭窗口")
            jobHandler.terminate_all() # Global jobHandler
            # Ensure exit_code is set before exiting. If not set, default to 0 or 1.
            current_exit_code = exit_code.get()
            if current_exit_code is None: # Should ideally always be set
                 self.logger.warning("退出代码未设置，默认为 0。")
                 current_exit_code = 0
            os._exit(current_exit_code)
        else:
            self.logger.warning("程序已经强制退出")
            pid = os.getpid()
            os.kill(pid, signal.SIGTERM) # Using SIGTERM for a more graceful forced exit if possible

    def _setup_system_handlers(self):
        sys.excepthook = self._handle_global_exception
        signal.signal(signal.SIGINT, self._handle_exit_signal)
        if not os.path.exists(SESSION_FOLDER): # SESSION_FOLDER from utils.static
            try:
                os.mkdir(SESSION_FOLDER)
            except Exception as e:
                self.logger.error(f"创建会话文件夹 {SESSION_FOLDER} 失败: {e}")
                # This might be critical, consider how to handle. For now, just log.

    def _initialize_app_basics(self) -> bool:
        patch() # from utils.old_version_patches
        self.logger.info("欢迎使用Steamauto Github仓库:https://github.com/Steamauto/Steamauto")
        self.logger.info("欢迎加入Steamauto 官方QQ群 群号: 425721057")
        self.logger.info("若您觉得Steamauto好用, 请给予Star支持, 谢谢! \n")
        self.logger.info(f"{Fore.RED+Style.BRIGHT}！！！ 本程序完全{Fore.YELLOW}免费开源 {Fore.RED}若有人向你售卖，请立即投诉并申请退款 ！！！ \n")
        self.logger.info(f"当前版本: {CURRENT_VERSION}   编译信息: {BUILD_INFO}")
        
        if not self.config_manager.get_all_config(): # Checks if config loaded, even defaults
            self.logger.error("无法加载任何配置，甚至无法加载默认配置。请检查日志以获取详细信息。")
            return False # Critical error

        if self.config_manager.get("source_code_auto_update", False):
            if not hasattr(sys, '_MEIPASS'): # _MEIPASS indicates a PyInstaller bundle
                attempt_auto_update_github(CURRENT_VERSION) # Global function
        else:
            try:
                from utils import cloud_service # Cloud service check
                cloud_service.checkVersion()
                cloud_service.getAds()
            except Exception as e:
                self.logger.warning(f'无法使用云服务: {e}')
            
        self.logger.info("正在初始化...")
        if self.config_manager.get("no_pause") is not None:
            static.no_pause = self.config_manager.get("no_pause") # static from utils.static
        
        return True # Success

    def _login_to_steam(self) -> bool:
        # login_to_steam is a global function from utils.steam_client
        self.steam_client = login_to_steam(self.config_manager.get_all_config(), self.steam_client_mutex)
        if self.steam_client is None:
            send_notification('登录Steam失败，程序停止运行') # send_notification from utils.notifier
            return False
        return True

    def _initialize_plugins(self) -> int: # Returns original plugins_check_status codes (0:error, 1:success, 2:no_plugins)
        # PluginManager is a global import
        self.plugin_manager = PluginManager(
            config_manager=self.config_manager,
            logger=self.logger,
            steam_client=self.steam_client,
            steam_client_mutex=self.steam_client_mutex
        )
        self.plugins_enabled = self.plugin_manager.get_enabled_plugins()
        return self.plugin_manager.check_plugins_initialization(self.plugins_enabled)

    def _start_plugins(self):
        self.logger.info("初始化完成, 开始运行插件!")
        print("\n") # For spacing, as in original
        time.sleep(0.1)

        if not self.plugins_enabled:
            # This case should ideally be caught by plugins_check_status in run()
            self.logger.error("没有启用的插件可以启动。")
            exit_code.set(1) # General error
            return

        if len(self.plugins_enabled) == 1:
            plugin_return_code = self.plugins_enabled[0].exec()
            exit_code.set(plugin_return_code if plugin_return_code is not None else 0)
        else:
            threads = []
            for plugin in self.plugins_enabled:
                # Assuming plugin.exec is designed to be thread-safe and sets its own exit conditions if necessary
                threads.append(threading.Thread(target=plugin.exec))
            for thread in threads:
                thread.daemon = True 
                thread.start()
            for thread in threads:
                thread.join() 
            # For multiple plugins, exit_code might need a strategy.
            # Original code sets exit_code if any plugin sets it.
            # Here, we might rely on plugins to set a global error flag or sum return codes.
            # The original code had `if exit_code.get() != 0: logger.warning(...)`
            # This implies plugins might set the exit_code directly. We'll keep that behavior.
            # If no plugin sets exit_code, it remains as its last value (potentially 0).
        
        if exit_code.get() != 0:
             self.logger.warning("所有插件都已经退出！这不是一个正常情况，请检查配置文件！")
        else:
            # If exit_code is still 0 (or default), means plugins completed without error signals through exit_code
            self.logger.info("所有插件执行完毕。")


    def run(self):
        self._setup_system_handlers()

        if not self._initialize_app_basics():
            pause()
            exit_code.set(1) 
            self._handle_exit_signal() 
            return # Effectively returns via os._exit

        if not self._login_to_steam():
            pause()
            exit_code.set(1)
            self._handle_exit_signal()
            return

        plugins_check_status = self._initialize_plugins()
        if plugins_check_status == 0: # Error in plugin init
            self.logger.info("存在插件无法正常初始化, Steamauto即将退出！ ")
            pause()
            exit_code.set(1)
            self._handle_exit_signal()
            return
        elif plugins_check_status == 2: # No plugins enabled
            self.logger.error("未启用任何插件或插件加载失败, Steamauto即将退出！")
            pause()
            exit_code.set(1) 
            self._handle_exit_signal()
            return

        send_notification('Steamauto 已经成功登录Steam并开始运行')
        self._start_plugins() 

        self.logger.info("由于所有插件已经关闭,程序即将退出...")
        # exit_code should have been set by plugins or _start_plugins logic
        # If not set by plugins, and _start_plugins didn't set it to error, ensure it's 0 for normal completion.
        if exit_code.get() is None: # Check if it's still None
            exit_code.set(0)
            
        self._handle_exit_signal() 
        # The return value of run() is effectively managed by _handle_exit_signal calling os._exit
        # So, an explicit return here is more for logical completeness if os._exit was not called.


# Program entry point
if __name__ == "__main__":
    app = Application()
    app.run() # run() handles the entire lifecycle including setting exit codes and exiting.
