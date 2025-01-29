from hashlib import sha256
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
from ssl import SSLCertVerificationError
from typing import no_type_check

import json5
import requests
from colorama import Fore, Style
from requests.exceptions import SSLError

from plugins.BuffAutoAcceptOffer import BuffAutoAcceptOffer
from plugins.BuffAutoComment import BuffAutoComment
from plugins.BuffAutoOnSale import BuffAutoOnSale
from plugins.BuffProfitReport import BuffProfitReport
from plugins.C5AutoAcceptOffer import C5AutoAcceptOffer
from plugins.ECOsteam import ECOsteamPlugin
from plugins.SteamAutoAcceptOffer import SteamAutoAcceptOffer
from plugins.UUAutoAcceptOffer import UUAutoAcceptOffer
from plugins.UUAutoLease import UUAutoLeaseItem
from plugins.UUAutoSell import UUAutoSellItem
from steampy.client import SteamClient
from steampy.exceptions import ApiException
from utils.tools import calculate_sha256, jobHandler

try:
    from steampy.utils import ping_proxy  # type: ignore
except:

    def ping_proxy(nothing):
        return False


from utils.logger import handle_caught_exception
from utils.static import (BUILD_INFO, CONFIG_FILE_PATH, CONFIG_FOLDER,
                          CURRENT_VERSION, DEFAULT_CONFIG_JSON,
                          DEFAULT_STEAM_ACCOUNT_JSON, DEV_FILE_FOLDER,
                          LOGS_FOLDER, PLUGIN_FOLDER, SESSION_FOLDER,
                          STEAM_ACCOUNT_INFO_FILE_PATH, set_is_latest_version,
                          set_no_pause)
from utils.tools import (accelerator, compare_version, exit_code, get_encoding,
                         logger, pause)


