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
from steampy.client import SteamClient
from utils.code_updater import attempt_auto_update_github
from utils.logger import handle_caught_exception, logger
from utils.notifier import send_notification
from utils.static import (BUILD_INFO, CONFIG_FILE_PATH, CONFIG_FOLDER,
                          CURRENT_VERSION, DEFAULT_CONFIG_JSON,
                          DEFAULT_STEAM_ACCOUNT_JSON, DEV_FILE_FOLDER,
                          PLUGIN_FOLDER, SESSION_FOLDER,
                          STEAM_ACCOUNT_INFO_FILE_PATH)
from utils.old_version_patches import patch
from utils.steam_client import login_to_steam, steam_client_mutex
from utils.tools import (calculate_sha256, exit_code, get_encoding, jobHandler, pause)


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


# 文件缺失或格式错误返回0，首次运行返回1，非首次运行返回2
def init_files_and_params() -> int:
    global config
    development_mode = False
    patch()
    logger.info("欢迎使用Steamauto Github仓库:https://github.com/Steamauto/Steamauto")
    logger.info("欢迎加入Steamauto 官方QQ群 群号: 425721057")
    logger.info("若您觉得Steamauto好用, 请给予Star支持, 谢谢! \n")
    logger.info(
        f"{Fore.RED+Style.BRIGHT}！！！ 本程序完全{Fore.YELLOW}免费开源 {Fore.RED}若有人向你售卖，请立即投诉并申请退款 ！！！ \n"
    )
    logger.info(f"当前版本: {CURRENT_VERSION}   编译信息: {BUILD_INFO}")

    try:
        with open(CONFIG_FILE_PATH, "r", encoding=get_encoding(CONFIG_FILE_PATH)) as f:
            config = json5.load(f)
    except:
        config = {}
    if not hasattr(os, "frozen"):
        if config.get("source_code_auto_update", False):
            attempt_auto_update_github(CURRENT_VERSION)
    try:
        from utils import cloud_service

        if hasattr(os, "frozen"):
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
            static.no_pause = config["no_pause"]
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
                        if plugin not in config.get('plugin_whitelist', []):
                            logger.info('检测到插件' + plugin + '有更新，已自动更新 如果不需要更新请在配置文件中将该插件加入白名单')
                            shutil.copy(plugin_absolute, local_plugin_absolute)
                        else:
                            logger.info('插件' + plugin + '与本地版本不同 由于已被加入白名单，不会自动更新')     
    return os.path.join(base_path, PLUGIN_FOLDER)

def import_module_from_file(module_name, file_path):
    """
    从指定文件路径动态导入模块。

    参数：
        module_name (str): 模块的名称（应在当前环境中唯一）。
        file_path (str): 模块的文件路径。

    返回：
        module: 导入的模块对象。
    """
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None:
            raise ImportError(f"无法从路径 '{file_path}' 创建模块规格")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module) # type: ignore
        sys.modules[module_name] = module
        return module
    except Exception as e:
        print(f"导入模块时出错: {e}")
        return None

def import_all_plugins():
    # 自动导入所有插件
    plugin_files = [f for f in os.listdir(get_plugins_folder()) if f.endswith(".py") and f != "__init__.py"]

    for plugin_file in plugin_files:
        module_name = f"{PLUGIN_FOLDER}.{plugin_file[:-3]}"
        import_module_from_file(module_name, os.path.join(get_plugins_folder(), plugin_file))


def camel_to_snake(name):
    if name == "ECOsteamPlugin":  # 特殊处理
        return "ecosteam"
    if name == "ECOsteam":  # 特殊处理
        return "ecosteam"
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def get_plugin_classes():
    plugin_classes = {}
    for name, obj in sys.modules.items():
        if name.startswith(f"{PLUGIN_FOLDER}.") and name != f"{PLUGIN_FOLDER}.__init__":
            plugin_name = name.replace(f"{PLUGIN_FOLDER}.", '')
            plugin_name = camel_to_snake(plugin_name)
            plugin_classes[plugin_name] = obj
    # 返回的文件结构：
    # {
    #     "[插件名]": [插件类],
    #     ...
    # }
    return plugin_classes


def get_plugins_enabled(steam_client: SteamClient, steam_client_mutex):
    global config
    plugins_enabled = []
    plugin_modules = get_plugin_classes()  # 获取所有插件类

    for plugin_key, plugin_module in plugin_modules.items():
        # 判断配置文件里是否存在 plugin_key 且已启用
        if (plugin_key in config and config[plugin_key].get("enable")) or plugin_key not in config:
            if plugin_key not in config:
                logger.info(f'已加载自定义插件 {plugin_key}')
            # 遍历插件模块里的所有类
            for cls_name, cls_obj in inspect.getmembers(plugin_module, inspect.isclass):
                # 根据构造函数的形参，对号入座。用kwargs可以避免顺序不一致的问题
                init_signature = inspect.signature(cls_obj.__init__)
                init_kwargs = {}
                unknown_class = False

                for param_name, param in init_signature.parameters.items():
                    if param_name == "logger":
                        init_kwargs[param_name] = logger
                    elif param_name == "steam_client":
                        init_kwargs[param_name] = steam_client
                    elif param_name == "steam_client_mutex":
                        init_kwargs[param_name] = steam_client_mutex
                    elif param_name == "config":
                        init_kwargs[param_name] = config
                    elif param_name == "self":
                        continue
                    else:
                        # 根本不认识这个类
                        unknown_class = True
                        break
                if unknown_class:
                    continue

                plugin_instance = cls_obj(**init_kwargs)
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
    steam_client = login_to_steam(config)
    if steam_client is None:
        send_notification('登录Steam失败，程序停止运行')
        pause()
        return 1
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
