import pickle
import shutil
import signal
import sys
import threading
import time
import os

import pyjson5 as json
import requests
from requests.exceptions import SSLError

from plugins.BuffAutoAcceptOffer import BuffAutoAcceptOffer
from plugins.BuffAutoOnSale import BuffAutoOnSale
from plugins.SteamAutoAcceptOffer import SteamAutoAcceptOffer
from plugins.UUAutoAcceptOffer import UUAutoAcceptOffer
from steampy.client import SteamClient
from steampy.exceptions import CaptchaRequired, ApiException
from utils.logger import handle_caught_exception
from utils.tools import get_encoding, pause, logger, compare_version
from utils.static import (
    CONFIG_FOLDER,
    CONFIG_FILE_PATH,
    EXAMPLE_CONFIG_FILE_PATH,
    DEFAULT_STEAM_ACCOUNT_JSON,
    STEAM_ACCOUNT_INFO_FILE_PATH,
    STEAM_SESSION_PATH,
    DEV_FILE_FOLDER,
    SESSION_FOLDER,
    UU_TOKEN_FILE_PATH,
)

if "-uu" in sys.argv:
    import uuyoupinapi

    logger.info("你使用了-uu参数启动Steamauto,这代表着Steamauto会引导你获取悠悠有品的token")
    logger.info("如果无需获取悠悠有品的token,请删除-uu参数后重启Steamauto")
    logger.info("按回车键继续...")
    input()
    token = uuyoupinapi.UUAccount.get_token_automatically()
    if not os.path.exists(CONFIG_FOLDER):
        os.mkdir(CONFIG_FOLDER)
    with open(UU_TOKEN_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(token)
    logger.info(f"已自动获取悠悠有品token,并写入{UU_TOKEN_FILE_PATH}.")
    logger.info("按回车键继续启动Steamauto...")
    input()


def handle_global_exception(exc_type, exc_value, exc_traceback):
    logger.exception(
        "程序发生致命错误，请将此界面截图，并提交最新的log文件到https://github.com/jiajiaxd/Steamauto/issues",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
    logger.error("由于出现致命错误，程序即将退出...")
    pause()


def login_to_steam():
    global config
    steam_client = None
    if not os.path.exists(STEAM_SESSION_PATH):
        logger.info("检测到首次登录Steam，正在尝试登录...登录完成后会自动缓存session")
    else:
        logger.info("检测到缓存的steam_session, 正在尝试登录...")
        try:
            with open(STEAM_SESSION_PATH, "rb") as f:
                client = pickle.load(f)
                if config["steam_login_ignore_ssl_error"]:
                    logger.warning("警告: 已经关闭SSL验证, 账号可能存在安全问题")
                    client._session.verify = False
                    requests.packages.urllib3.disable_warnings()
                else:
                    client._session.verify = True

                if client.is_session_alive():
                    logger.info("登录成功")
                    steam_client = client
        except requests.exceptions.ConnectionError as e:
            handle_caught_exception(e)
            logger.error("使用缓存的session登录失败!可能是网络异常.")
            steam_client = None
        except EOFError as e:
            handle_caught_exception(e)
            shutil.rmtree(SESSION_FOLDER)
            steam_client = None
            logger.error("session文件异常.已删除session文件夹.")
    if steam_client is None:
        try:
            logger.info("正在登录Steam...")
            with open(STEAM_ACCOUNT_INFO_FILE_PATH, "r", encoding=get_encoding(STEAM_ACCOUNT_INFO_FILE_PATH)) as f:
                try:
                    acc = json.load(f)
                except (json.Json5DecoderException, json.Json5IllegalCharacter) as e:
                    handle_caught_exception(e)
                    logger.error("检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + "格式错误, 请检查配置文件格式是否正确! ")
                    pause()
                    return None
            client = SteamClient(acc.get("api_key"))
            if config["steam_login_ignore_ssl_error"]:
                logger.warning("警告: 已经关闭SSL验证, 账号可能存在安全问题")
                client._session.verify = False
                requests.packages.urllib3.disable_warnings()
            SteamClient.login(client, acc.get("steam_username"), acc.get("steam_password"), STEAM_ACCOUNT_INFO_FILE_PATH)
            with open(STEAM_SESSION_PATH, "wb") as f:
                pickle.dump(client, f)
            logger.info("登录完成! 已经自动缓存session.")
            steam_client = client
        except FileNotFoundError as e:
            handle_caught_exception(e)
            logger.error("未检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + ", 请添加到" + STEAM_ACCOUNT_INFO_FILE_PATH + "后再进行操作! ")
            pause()
            return None
        except (requests.exceptions.ConnectionError, TimeoutError) as e:
            handle_caught_exception(e)
            logger.error(
                "\n网络错误! 请通过修改hosts/使用代理等方法代理Python解决问题. \n" "注意: 使用游戏加速器并不能解决问题. 请尝试使用Proxifier及其类似软件代理Python.exe解决. "
            )
            pause()
            return None
        except SSLError as e:
            handle_caught_exception(e)
            logger.error("登录失败. SSL证书验证错误! " "若您确定网络环境安全, 可尝试将config.json中的steam_login_ignore_ssl_error设置为true\n")
            pause()
            return None
        except (ValueError, ApiException) as e:
            handle_caught_exception(e)
            logger.error("登录失败. 请检查" + STEAM_ACCOUNT_INFO_FILE_PATH + "的格式或内容是否正确!\n")
            pause()
            return None
        except CaptchaRequired as e:
            handle_caught_exception(e)
            logger.error("登录失败. 触发Steam风控, 请尝试更换加速器节点.\n" "若您不知道该使用什么加速器，推荐使用 Watt Toolkit 自带的免费Steam加速(请开启hosts代理模式).")
            pause()
            return None
    return steam_client


def main():
    global config
    development_mode = False
    logger.info("欢迎使用Steamauto Github仓库:https://github.com/jiajiaxd/Steamauto")
    logger.info("若您觉得Steamauto好用, 请给予Star支持, 谢谢! ")
    if os.path.exists("version.json"):
        with open("version.json", "r", encoding="utf-8") as f:
            try:
                current_version = json.load(f)['this_version']
                logger.info(f"当前版本: {current_version}")
                logger.info("正在检查更新...")
                try:
                    response_json = requests.get("https://steamauto.jiajiaxd.com/latest", timeout=5)
                    data = response_json.json()
                    latest_version = data["latest_version"]
                    if compare_version(current_version, latest_version) == -1:
                        logger.info(f"检测到新版本: {latest_version}")
                        changelog_to_output = str()
                        for version in latest_version['history_versions']:
                            if compare_version(current_version, version['version']) == -1:
                                changelog_to_output += f"版本: {version['version']}\n更新日志: {version['changelog']}\n"
                        logger.info(f"更新日志:\n{changelog_to_output}")
                except requests.exceptions.Timeout as e:
                    handle_caught_exception(e)
                    logger.info("检查更新超时, 跳过检查更新")
            except (json.Json5DecoderException, json.Json5IllegalCharacter) as e:
                handle_caught_exception(e)
                logger.warning('检测到version.json格式错误,将会跳过检查更新')
    else:
        logger.debug("未检测到version.json,将会跳过检查更新")
    logger.info("正在初始化...")
    first_run = False
    if not os.path.exists(CONFIG_FOLDER):
        os.mkdir(CONFIG_FOLDER)
    if not os.path.exists(CONFIG_FILE_PATH):
        if not os.path.exists(EXAMPLE_CONFIG_FILE_PATH):
            logger.error("未检测到" + EXAMPLE_CONFIG_FILE_PATH + ", 请前往GitHub进行下载, 并保证文件和程序在同一目录下. ")
            pause()
            return 0
        else:
            shutil.copy(EXAMPLE_CONFIG_FILE_PATH, CONFIG_FILE_PATH)
            logger.info("检测到首次运行, 已为您生成" + CONFIG_FILE_PATH + ", 请按照README提示填写配置文件! ")
    with open(CONFIG_FILE_PATH, "r", encoding=get_encoding(CONFIG_FILE_PATH)) as f:
        try:
            config = json.load(f)
        except (json.Json5DecoderException, json.Json5IllegalCharacter) as e:
            handle_caught_exception(e)
            logger.error("检测到" + CONFIG_FILE_PATH + "格式错误, 请检查配置文件格式是否正确! ")
            pause()
            return 0
    if not os.path.exists(STEAM_ACCOUNT_INFO_FILE_PATH):
        with open(STEAM_ACCOUNT_INFO_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_STEAM_ACCOUNT_JSON)
            logger.info("检测到首次运行, 已为您生成" + STEAM_ACCOUNT_INFO_FILE_PATH + ", 请按照README提示填写配置文件! ")
            first_run = True

    if "development_mode" in config and config["development_mode"]:
        development_mode = True
    if development_mode:
        logger.info("开发者模式已开启")
    steam_client = None
    if development_mode:
        logger.info("开发者模式已开启, 跳过Steam登录")
    elif not first_run:
        steam_client = login_to_steam()
        if steam_client is None:
            return 1
    plugins_enabled = []
    if (
        "buff_auto_accept_offer" in config
        and "enable" in config["buff_auto_accept_offer"]
        and config["buff_auto_accept_offer"]["enable"]
    ):
        buff_auto_accept_offer = BuffAutoAcceptOffer(logger, steam_client, config)
        plugins_enabled.append(buff_auto_accept_offer)
    if "buff_auto_on_sale" in config and "enable" in config["buff_auto_on_sale"] and config["buff_auto_on_sale"]["enable"]:
        buff_auto_on_sale = BuffAutoOnSale(logger, steam_client, config)
        plugins_enabled.append(buff_auto_on_sale)
    if (
        "uu_auto_accept_offer" in config
        and "enable" in config["uu_auto_accept_offer"]
        and config["uu_auto_accept_offer"]["enable"]
    ):
        uu_auto_accept_offer = UUAutoAcceptOffer(logger, steam_client, config)
        plugins_enabled.append(uu_auto_accept_offer)
    if (
        "steam_auto_accept_offer" in config
        and "enable" in config["steam_auto_accept_offer"]
        and config["steam_auto_accept_offer"]["enable"]
    ):
        steam_auto_accept_offer = SteamAutoAcceptOffer(logger, steam_client, config)
        plugins_enabled.append(steam_auto_accept_offer)
    if len(plugins_enabled) == 0:
        logger.error("未启用任何插件, 请检查" + CONFIG_FILE_PATH + "是否正确! ")
        pause()
        return 2
    for plugin in plugins_enabled:
        if plugin.init():
            first_run = True
    if first_run:
        logger.info("首次运行, 请按照README提示填写配置文件! ")
        pause()
        return 0
    logger.info("初始化完成, 开始运行插件!")
    print("\n")
    time.sleep(0.1)
    if len(plugins_enabled) == 1:
        plugins_enabled[0].exec()
    else:
        threads = []
        for plugin in plugins_enabled:
            threads.append(threading.Thread(target=plugin.exec))
        for thread in threads:
            thread.daemon = True
            thread.start()
        for thread in threads:
            thread.join()


def exit_app(signal_, frame):
    logger.info("正在退出...")
    sys.exit()


if __name__ == "__main__":
    sys.excepthook = handle_global_exception
    signal.signal(signal.SIGINT, exit_app)
    if not os.path.exists(DEV_FILE_FOLDER):
        os.mkdir(DEV_FILE_FOLDER)
    if not os.path.exists(SESSION_FOLDER):
        os.mkdir(SESSION_FOLDER)
    exit_code = main()
    if exit_code is not None:
        sys.exit(exit_code)
    else:
        sys.exit()