def handle_global_exception(exc_type, exc_value, exc_traceback):
    logger.exception(
        "程序发生致命错误，请将此界面截图，并提交最新的log文件到https://github.com/jiajiaxd/Steamauto/issues",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
    logger.error("由于出现致命错误，程序即将退出...")
    pause()


def set_exit_code(code):
    global exit_code
    exit_code = code


def login_to_steam():
    global config
    steam_client = None
    steam_account_info = dict()
    with open(STEAM_ACCOUNT_INFO_FILE_PATH, "r", encoding=get_encoding(STEAM_ACCOUNT_INFO_FILE_PATH)) as f:
        try:
            steam_account_info = json5.loads(f.read())
        except Exception as e:
            handle_caught_exception(e, known=True)
            logger.error("检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + "格式错误, 请检查配置文件格式是否正确! ")
            pause()
            return None

    steam_session_path = os.path.join(SESSION_FOLDER, steam_account_info.get("steam_username", "").lower() + ".pkl")  # type: ignore
    if not os.path.exists(steam_session_path):
        logger.info("检测到首次登录Steam，正在尝试登录...登录完成后会自动缓存登录信息")
    else:
        logger.info("检测到缓存的Steam登录信息, 正在尝试登录...")
        try:
            with open(steam_session_path, "rb") as f:
                client = pickle.load(f)
                if config["steam_login_ignore_ssl_error"]:
                    logger.warning("警告: 已经关闭SSL验证, 请确保你的网络安全")
                    client._session.verify = False
                    requests.packages.urllib3.disable_warnings()  # type: ignore
                else:
                    client._session.verify = True
                if config["steam_local_accelerate"]:
                    logger.info("已经启用Steamauto内置加速")
                    client._session.auth = accelerator()

                if client.is_session_alive():
                    logger.info("登录成功")
                    steam_client = client
        except requests.exceptions.ConnectionError as e:
            handle_caught_exception(e, known=True)
            logger.error("使用缓存的登录信息登录失败!可能是网络异常")
            steam_client = None
        except (EOFError, pickle.UnpicklingError) as e:
            handle_caught_exception(e, known=True)
            shutil.rmtree(SESSION_FOLDER)
            os.mkdir(SESSION_FOLDER)
            steam_client = None
            logger.error("检测到缓存的登录信息异常，已自动清空session文件夹")
        except AssertionError as e:
            handle_caught_exception(e, known=True)
            if config["steam_local_accelerate"]:
                logger.error("由于内置加速问题,暂时无法登录.请稍等10分钟后再进行登录,或者关闭内置加速功能！")
            else:
                logger.error("未知登录错误,可能是由于网络问题?")
    if steam_client is None:
        try:
            logger.info("正在登录Steam...")
            if "use_proxies" not in config:
                config["use_proxies"] = False
            if not (config.get("proxies", None)):
                config["use_proxies"] = False
            if config["use_proxies"]:
                logger.info("已经启用Steam代理")

                if not isinstance(config["proxies"], dict):
                    logger.error("proxies格式错误，请检查配置文件")
                    pause()
                    return None
                logger.info("正在检查代理服务器可用性...")
                proxy_status = ping_proxy(config["proxies"])
                if proxy_status is False:
                    logger.error("代理服务器不可用，请检查配置文件，或者将use_proxies配置项设置为false")
                    pause()
                    return None
                else:
                    logger.info("代理服务器可用")
                    logger.warning(
                        "警告: 你已启用proxy, 该配置将被缓存，下次启动Steamauto时请确保proxy可用，或删除session文件夹下的缓存文件再启动"
                    )

                client = SteamClient(api_key="", proxies=config["proxies"])

            else:
                client = SteamClient(api_key="")
            if config["steam_login_ignore_ssl_error"]:
                logger.warning("警告: 已经关闭SSL验证, 请确保你的网络安全")
                client._session.verify = False
                requests.packages.urllib3.disable_warnings()  # type: ignore
            if config["steam_local_accelerate"]:
                if config["use_proxies"]:
                    logger.warning('检测到你已经同时开启内置加速和代理功能！正常情况下不推荐通过这种方式使用软件。')
                logger.info("已经启用Steamauto内置加速")
                client._session.auth = accelerator()
            logger.info("正在登录...")
            client.login(
                steam_account_info.get("steam_username"),  # type: ignore
                steam_account_info.get("steam_password"),  # type: ignore
                json.dumps(steam_account_info),
            )
            if client.is_session_alive():
                logger.info("登录成功")
            else:
                logger.error("登录失败")
                return None
            with open(steam_session_path, "wb") as f:
                pickle.dump(client, f)
            logger.info("已经自动缓存session.")
            steam_client = client
        except FileNotFoundError as e:
            handle_caught_exception(e, known=True)
            logger.error(
                "未检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + ", 请添加到" + STEAM_ACCOUNT_INFO_FILE_PATH + "后再进行操作! "
            )
            pause()
            return None
        except (SSLCertVerificationError, SSLError) as e:
            handle_caught_exception(e, known=True)
            if config["steam_local_accelerate"]:
                logger.error(
                    "登录失败. 你开启了本地加速, 但是未关闭SSL证书验证. 请在配置文件中将steam_login_ignore_ssl_error设置为true"
                )
            else:
                logger.error(
                    "登录失败. SSL证书验证错误! "
                    "若您确定网络环境安全, 可尝试将配置文件中的steam_login_ignore_ssl_error设置为true\n"
                )
            pause()
            return None
        except (requests.exceptions.ConnectionError, TimeoutError) as e:
            handle_caught_exception(e, known=True)
            logger.error(
                "网络错误! \n强烈建议使用Steamauto内置加速，仅需在配置文件中将steam_login_ignore_ssl_error和steam_local_accelerate设置为true即可使用 \n注意: 使用游戏加速器并不能解决问题，请使用代理软件如Clash/Proxifier等"
            )
            pause()
            return None
        except (ValueError, ApiException) as e:
            handle_caught_exception(e, known=True)
            logger.error("登录失败. 请检查" + STEAM_ACCOUNT_INFO_FILE_PATH + "的格式或内容是否正确!\n")
            pause()
            return None
        except (TypeError, AttributeError) as e:
            handle_caught_exception(e, known=True)
            logger.error(
                "登录失败.可能原因如下：\n 1 代理问题，不建议同时开启proxy和内置代理，或者是代理波动，可以重试\n2 Steam服务器波动，无法登录"
            )
            pause()
            return None
        except Exception as e:
            handle_caught_exception(e, known=True)
            logger.error("登录失败. 请检查" + STEAM_ACCOUNT_INFO_FILE_PATH + "的格式或内容是否正确!\n")
            pause()
            return None
    return steam_client


# 文件缺失或格式错误返回0，首次运行返回1，非首次运行返回2
def init_files_and_params() -> int:
    global config
    development_mode = False

    if os.path.exists('update.txt'):
        with open('update.txt', 'r') as f:
            old_version_path = f.read()
        try:
            os.remove(old_version_path)
            os.remove('update.txt')
            logger.info('自动更新完毕！已删除旧版本文件')
        except Exception as e:
            handle_caught_exception(e, known=True)
            logger.error('无法删除旧版本文件 请手动删除！')

    logger.info("欢迎使用Steamauto Github仓库:https://github.com/Steamauto/Steamauto")
    logger.info("欢迎加入Steamauto 官方QQ群 群号: 425721057")
    logger.info("若您觉得Steamauto好用, 请给予Star支持, 谢谢! \n")
    logger.info(
        f"{Fore.RED+Style.BRIGHT}！！！ 本程序完全{Fore.YELLOW}免费开源 {Fore.RED}若有人向你售卖，请立即投诉并申请退款 ！！！ \n"
    )
    logger.info(f"当前版本: {CURRENT_VERSION}   编译信息: {BUILD_INFO}")
    try:
        from utils import cloud_service

        cloud_service.checkVersion()
        cloud_service.getAds()
    except Exception as e:
        logger.warning('无法使用云服务')
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
        with open(CONFIG_FILE_PATH, "r", encoding=get_encoding(CONFIG_FILE_PATH)) as f:
            try:
                config = json5.load(f)
            except Exception as e:
                handle_caught_exception(e, known=True)
                logger.error("检测到" + CONFIG_FILE_PATH + "格式错误, 请检查配置文件格式是否正确! ")
                return 0
    if not os.path.exists(STEAM_ACCOUNT_INFO_FILE_PATH):
        with open(STEAM_ACCOUNT_INFO_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_STEAM_ACCOUNT_JSON)
            logger.info("检测到首次运行, 已为您生成" + STEAM_ACCOUNT_INFO_FILE_PATH + ", 请按照README提示填写配置文件! ")
            first_run = True

    if not first_run:
        if "no_pause" in config:
            set_no_pause(config["no_pause"])
        if "development_mode" not in config:
            config["development_mode"] = False
        if "steam_login_ignore_ssl_error" not in config:
            config["steam_login_ignore_ssl_error"] = False
        if "steam_local_accelerate" not in config:
            config["steam_local_accelerate"] = False
        if "development_mode" in config and config["development_mode"]:
            development_mode = True
        if development_mode:
            logger.info("开发者模式已开启")

    if first_run:
        return 1
    else:
        return 2


@no_type_check
def get_plugins_folder():
    base_path = os.path.dirname(os.path.abspath(__file__))
    if hasattr(sys, '_MEIPASS'):
        base_path = os.path.dirname(sys.executable)
        if not os.path.exists(os.path.join(base_path, PLUGIN_FOLDER)):
            shutil.copytree(os.path.join(sys._MEIPASS, PLUGIN_FOLDER), os.path.join(base_path, PLUGIN_FOLDER))
        else:
            plugins = os.listdir(os.path.join(sys._MEIPASS, PLUGIN_FOLDER))
            for plugin in plugins:
                plugin_absolute = os.path.join(sys._MEIPASS, PLUGIN_FOLDER, plugin)
                local_plugin_absolute = os.path.join(base_path, PLUGIN_FOLDER, plugin)
                if not os.path.exists(local_plugin_absolute):
                    shutil.copy(plugin_absolute, local_plugin_absolute)
                else:
                    local_plugin_sha256 = calculate_sha256(local_plugin_absolute)
                    plugin_sha256 = calculate_sha256(plugin_absolute)
                    if local_plugin_sha256 != plugin_sha256:
                        if plugin not in config['plugins_whitelist']:
                            logger.info('检测到插件' + plugin + '有更新，已自动更新 如果不需要更新请在配置文件中将该插件加入白名单')
                            shutil.copy(plugin_absolute, local_plugin_absolute)
                        else:
                            logger.info('插件' + plugin + '与本地版本不同 由于已被加入白名单，不会自动更新')     
    return os.path.join(base_path, PLUGIN_FOLDER)


def import_all_plugins():
    # 自动导入所有插件
    plugin_files = [f for f in os.listdir(get_plugins_folder()) if f.endswith(".py") and f != "__init__.py"]

    for plugin_file in plugin_files:
        module_name = f"{PLUGIN_FOLDER}.{plugin_file[:-3]}"
        if module_name.startswith(f'{PLUGIN_FOLDER}.External'):
            globals()[module_name] = importlib.import_module(module_name)
        else:
            importlib.import_module(module_name)


def camel_to_snake(name):
    if name == "ECOsteamPlugin":  # 特殊处理
        return "ecosteam"
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


# 添加自定义插件的方法：在plugins文件夹下新建.py文件, 文件名需要以External开头, 然后在文件中定义一个类, 类名需要以External开头, 且有且只有一个类名以External开头
def get_plugin_classes():
    plugin_classes = {}
    for name, obj in globals().items():
        if inspect.isclass(obj) and obj.__module__.startswith(PLUGIN_FOLDER):
            plugin_name = camel_to_snake(obj.__name__)  # 将驼峰命名转换为下划线命名
            plugin_classes[plugin_name] = obj
        if inspect.ismodule(obj) and obj.__name__.startswith(f'{PLUGIN_FOLDER}.External'):
            for name, obj2 in inspect.getmembers(obj):
                if inspect.isclass(obj2) and name.startswith("External"):
                    plugin_name = camel_to_snake(obj.__name__)
                    plugin_classes[plugin_name] = obj2
    # 返回的文件结构：
    # {
    #     "[插件名]": [插件类],
    #     ...
    # }
    return plugin_classes


def get_plugins_enabled(steam_client: SteamClient, steam_client_mutex):
    global config
    plugins_enabled = []
    plugin_classes = get_plugin_classes()  # 获取所有插件类

    for plugin_key, plugin_class in plugin_classes.items():
        if (plugin_key in config and "enable" in config[plugin_key] and config[plugin_key]["enable"]) or plugin_key.startswith(
            f'{PLUGIN_FOLDER.lower()}._external'
        ):
            if plugin_key.startswith(f'{PLUGIN_FOLDER.lower()}._external'):
                logger.info('已加载自定义插件: ' + plugin_key)
            args = []
            if hasattr(plugin_class, '__init__'):
                init_signature = inspect.signature(plugin_class.__init__)
                for param in init_signature.parameters.values():
                    if param.name == "logger":
                        args.append(logger)
                    elif param.name == "steam_client":
                        args.append(steam_client)
                    elif param.name == "steam_client_mutex":
                        args.append(steam_client_mutex)
                    elif param.name == "config":
                        args.append(config)
            plugin_instance = plugin_class(*args)
            plugins_enabled.append(plugin_instance)

    return plugins_enabled


def plugins_check(plugins_enabled):
    if len(plugins_enabled) == 0:
        logger.error("未启用任何插件, 请检查" + CONFIG_FILE_PATH + "是否正确! ")
        return 2
    for plugin in plugins_enabled:
        if plugin.init():
            return 0
    return 1


def get_steam_client_mutexs(num):
    steam_client_mutexs = []
    for i in range(num):
        steam_client_mutexs.append(threading.Lock())
    return steam_client_mutexs


def init_plugins_and_start(steam_client, steam_client_mutex):
    plugins_enabled = get_plugins_enabled(steam_client, steam_client_mutex)
    logger.info("初始化完成, 开始运行插件!")
    print("\n")
    time.sleep(0.1)
    if len(plugins_enabled) == 1:
        exit_code.set(plugins_enabled[0].exec())
    else:
        threads = []
        for plugin in plugins_enabled:
            threads.append(threading.Thread(target=plugin.exec))
        for thread in threads:
            thread.daemon = True
            thread.start()
        for thread in threads:
            thread.join()
    if exit_code.get() != 0:
        logger.warning("所有插件都已经退出！这不是一个正常情况，请检查配置文件！")


tried_exit = False


def exit_app(signal_, frame):
    global tried_exit
    if not tried_exit:
        tried_exit = True
        jobHandler.terminate_all()
        logger.warning("正在退出...若无响应，请再按一次Ctrl+C或者直接关闭窗口")
        os._exit(exit_code.get())
    else:
        logger.warning("程序已经强制退出")
        pid = os.getpid()
        os.kill(pid, signal.SIGTERM)


# 主函数
def main():
    global config
    # 初始化
    init_status = init_files_and_params()
    if init_status == 0:
        pause()
        return 1
    elif init_status == 1:
        pause()
        return 0

    steam_client = None
    steam_client = login_to_steam()
    if steam_client is None:
        return 1
    steam_client_mutex = threading.Lock()
    # 仅用于获取启用的插件
    import_all_plugins()
    plugins_enabled = get_plugins_enabled(steam_client, steam_client_mutex)
    # 检查插件是否正确初始化
    plugins_check_status = plugins_check(plugins_enabled)
    if plugins_check_status == 0:
        logger.info("存在插件无法正常初始化, Steamauto即将退出！ ")
        pause()
        return 1

    if steam_client is not None:
        init_plugins_and_start(steam_client, steam_client_mutex)

    logger.info("由于所有插件已经关闭,程序即将退出...")
    pause()
    return 1


# 程序运行开始处
if __name__ == "__main__":
    sys.excepthook = handle_global_exception
    signal.signal(signal.SIGINT, exit_app)
    if not os.path.exists(DEV_FILE_FOLDER):
        os.mkdir(DEV_FILE_FOLDER)
    if not os.path.exists(SESSION_FOLDER):
        os.mkdir(SESSION_FOLDER)
    exit_code.set(main())  # type: ignore
    exit_app(None, None)
