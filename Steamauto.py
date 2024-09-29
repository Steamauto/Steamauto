# Steamauto.py

import importlib
import inspect
import json
import os
import pickle
import re
import shutil
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from ssl import SSLCertVerificationError
from typing import Dict, Any

import json5
import requests
from colorama import Fore, Style
from requests.exceptions import SSLError

from ConfigManager import ConfigManager
from steampy.client import SteamClient
from steampy.exceptions import ApiException

from utils.logger import handle_caught_exception, logger
from utils.static import (
    BUILD_INFO,
    CONFIG_FILE_PATH,
    CONFIG_FOLDER,
    CURRENT_VERSION,
    DEFAULT_CONFIG_JSON,
    DEFAULT_STEAM_ACCOUNT_JSON,
    SESSION_FOLDER,
    STEAM_ACCOUNT_INFO_FILE_PATH,
    set_is_latest_version,
    set_no_pause,
    PLUGIN_FOLDER,
)
from utils.tools import accelerator, compare_version, exit_code, get_encoding, pause


def handle_global_exception(exc_type, exc_value, exc_traceback):
    logger.exception(
        "程序发生致命错误，请将此界面截图，并提交最新的log文件到https://github.com/jiajiaxd/Steamauto/issues",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
    logger.error("由于出现致命错误，程序即将退出...")
    pause()


class Steamauto:
    def __init__(self):
        self.steam_session_path = os.path.join(SESSION_FOLDER, "session.pkl")
        self.config = {}
        self.steam_client = None
        self.steam_client_mutex = threading.Lock()
        self.plugins_enabled = []
        self.exit_code = 0
        self.tried_exit = False
        self.steam_account_info = {}

    @staticmethod
    def check_update():
        try:
            response_json = requests.get("https://steamauto.jiajiaxd.com/versions", params={"version": CURRENT_VERSION},
                                         timeout=5)
            data = response_json.json()
            latest_version = data["latest_version"]["version"]
            broadcast = data.get("broadcast", None)
            if broadcast:
                logger.info(f"Steamauto官方公告:\n {broadcast}\n")
            if compare_version(CURRENT_VERSION, latest_version) == -1:
                logger.info(f"检测到最新版本: {latest_version}")
                changelog_to_output = str()
                for version in data["history_versions"]:
                    if compare_version(CURRENT_VERSION, version["version"]) == -1:
                        changelog_to_output += f"版本: {version['version']}\n更新日志: {version['changelog']}\n\n"

                logger.info(f"\n{changelog_to_output}")
                set_is_latest_version(False)
                logger.warning("当前版本不是最新版本,为了您的使用体验,请及时更新!")
            else:
                set_is_latest_version(True)
                logger.info("当前版本已经是最新版本")
        except Exception as e:
            logger.warning("检查更新失败, 跳过检查更新")

    @staticmethod
    def ping_proxy(proxies: dict):
        try:
            requests.get('https://steamcommunity.com/login/dologin', proxies=proxies)
            return True
        except (requests.exceptions.ConnectionError, TimeoutError) as e:
            return False

    def init_files_and_params(self) -> int:
        logger.info("欢迎使用Steamauto Github仓库:https://github.com/jiajiaxd/Steamauto")
        logger.info("欢迎加入Steamauto 官方QQ群 群号: 425721057")
        logger.info("若您觉得Steamauto好用, 请给予Star支持, 谢谢! \n")
        logger.info(
            f"{Fore.RED + Style.BRIGHT}！！！ 本程序完全{Fore.YELLOW}免费开源 {Fore.RED}若有人向你售卖，请立即投诉并申请退款 ！！！ \n")
        logger.info(f"当前版本: {CURRENT_VERSION}   编译信息: {BUILD_INFO}")
        logger.info("正在检查更新...")

        # 检查更新
        self.check_update()

        logger.info("正在初始化...")
        first_run = False
        if not os.path.exists(CONFIG_FOLDER):
            os.mkdir(CONFIG_FOLDER)
        if not os.path.exists(CONFIG_FILE_PATH):
            with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
                f.write(DEFAULT_CONFIG_JSON)
            logger.info("检测到首次运行, 已为您生成" + CONFIG_FILE_PATH + ", 请按照README提示填写配置文件! ")
            first_run = True
        else:
            config = ConfigManager(CONFIG_FILE_PATH)
            if not config.load_config():
                return 0
            self.config = config.config

        if not os.path.exists(STEAM_ACCOUNT_INFO_FILE_PATH):
            with open(STEAM_ACCOUNT_INFO_FILE_PATH, "w", encoding="utf-8") as f:
                f.write(DEFAULT_STEAM_ACCOUNT_JSON)
                logger.info(
                    "检测到首次运行, 已为您生成" + STEAM_ACCOUNT_INFO_FILE_PATH + ", 请按照README提示填写配置文件! ")
                first_run = True

        if not first_run:
            if "no_pause" in self.config:
                set_no_pause(self.config["no_pause"])
            if "steam_login_ignore_ssl_error" not in self.config:
                self.config["steam_login_ignore_ssl_error"] = False
            if "steam_local_accelerate" not in self.config:
                self.config["steam_local_accelerate"] = False

        if first_run:
            return 1
        else:
            return 2

    def login_to_steam(self):
        if not self.load_steam_account_info():
            return None
        if self.steam_login_with_session() is None:
            return self.steam_login_with_account_info()
        return self.steam_client

    def load_steam_account_info(self):
        with open(STEAM_ACCOUNT_INFO_FILE_PATH, "r", encoding=get_encoding(STEAM_ACCOUNT_INFO_FILE_PATH)) as f:
            try:
                steam_account_info = json5.loads(f.read())
                self.steam_account_info = steam_account_info
                return steam_account_info
            except FileNotFoundError:
                logger.error(f"未检测到 {STEAM_ACCOUNT_INFO_FILE_PATH}, 请添加后再进行操作!")
                pause()
                return None
            except ValueError:
                logger.error(f"检测到 {STEAM_ACCOUNT_INFO_FILE_PATH} 格式错误, 请检查配置文件格式是否正确!")
                pause()
                return None
            except Exception as e:
                handle_caught_exception(e)
                logger.error(f"读取 {STEAM_ACCOUNT_INFO_FILE_PATH} 时发生未知错误: {e}")
                pause()
                return None

    def steam_login_with_session(self):
        self.steam_session_path = os.path.join(SESSION_FOLDER,
                                               self.steam_account_info.get("steam_username",
                                                                           "session").lower() + ".pkl")
        if not os.path.exists(self.steam_session_path):
            logger.info("检测到首次登录Steam，正在尝试登录...登录完成后会自动缓存登录信息")
        else:
            logger.info("检测到缓存的Steam登录信息, 正在尝试登录...")
            try:
                with open(self.steam_session_path, "rb") as f:
                    client = pickle.load(f)
                    if self.config["steam_login_ignore_ssl_error"]:
                        logger.warning("警告: 已经关闭SSL验证, 请确保你的网络安全")
                        client._session.verify = False
                        requests.packages.urllib3.disable_warnings()
                    else:
                        client._session.verify = True
                    if self.config["steam_local_accelerate"]:
                        logger.info("已经启用Steamauto内置加速")
                        client._session.auth = accelerator()

                    if client.is_session_alive():
                        logger.info("登录成功")
                        self.steam_client = client
                        return client
            except Exception as e:
                handle_caught_exception(e)
                logger.error("使用缓存的登录信息登录失败!可能是网络异常")
                self.steam_client = None
                return None

    def steam_login_with_account_info(self):
        if self.steam_client is None:
            try:
                logger.info("正在登录Steam...")
                if "use_proxies" not in self.config:
                    self.config["use_proxies"] = False
                if not (self.config.get("proxies", None)):
                    self.config["use_proxies"] = False
                if self.config["use_proxies"]:
                    logger.info("已经启用Steam代理")

                    if not isinstance(self.config["proxies"], dict):
                        logger.error("proxies格式错误，请检查配置文件")
                        pause()
                        return None
                    logger.info("正在检查代理服务器可用性...")
                    proxy_status = self.ping_proxy(self.config["proxies"])
                    if proxy_status is False:
                        logger.error("代理服务器不可用，请检查配置文件，或者将use_proxies配置项设置为false")
                        pause()
                        return None
                    else:
                        logger.info("代理服务器可用")
                        logger.warning(
                            "警告: 你已启用proxy, 该配置将被缓存，下次启动Steamauto时请确保proxy可用，或删除session文件夹下的缓存文件再启动")

                    client = SteamClient(api_key="", proxies=self.config["proxies"])

                else:
                    client = SteamClient(api_key="")
                if self.config["steam_login_ignore_ssl_error"]:
                    logger.warning("警告: 已经关闭SSL验证, 请确保你的网络安全")
                    client._session.verify = False
                    requests.packages.urllib3.disable_warnings()  # type: ignore
                if self.config["steam_local_accelerate"]:
                    if self.config["use_proxies"]:
                        logger.warning('检测到你已经同时开启内置加速和代理功能！正常情况下不推荐通过这种方式使用软件。')
                    logger.info("已经启用Steamauto内置加速")
                    client._session.auth = accelerator()
                logger.info("正在登录...")
                client.login(
                    steam_account_info.get("steam_username"),  # type: ignore
                    steam_account_info.get("steam_password"),  # type: ignore
                    json.dumps(self.steam_account_info),
                )
                if client.is_session_alive():
                    logger.info("登录成功")
                else:
                    logger.error("登录失败")
                    return None
                with open(self.steam_session_path, "wb") as f:
                    pickle.dump(client, f)
                logger.info("已经自动缓存session.")
                self.steam_client = client
            except FileNotFoundError as e:
                handle_caught_exception(e)
                logger.error(
                    "未检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + ", 请添加到" + STEAM_ACCOUNT_INFO_FILE_PATH + "后再进行操作! ")
                pause()
                return None
            except (SSLCertVerificationError, SSLError) as e:
                handle_caught_exception(e)
                if self.config["steam_local_accelerate"]:
                    logger.error(
                        "登录失败. 你开启了本地加速, 但是未关闭SSL证书验证. 请在配置文件中将steam_login_ignore_ssl_error设置为true")
                else:
                    logger.error(
                        "登录失败. SSL证书验证错误! " "若您确定网络环境安全, 可尝试将配置文件中的steam_login_ignore_ssl_error设置为true\n")
                pause()
                return None
            except (requests.exceptions.ConnectionError, TimeoutError) as e:
                handle_caught_exception(e)
                logger.error(
                    "网络错误! \n强烈建议使用Steamauto内置加速，仅需在配置文件中将steam_login_ignore_ssl_error和steam_local_accelerate设置为true即可使用 \n注意: 使用游戏加速器并不能解决问题，请使用代理软件如Clash/Proxifier等"
                )
                pause()
                return None
            except (ValueError, ApiException) as e:
                handle_caught_exception(e)
                logger.error("登录失败. 请检查" + STEAM_ACCOUNT_INFO_FILE_PATH + "的格式或内容是否正确!\n")
                pause()
                return None
            except (TypeError, AttributeError) as e:
                handle_caught_exception(e)
                logger.error(
                    "登录失败.可能原因如下：\n 1 代理问题，不建议同时开启proxy和内置代理，或者是代理波动，可以重试\n2 Steam服务器波动，无法登录")
                pause()
                return None
            except Exception as e:
                handle_caught_exception(e)
                logger.error("登录失败. 请检查" + STEAM_ACCOUNT_INFO_FILE_PATH + "的格式或内容是否正确!\n")
                pause()
                return None
        return self.steam_client

    @staticmethod
    def get_base_path():
        if hasattr(sys, '_MEIPASS'):
            return os.path.join(sys._MEIPASS)
        else:
            return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def get_plugins_folder():
        base_path = Steamauto.get_base_path()
        return os.path.join(base_path, PLUGIN_FOLDER)

    @staticmethod
    def import_all_plugins():
        plugin_files = [f for f in os.listdir(Steamauto.get_plugins_folder()) if
                        f.endswith(".py") and f != "__init__.py"]

        for plugin_file in plugin_files:
            module_name = f"{PLUGIN_FOLDER}.{plugin_file[:-3]}"
            if module_name.startswith(f'{PLUGIN_FOLDER}.External'):
                globals()[module_name] = importlib.import_module(module_name)
            else:
                importlib.import_module(module_name)

    @staticmethod
    def camel_to_snake(name):
        if name == "ECOSteam":
            return "ecosteam"
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

    @staticmethod
    def get_plugin_classes() -> Dict[str, Any]:
        plugin_classes = {}
        for module_name, module in sys.modules.items():
            if module_name.startswith(PLUGIN_FOLDER):
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if obj.__module__ == module_name:
                        plugin_name = Steamauto.camel_to_snake(obj.__name__)
                        plugin_classes[plugin_name] = obj
        return plugin_classes

    def get_plugins_enabled(self):
        plugins_enabled = []
        plugin_classes = self.get_plugin_classes()

        for plugin_key, plugin_class in plugin_classes.items():
            if ((plugin_key in self.config and "enable" in self.config[plugin_key]
                and self.config[plugin_key]["enable"]) or
                    plugin_key.startswith(f'{PLUGIN_FOLDER.lower()}._external')):
                if plugin_key.startswith(f'{PLUGIN_FOLDER.lower()}._external'):
                    logger.info('已加载自定义插件: ' + plugin_key)
                args = []
                if hasattr(plugin_class, '__init__'):
                    init_signature = inspect.signature(plugin_class.__init__)
                    for param in init_signature.parameters.values():
                        if param.name == "logger":
                            args.append(logger)
                        elif param.name == "steam_client":
                            args.append(self.steam_client)
                        elif param.name == "steam_client_mutex":
                            args.append(self.steam_client_mutex)
                        elif param.name == "config":
                            args.append(self.config)
                plugin_instance = plugin_class(*args)
                plugins_enabled.append(plugin_instance)

        return plugins_enabled

    def plugins_check(self):
        if len(self.plugins_enabled) == 0:
            logger.error("未启用任何插件, 请检查" + CONFIG_FILE_PATH + "是否正确! ")
            return 2
        for plugin in self.plugins_enabled:
            if plugin.init():
                return 0
        return 1

    def init_plugins_and_start(self):
        logger.info("初始化完成, 开始运行插件!")
        if len(self.plugins_enabled) == 1:
            self.exit_code = self.plugins_enabled[0].exec()
        else:
            with ThreadPoolExecutor(max_workers=len(self.plugins_enabled)) as executor:
                futures = [executor.submit(plugin.exec) for plugin in self.plugins_enabled]
                for future in futures:
                    try:
                        future.result()
                    except Exception as e:
                        handle_caught_exception(e)
                        logger.error(f"插件执行出错: {e}")
        if self.exit_code != 0:
            logger.warning("所有插件都已经退出！这不是一个正常情况，请检查配置文件！")

    def main(self):
        init_status = self.init_files_and_params()
        if init_status == 0:
            pause()
            return 1
        elif init_status == 1:
            pause()
            return 0

        self.steam_client = self.login_to_steam()
        if self.steam_client is None:
            return 1

        self.import_all_plugins()
        self.plugins_enabled = self.get_plugins_enabled()
        plugins_check_status = self.plugins_check()
        if plugins_check_status != 0:
            logger.info("存在插件无法正常初始化, Steamauto即将退出！ ")
            pause()
            return 1

        if self.steam_client is not None:
            self.init_plugins_and_start()

        logger.info("由于所有插件已经关闭,程序即将退出...")
        pause()
        return 1

    def exit_app(self, signal_, frame):
        if not self.tried_exit:
            self.tried_exit = True
            logger.warning("正在退出...若无响应，请再按一次Ctrl+C或者直接关闭窗口")
            sys.exit(self.exit_code)
        else:
            logger.warning("程序已经强制退出")
            pid = os.getpid()
            os.kill(pid, signal.SIGTERM)


if __name__ == "__main__":
    steam_auto = Steamauto()
    sys.excepthook = handle_global_exception
    signal.signal(signal.SIGINT, steam_auto.exit_app)
    if not os.path.exists(SESSION_FOLDER):
        os.mkdir(SESSION_FOLDER)
    steam_auto.exit_code = steam_auto.main()
    steam_auto.exit_app(None, None)
