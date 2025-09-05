import base64
import json
import os
import threading
import time
from datetime import datetime
from ssl import SSLCertVerificationError, SSLError

import json5
import requests
from requests.exceptions import RequestException

import steampy.exceptions
from steampy.client import SteamClient
from steampy.exceptions import ApiException
from steampy.models import GameOptions
from utils import static
from utils.logger import PluginLogger, handle_caught_exception
from utils.notifier import send_notification
from utils.static import SESSION_FOLDER, STEAM_ACCOUNT_INFO_FILE_PATH
from utils.tools import accelerator, get_encoding, logger, pause

logger = PluginLogger('SteamClient')

steam_client_mutex = threading.Lock()
steam_client = None


def _parse_jwt_exp(jwt_token: str) -> int:
    try:
        parts = jwt_token.split('.')
        if len(parts) != 3:
            return 0
        payload = parts[1]
        payload += '=' * (4 - len(payload) % 4)
        decoded_payload = base64.b64decode(payload)
        payload_data = json.loads(decoded_payload)
        
        return payload_data.get('exp', 0)
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.warning("解析JWT过期时间失败")
        return 0

def _get_token_cache_path(username: str) -> str:
    return os.path.join(SESSION_FOLDER, f"steam_account_{username.lower()}.json")


def _load_token_cache(username: str) -> dict:
    cache_path = _get_token_cache_path(username)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            handle_caught_exception(e, known=True)
            logger.warning(f"读取token缓存文件失败: {cache_path}")
    return {}


