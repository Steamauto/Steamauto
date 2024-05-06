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

import json5
import requests
from bs4 import BeautifulSoup
from requests.exceptions import SSLError

from plugins.BuffAutoAcceptOffer import BuffAutoAcceptOffer
from plugins.BuffAutoComment import BuffAutoComment
from plugins.BuffAutoOnSale import BuffAutoOnSale
from plugins.BuffProfitReport import BuffProfitReport
from plugins.SteamAutoAcceptOffer import SteamAutoAcceptOffer
from plugins.UUAutoAcceptOffer import UUAutoAcceptOffer
from steampy.client import SteamClient
from steampy.exceptions import (ApiException, CaptchaRequired,
                                InvalidCredentials)
try:
    from steampy.utils import ping_proxy
except:
    def ping_proxy(nothing):
        return False
from utils.logger import handle_caught_exception
from utils.static import (CONFIG_FILE_PATH, CONFIG_FOLDER, DEFAULT_CONFIG_JSON,
                          DEFAULT_STEAM_ACCOUNT_JSON, DEV_FILE_FOLDER,
                          SESSION_FOLDER, STEAM_ACCOUNT_INFO_FILE_PATH,
                          UU_ARG_FILE_PATH, UU_TOKEN_FILE_PATH, set_no_pause, STEAM_ACCOUNT_JSON_INFO_FILE_PATH)
from utils.tools import (accelerator, compare_version, exit_code, get_encoding,
                         logger, pause)

current_version = "3.5.0"

