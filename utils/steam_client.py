import json
import os
import pickle
import shutil
import threading
from ssl import SSLCertVerificationError, SSLError

import json5
import requests

import steampy.exceptions
from steampy.client import SteamClient
from steampy.exceptions import ApiException
from steampy.models import GameOptions
from utils.logger import PluginLogger, handle_caught_exception
from utils.static import SESSION_FOLDER, STEAM_ACCOUNT_INFO_FILE_PATH
from utils.tools import accelerator, get_encoding, logger, pause

try:
    from steampy.utils import ping_proxy  # type: ignore
except:

    def ping_proxy(nothing):
        return False


logger = PluginLogger('SteamClient')

steam_client_mutex = threading.Lock()
steam_client = None


def login_to_steam(config: dict):
    global steam_client
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
                steam_account_info,
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


def accept_trade_offer(client: SteamClient, mutex, tradeOfferId, retry=False):
    try:
        with mutex:
            client.accept_trade_offer(str(tradeOfferId))
        return True
    except Exception as e:
        if retry:
            logger.error(f"接受报价号{tradeOfferId}失败！")
            return False
        handle_caught_exception(e, "SteamClient", known=True)
        relogin = False
        if isinstance(e, steampy.exceptions.ConfirmationExpected) or isinstance(e, steampy.exceptions.InvalidCredentials):
            relogin = True
        with mutex:
            try:
                if (not client.is_session_alive()) or relogin:
                    logger.warning("Steam会话已过期，正在尝试重新登录...")
                    client.relogin()
                    logger.info("重新登录成功")
                    steam_session_path = os.path.join(SESSION_FOLDER, client.username.lower() + ".pkl")
                    with open(steam_session_path, "wb") as f:
                        pickle.dump(client, f)
                    logger.info("已经更新登录会话，正在重试接受报价号" + tradeOfferId)
                    return accept_trade_offer(client, mutex, tradeOfferId, retry=True)
            except Exception as e:
                logger.error(f"接受报价号{tradeOfferId}失败！")
        return False

def get_cs2_inventory(client: SteamClient, mutex):
    try:
        with mutex:
            inventory = client.get_my_inventory(game=GameOptions.CS)  # type: ignore
            logger.log(5, '获取到的Steam库存:' + json.dumps(inventory, ensure_ascii=False))
    except Exception as e:
        handle_caught_exception(e, "SteamClient", known=True)
    return inventory