def _save_token_cache(username: str, steamid: str, refresh_token: str):
    cache_path = _get_token_cache_path(username)
    
    exp_timestamp = _parse_jwt_exp(refresh_token)
    
    cache_data = {
        "steamid": steamid,
        "refresh_token": refresh_token,
        "exp_timestamp": exp_timestamp
    }
    
    if exp_timestamp > 0:
        try:
            exp_datetime = datetime.fromtimestamp(exp_timestamp)
            cache_data["exp_readable"] = exp_datetime.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
        
        # 输出保存信息，包含过期时间
        if exp_timestamp > 0:
            exp_datetime = datetime.fromtimestamp(exp_timestamp)
            logger.info(f"已保存token缓存: {cache_path}, 过期时间: {exp_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            logger.info(f"已保存token缓存: {cache_path}")
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error(f"保存token缓存失败: {cache_path}")


def _setup_client_session(client: SteamClient, config: dict):
    if config["steam_login_ignore_ssl_error"]:
        logger.warning("警告: 已经关闭SSL验证, 请确保你的网络安全")
        client._session.verify = False
        requests.packages.urllib3.disable_warnings()  # type: ignore
    else:
        client._session.verify = True
        
    if config["steam_local_accelerate"]:
        logger.info("已经启用Steamauto内置加速")
        client._session.auth = accelerator()
        
    if config.get("use_proxies", False):
        client._session.proxies = config["proxies"]
        logger.info("已经启用Steam代理")


def _check_proxy_availability(config: dict) -> bool:
    if not config.get("use_proxies", False):
        return True
        
    if not isinstance(config["proxies"], dict):
        logger.error("proxies格式错误，请检查配置文件")
        return False
        
    logger.info("正在检查代理服务器可用性...")
    try:
        requests.get("https://steamcommunity.com", proxies=config["proxies"], timeout=10)
        logger.info("代理服务器可用")
        return True
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error("代理服务器不可用，请检查配置文件，或者将use_proxies配置项设置为false")
        return False


def login_to_steam(config: dict):
    global steam_client
    
    # 读取Steam账号信息
    steam_account_info = dict()
    with open(STEAM_ACCOUNT_INFO_FILE_PATH, "r", encoding=get_encoding(STEAM_ACCOUNT_INFO_FILE_PATH)) as f:
        try:
            steam_account_info = json5.loads(f.read())
        except Exception as e:
            handle_caught_exception(e, known=True)
            logger.error("检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + "格式错误, 请检查配置文件格式是否正确! ")
            pause()
            return None
    
    # 验证账号信息
    if not isinstance(steam_account_info, dict):
        logger.error("配置文件格式错误，请检查配置文件")
        return None
        
    for key, value in steam_account_info.items():
        if not value:
            logger.error(f"Steam账号配置文件中 {key} 为空，请检查配置文件")
            return None
    
    username = steam_account_info.get("steam_username", "")
    password = steam_account_info.get("steam_password", "")
    
    if not username or not password:
        logger.error("Steam用户名或密码为空，请检查配置文件")
        return None
    
    config["use_proxies"] = config.get("use_proxies", False)
    
    # 检查代理可用性
    if not _check_proxy_availability(config):
        pause()
        return None
    
    # 尝试使用refreshToken登录
    token_cache = _load_token_cache(username)
    if token_cache.get("refresh_token") and token_cache.get("steamid"):
        logger.info("检测到缓存的refreshToken, 正在检查过期时间...")
        
        # 检查JWT是否过期
        exp_timestamp = token_cache.get("exp_timestamp", 0)
        current_time = int(time.time())
        
        if exp_timestamp > 0:
            if current_time >= exp_timestamp:
                # Token已过期
                exp_datetime = datetime.fromtimestamp(exp_timestamp)
                logger.warning(f"refreshToken已于 {exp_datetime.strftime('%Y-%m-%d %H:%M:%S')} 过期，将回退到账密登录")
            else:
                # Token未过期，显示剩余时间
                exp_datetime = datetime.fromtimestamp(exp_timestamp)
                remaining_seconds = exp_timestamp - current_time
                remaining_hours = remaining_seconds // 3600
                remaining_days = remaining_hours // 24
                
                if remaining_days > 0:
                    logger.info(f"refreshToken预计将于 {exp_datetime.strftime('%Y-%m-%d %H:%M:%S')} 过期 (还有约{remaining_days}天)")
                elif remaining_hours > 0:
                    logger.info(f"refreshToken预计将于 {exp_datetime.strftime('%Y-%m-%d %H:%M:%S')} 过期 (还有约{remaining_hours}小时)")
                else:
                    remaining_minutes = remaining_seconds // 60
                    logger.info(f"refreshToken预计将于 {exp_datetime.strftime('%Y-%m-%d %H:%M:%S')} 过期 (还有约{remaining_minutes}分钟)")
                
                # 尝试使用refreshToken登录
                try:
                    # 创建客户端
                    if config.get("use_proxies", False):
                        client = SteamClient(api_key="", proxies=config["proxies"])
                    else:
                        client = SteamClient(api_key="")
                    
                    # 设置会话配置
                    _setup_client_session(client, config)
                    
                    # 尝试refreshToken登录
                    login_success = client.loginByRefreshToken(
                        token_cache["refresh_token"],
                        token_cache["steamid"],
                        steam_account_info
                    )
                    
                    if login_success and client.is_session_alive():
                        logger.info("使用refreshToken登录成功")
                        steam_client = client
                        static.STEAM_ACCOUNT_NAME = client.username or username
                        static.STEAM_64_ID = client.get_steam64id_from_cookies()
                        return steam_client
                    else:
                        logger.warning("refreshToken登录失败，将回退到账密登录")
                        
                except Exception as e:
                    handle_caught_exception(e, known=True)
                    logger.warning("refreshToken登录失败，将回退到账密登录")
        else:
            # 如果没有过期时间信息，仍然尝试登录
            logger.info("未找到refreshToken过期时间信息，尝试使用refreshToken登录...")
            try:
                # 创建客户端
                if config.get("use_proxies", False):
                    client = SteamClient(api_key="", proxies=config["proxies"])
                else:
                    client = SteamClient(api_key="")
                
                # 设置会话配置
                _setup_client_session(client, config)
                
                # 尝试refreshToken登录
                login_success = client.loginByRefreshToken(
                    token_cache["refresh_token"],
                    token_cache["steamid"],
                    steam_account_info
                )
                
                if login_success and client.is_session_alive():
                    logger.info("使用refreshToken登录成功")
                    steam_client = client
                    static.STEAM_ACCOUNT_NAME = client.username or username
                    static.STEAM_64_ID = client.get_steam64id_from_cookies()
                    return steam_client
                else:
                    logger.warning("refreshToken登录失败，将回退到账密登录")
                    
            except Exception as e:
                handle_caught_exception(e, known=True)
                logger.warning("refreshToken登录失败，将回退到账密登录")
    
    # 回退到账密登录
    logger.info("正在使用账密登录Steam...")
    try:
        # 创建客户端
        if config.get("use_proxies", False):
            client = SteamClient(api_key="", proxies=config["proxies"])
        else:
            client = SteamClient(api_key="")
        
        # 设置会话配置
        _setup_client_session(client, config)
        
        if config['use_proxies'] and config['steam_local_accelerate']:
            logger.warning('检测到你已经同时开启内置加速和代理功能！正常情况下不推荐通过这种方式使用软件')
        
        logger.info("正在登录...")
        
        # 执行登录
        login_result = client.login(
            username,
            password,
            steam_account_info,
        )
        
        if client.is_session_alive():
            logger.info("账密登录成功")
            
            # 保存新的token缓存
            if login_result and isinstance(login_result, dict):
                if login_result.get("refresh_token") and login_result.get("steamid"):
                    _save_token_cache(
                        username,
                        login_result["steamid"],
                        login_result["refresh_token"]
                    )
            
            steam_client = client
            static.STEAM_ACCOUNT_NAME = client.username
            static.STEAM_64_ID = client.get_steam64id_from_cookies()
            return steam_client
        else:
            logger.error("登录失败")
            return None
            
    except FileNotFoundError as e:
        handle_caught_exception(e, known=True)
        logger.error("未检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + ", 请添加到" + STEAM_ACCOUNT_INFO_FILE_PATH + "后再进行操作! ")
        pause()
        return None
    except (SSLCertVerificationError, SSLError) as e:
        handle_caught_exception(e, known=True)
        if config["steam_local_accelerate"]:
            logger.error("登录失败. 你开启了本地加速, 但是未关闭SSL证书验证. 请在配置文件中将steam_login_ignore_ssl_error设置为true")
        else:
            logger.error("登录失败. SSL证书验证错误! " "若您确定网络环境安全, 可尝试将配置文件中的steam_login_ignore_ssl_error设置为true\n")
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
        logger.error("登录失败.可能原因如下：\n 1 代理问题，不建议同时开启proxy和内置代理，或者是代理波动，可以重试\n2 Steam服务器波动，无法登录")
        pause()
        return None
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error("登录失败. 请检查" + STEAM_ACCOUNT_INFO_FILE_PATH + "的格式或内容是否正确!\n")
        pause()
        return None


def accept_trade_offer(client: SteamClient, mutex, tradeOfferId, retry=False, desc="", network_retry_count=0):
    max_network_retries = 3
    network_retry_delay = 5
    
    try:
        with mutex:
            client.accept_trade_offer(str(tradeOfferId))
        send_notification(f'报价号：{tradeOfferId}\n{desc}', title='接受报价成功')
        return True
    except Exception as e:
        if retry:
            logger.error(f"接受报价号{tradeOfferId}失败！")
            return False
            
        # 处理网络错误，允许重试
        if isinstance(e, RequestException):
            if network_retry_count < max_network_retries:
                logger.warning(f"接受报价号{tradeOfferId}遇到网络错误，正在重试 ({network_retry_count + 1}/{max_network_retries})...")
                handle_caught_exception(e, "SteamClient", known=True)
                time.sleep(network_retry_delay)
                return accept_trade_offer(client, mutex, tradeOfferId, retry=False, desc=desc, network_retry_count=network_retry_count + 1)
            else:
                logger.error(f"接受报价号{tradeOfferId}网络错误重试次数已达到上限({max_network_retries})，操作失败")
                handle_caught_exception(e, "SteamClient", known=True)
                send_notification(f'报价号：{tradeOfferId}\n{desc}', title='接受报价失败(网络错误)')
                return False
        
        relogin = False
        if isinstance(e, ValueError):
            if 'Accepted' in str(e):
                logger.warning(f'报价号 {tradeOfferId} 已经处理过，无需再次处理')
                handle_caught_exception(e, "SteamClient", known=True)
                return True
        if isinstance(e, steampy.exceptions.ConfirmationExpected) or isinstance(e, steampy.exceptions.InvalidCredentials):
            relogin = True
            handle_caught_exception(e, "SteamClient", known=True)
        if isinstance(e, KeyError):
            logger.error(f"接受报价号{tradeOfferId}失败！未找到报价号或报价号已过期")
            return False
        with mutex:
            try:
                if not client.is_session_alive():
                    relogin = True
                if relogin:
                    logger.warning("Steam会话已过期，正在尝试重新登录...")
                    relogin_result = client.relogin()
                    logger.info("重新登录成功")
                    
                    # 更新token缓存
                    if relogin_result and isinstance(relogin_result, dict):
                        if relogin_result.get("refresh_token") and relogin_result.get("steamid") and client.username:
                            _save_token_cache(
                                client.username,
                                relogin_result["steamid"],
                                relogin_result["refresh_token"]
                            )
                else:
                    handle_caught_exception(e, "SteamClient")
                    logger.error(f"接受报价号{tradeOfferId}失败！")
            except Exception as relogin_exception:
                handle_caught_exception(relogin_exception, "SteamClient")
                logger.error(f"接受报价号{tradeOfferId}失败！")

        if 'substring not found' in str(e):
            logger.error(f'由于Steam风控，报价号 {tradeOfferId} 处理失败，请检查IP/加速器/梯子')
            handle_caught_exception(e, "SteamClient", known=True)
            return False
        
        if relogin:
            logger.info("已经更新登录会话，正在重试接受报价号" + tradeOfferId)
            return accept_trade_offer(client, mutex, tradeOfferId, retry=True, desc=desc, network_retry_count=network_retry_count)
        
        send_notification(f'报价号：{tradeOfferId}\n{desc}', title='接受报价失败')
        return False


def get_cs2_inventory(client: SteamClient, mutex):
    inventory = None
    try:
        with mutex:
            inventory = client.get_my_inventory(game=GameOptions.CS)  # type: ignore
            logger.log(5, '获取到的Steam库存:' + json.dumps(inventory, ensure_ascii=False))
    except Exception as e:
        handle_caught_exception(e, "SteamClient", known=True)
        send_notification('获取库存失败，请检查服务器网络', title='获取库存失败')
    return inventory