if ("-uu" in sys.argv) or (os.path.exists(UU_ARG_FILE_PATH)):
    import uuyoupinapi

    if os.path.exists("uu.txt") or os.path.exists("uu.txt.txt"):
        logger.info("检测到uu.txt文件,已经自动使用-uu参数启动Steamauto")
        logger.info("已经自动删除uu.txt文件")
        os.remove("uu.txt")
    logger.info("你使用了-uu参数启动Steamauto,这代表着Steamauto会引导你获取悠悠有品的token")
    logger.info("如果无需获取悠悠有品的token,请删除-uu参数后重启Steamauto")
    logger.info("按回车键继续...")
    input()
    token = uuyoupinapi.UUAccount.get_token_automatically()
    if not os.path.exists(CONFIG_FOLDER):
        os.mkdir(CONFIG_FOLDER)
    with open(UU_TOKEN_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(token)
    logger.info(f"已成功获取悠悠有品token,并写入{UU_TOKEN_FILE_PATH}中!")
    logger.info("需要注意的是, 你需要在配置文件中将uu_auto_accept_offer.enable设置为true才能使用悠悠有品的自动发货功能")
    logger.info("按回车键继续启动Steamauto...")
    input()


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


def get_api_key(steam_client):
    resp = steam_client._session.get("https://steamcommunity.com/dev/apikey")
    soup = BeautifulSoup(resp.text, "html.parser")
    if soup.find(id="bodyContents_ex") is not None:
        api_key = soup.find(id="bodyContents_ex").find("p").text.split(" ")[-1]
        regex = re.compile(r"[a-zA-Z0-9]{32}")
        if regex.match(api_key):
            return api_key
    resp = steam_client._session.post(
        "https://steamcommunity.com/dev/requestkey",
        data={
            "domain": "localhost",
            "request_id": "0",
            "sessionid": steam_client._session.cookies.get_dict("steamcommunity.com")["sessionid"],
            "agreeToTerms": "true",
        },
    )
    resp_json = resp.json()
    if resp_json["success"] == 22:
        request_id = resp_json["request_id"]
        steam_client._confirm_transaction(resp_json["request_id"])
        time.sleep(2)  # wait for steam to process the request
        test_resp = steam_client._session.post(
            "https://steamcommunity.com/dev/requestkey",
            data={
                "domain": "localhost",
                "request_id": request_id,
                "sessionid": steam_client._session.cookies.get_dict("steamcommunity.com")["sessionid"],
                "agreeToTerms": "true",
            },
        )
        if test_resp.json()["success"] == 1:
            api_key = test_resp.json()["api_key"]
            return api_key
    soup = BeautifulSoup(resp.text, "html.parser")
    if soup.find(id="bodyContents_ex") is None:
        return ""
    api_key = soup.find(id="bodyContents_ex").find("p").text.split(" ")[-1]
    return api_key


def get_steam_64_id_from_steam_community(steam_client):
    resp = steam_client._session.get("https://steamcommunity.com/")
    soup = BeautifulSoup(resp.text, "html.parser")
    steam_user_json = soup.find(id="webui_config").get("data-userinfo")
    steam_user = json5.loads(steam_user_json)
    return str(steam_user["steamid"])


def login_to_steam():
    global config
    steam_client = None
    with open(STEAM_ACCOUNT_INFO_FILE_PATH, "r", encoding=get_encoding(STEAM_ACCOUNT_INFO_FILE_PATH)) as f:
        try:
            acc = json5.load(f)
            # convert to json
            acc = json.loads(json.dumps(acc))
            with open(STEAM_ACCOUNT_JSON_INFO_FILE_PATH, "w", encoding="utf-8") as f:
                f.write(json.dumps(acc, indent=4))
        except Exception as e:
            handle_caught_exception(e)
            logger.error("检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + "格式错误, 请检查配置文件格式是否正确! ")
            pause()
            return None
    steam_session_path = os.path.join(SESSION_FOLDER, acc.get("steam_username").lower() + ".pkl")
    if not os.path.exists(steam_session_path):
        logger.info("检测到首次登录Steam，正在尝试登录...登录完成后会自动缓存session")
    else:
        logger.info("检测到缓存的steam_session, 正在尝试登录...")
        try:
            with open(steam_session_path, "rb") as f:
                client = pickle.load(f)
                if config["steam_login_ignore_ssl_error"]:
                    logger.warning("警告: 已经关闭SSL验证, 请确保你的网络安全")
                    client._session.verify = False
                    requests.packages.urllib3.disable_warnings()
                else:
                    client._session.verify = True
                if config["steam_local_accelerate"]:
                    logger.info("已经启用Steamauto内置加速")
                    client._session.auth = accelerator()

                if client.is_session_alive():
                    logger.info("登录成功")
                    steam_client = client
        except requests.exceptions.ConnectionError as e:
            handle_caught_exception(e)
            logger.error("使用缓存的session登录失败!可能是网络异常")
            steam_client = None
        except EOFError as e:
            handle_caught_exception(e)
            shutil.rmtree(SESSION_FOLDER)
            steam_client = None
            logger.error("session文件异常.已删除session文件夹")
        except AssertionError as e:
            handle_caught_exception(e)
            if config["steam_local_accelerate"]:
                logger.error("由于内置加速问题,暂时无法登录.请稍等10分钟后再进行登录,或者关闭内置加速功能！")
            else:
                logger.error("未知登录错误,可能是由于网络问题?")
    if steam_client is None:
        try:
            logger.info("正在登录Steam...")
            if "use_proxies" not in config:
                config["use_proxies"] = False
            if config["use_proxies"]:
                logger.info("已经启用Steam代理")
                if "proxies" not in config:
                    config["proxies"] = {}

                if not isinstance(config["proxies"], dict):
                    logger.error("proxies格式错误，请检查配置文件")
                    pause()
                    return None
                logger.info("正在检查代理服务器可用性...")
                proxy_status = ping_proxy(config["proxies"])
                if proxy_status is False:
                    logger.error("代理服务器不可用，请检查配置文件")
                    pause()
                    return None
                else:
                    # logger.info("代理服务器可用")
                    logger.warning("警告: 你已启用proxy, 该配置将被缓存，下次启动Steamauto时请确保proxy可用，或删除session文件夹下的缓存文件再启动")

                client = SteamClient(api_key="", proxies=config["proxies"])

            else:
                client = SteamClient(api_key="")
            if config["steam_login_ignore_ssl_error"]:
                logger.warning("警告: 已经关闭SSL验证, 请确保你的网络安全")
                client._session.verify = False
                requests.packages.urllib3.disable_warnings()
            if config["steam_local_accelerate"]:
                logger.info("已经启用Steamauto内置加速")
                client._session.auth = accelerator()
            logger.info("正在登录...")
            client.login(acc.get("steam_username"), acc.get("steam_password"), STEAM_ACCOUNT_JSON_INFO_FILE_PATH)
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
            handle_caught_exception(e)
            logger.error("未检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + ", 请添加到" + STEAM_ACCOUNT_INFO_FILE_PATH + "后再进行操作! ")
            pause()
            return None
        except (SSLCertVerificationError, SSLError) as e:
            handle_caught_exception(e)
            if config["steam_local_accelerate"]:
                logger.error("登录失败. 你开启了本地加速, 但是未关闭SSL证书验证. 请在配置文件中将steam_login_ignore_ssl_error设置为true")
            else:
                logger.error("登录失败. SSL证书验证错误! " "若您确定网络环境安全, 可尝试将配置文件中的steam_login_ignore_ssl_error设置为true\n")
            pause()
            return None
        except (requests.exceptions.ConnectionError, TimeoutError) as e:
            handle_caught_exception(e)
            logger.error(
                "网络错误! \n强烈建议使用Steamauto内置加速，仅需在配置文件中将steam_login_ignore_ssl_error和steam_local_accelerate设置为true即可使用 \n注意: 使用游戏加速器并不能解决问题. 请尝试使用Proxifier及其类似软件代理Python进程解决"
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
            logger.error("登录失败.可能原因如下：\n 1 代理问题，不建议同时开启proxy和内置代理，或者是代理波动，可以重试\n2 Steam服务器波动，无法登录")
            pause()
            return None
        except CaptchaRequired as e:
            handle_caught_exception(e)
            logger.error(
                "登录失败. 触发Steam风控, 请尝试更换加速器节点或使用手机热点等其它网络环境重试.\n"
                "强烈建议使用Steamauto内置加速，仅需在配置文件中将steam_login_ignore_ssl_error和steam_local_accelerate设置为true即可使用.\n"
                "这并不是一个程序问题, 请勿提交相关issue!(即使你已经开启Steamauto内置加速) "
            )
            pause()
            return None
        except InvalidCredentials as e:
            handle_caught_exception(e)
            logger.error("登录失败(账号或密码错误). 请检查" + STEAM_ACCOUNT_INFO_FILE_PATH + "中的账号密码是否正确\n")
    steam_client.steam_guard["steamid"] = str(get_steam_64_id_from_steam_community(steam_client))
    try:
        steam_client._api_key = get_api_key(steam_client)
    except Exception as e:
        handle_caught_exception(e)
        logger.error("获取API_KEY失败, 但由于现在完全没用到API_KEY, 所以不影响程序运行. 请忽略此错误!")
    return steam_client


# 文件缺失或格式错误返回0，首次运行返回1，非首次运行返回2
def init_files_and_params() -> int:
    global config
    development_mode = False
    logger.info("欢迎使用Steamauto Github仓库:https://github.com/jiajiaxd/Steamauto")
    logger.info("欢迎加入Steamauto 官方QQ群 群号: 425721057")
    logger.info("若您觉得Steamauto好用, 请给予Star支持, 谢谢! \n")
    logger.info("\033[1;31m！！！ 本程序完全\033[1;33m免费开源\033[1;31m，若有人向你售卖，请立即投诉并申请退款！！！ \033[0m\n")
    logger.info("\033[1;31m闲鱼 大学路蹦极选手 刘少魔帝 心如止水 蜜汁老八小憨包 请立刻停止倒卖\033[0m\n")
    logger.info(f"当前版本: {current_version}")
    logger.info("正在检查更新...")
    try:
        response_json = requests.get("https://steamauto.jiajiaxd.com/versions", timeout=5)
        data = response_json.json()
        latest_version = data["latest_version"]["version"]
        broadcast = data.get("broadcast", None)
        if broadcast:
            logger.info(f"公告: {broadcast}\n")
        if compare_version(current_version, latest_version) == -1:
            logger.info(f"检测到最新版本: {latest_version}")
            changelog_to_output = str()
            for version in data["history_versions"]:
                if compare_version(current_version, version["version"]) == -1:
                    changelog_to_output += f"版本: {version['version']}\n更新日志: {version['changelog']}\n\n"

            logger.info(f"\n{changelog_to_output}")
            logger.warning("当前版本不是最新版本,为了您的使用体验,请及时更新!")
        else:
            logger.info("当前版本已经是最新版本")
    except Exception as e:
        handle_caught_exception(e)
        logger.info("检查更新失败, 跳过检查更新")
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
                handle_caught_exception(e)
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


def get_plugins_enabled(steam_client, steam_client_mutex):
    global config
    plugins_enabled = []
    if (
        "buff_auto_accept_offer" in config
        and "enable" in config["buff_auto_accept_offer"]
        and config["buff_auto_accept_offer"]["enable"]
    ):
        buff_auto_accept_offer = BuffAutoAcceptOffer(logger, steam_client, steam_client_mutex, config)
        plugins_enabled.append(buff_auto_accept_offer)
    if (
        "buff_auto_comment" in config
        and "enable" in config["buff_auto_comment"]
        and config["buff_auto_comment"]["enable"]
    ):
        buff_auto_comment = BuffAutoComment(logger, steam_client, steam_client_mutex, config)
        plugins_enabled.append(buff_auto_comment)
    if (
        "buff_profit_report" in config
        and "enable" in config["buff_profit_report"]
        and config["buff_profit_report"]["enable"]
    ):
        buff_profit_report = BuffProfitReport(logger, steam_client, steam_client_mutex, config)
        plugins_enabled.append(buff_profit_report)
    if "buff_auto_on_sale" in config and "enable" in config["buff_auto_on_sale"] and config["buff_auto_on_sale"]["enable"]:
        buff_auto_on_sale = BuffAutoOnSale(logger, steam_client, steam_client_mutex, config)
        plugins_enabled.append(buff_auto_on_sale)
    if (
        "uu_auto_accept_offer" in config
        and "enable" in config["uu_auto_accept_offer"]
        and config["uu_auto_accept_offer"]["enable"]
    ):
        uu_auto_accept_offer = UUAutoAcceptOffer(logger, steam_client, steam_client_mutex, config)
        plugins_enabled.append(uu_auto_accept_offer)
    if (
        "steam_auto_accept_offer" in config
        and "enable" in config["steam_auto_accept_offer"]
        and config["steam_auto_accept_offer"]["enable"]
    ):
        steam_auto_accept_offer = SteamAutoAcceptOffer(logger, steam_client, steam_client_mutex, config)
        plugins_enabled.append(steam_auto_accept_offer)

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
        logger.warning("所有插件都已经退出！这不是一个正常情况，请检查配置文件.")


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
    plugins_enabled = get_plugins_enabled(steam_client, steam_client_mutex)
    # 检查插件是否正确初始化
    plugins_check_status = plugins_check(plugins_enabled)
    if plugins_check_status == 0:
        logger.info("存在插件首次运行, 请按照README提示填写配置文件! ")
        pause()
        return 1

    if steam_client is not None:
        init_plugins_and_start(steam_client, steam_client_mutex)

    logger.info("由于所有插件已经关闭,程序即将退出...")
    pause()
    sys.exit(exit_code.get())


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
    exit_code.set(main())
    if exit_code is not None:
        sys.exit(exit_code.get())
    else:
        sys.exit()
