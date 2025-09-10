import base64
import json
import os
import threading
import time
from datetime import datetime
from ssl import SSLCertVerificationError, SSLError
from typing import Optional, Dict, Any

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
from utils.tools import accelerator, get_encoding, pause

logger = PluginLogger('SteamClient')

steam_client_mutex = threading.Lock()
steam_client: Optional[SteamClient] = None
token_refresh_thread = None  # 后台刷新线程引用

# ================= JWT 解析与缓存辅助 ===================

def _parse_jwt_exp(jwt_token: Optional[str]) -> int:
    if not jwt_token:
        return 0
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

def _save_token_cache(username: str, auth_info: Dict[str, Any]):
    """
    auth_info 期望结构:
    {
        steamid: str,
        access_token: Optional[str],
        refresh_token: Optional[str]
    }
    """
    cache_path = _get_token_cache_path(username)
    steamid = auth_info.get("steamid")
    access_token = auth_info.get("access_token")
    refresh_token = auth_info.get("refresh_token")

    access_exp = _parse_jwt_exp(access_token)
    refresh_exp = _parse_jwt_exp(refresh_token)

    cache_data = {
        "steamid": steamid,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_token_exp_timestamp": access_exp,
        "refresh_token_exp_timestamp": refresh_exp,
    }

    # 友好可读时间
    try:
        if access_exp:
            cache_data["access_token_exp_readable"] = datetime.fromtimestamp(access_exp).strftime("%Y-%m-%d %H:%M:%S")
        if refresh_exp:
            cache_data["refresh_token_exp_readable"] = datetime.fromtimestamp(refresh_exp).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
        logger.info("已保存token缓存: %s", cache_path)
        if access_exp:
            logger.info(" access_token 过期时间: %s", cache_data.get("access_token_exp_readable"))
        if refresh_exp:
            logger.info(" refresh_token 过期时间: %s", cache_data.get("refresh_token_exp_readable"))
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error(f"保存token缓存失败: {cache_path}")

# ================== 会话与代理设置 ======================

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

# ================== 后台刷新线程 ========================

class TokenRefreshThread(threading.Thread):
    """
    后台维护 access_token / refresh_token
    策略:
      - 每次循环检查距离 access_token 过期时间
      - 距离过期 < 3600 秒则尝试刷新 (loginByRefreshToken)
      - 如果 session 失效或刷新失败 -> relogin()
      - 若完全失败 -> 发送通知
    """
    def __init__(self, username: str, config: dict):
        super().__init__(daemon=True)
        self.username = username
        self.config = config
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.is_set():
            try:
                self._refresh_cycle()
            except Exception as e:
                handle_caught_exception(e, known=True)
                logger.error("后台Token刷新循环出现异常")
            # 计算下一次检查间隔
            wait_seconds = self._compute_wait_interval()
            self.stop_event.wait(wait_seconds)

    def _compute_wait_interval(self) -> int:
        """
        基于缓存中 access_token 过期时间决定下一次检查:
          - 距离过期 > 6h: 3h 后检查
          - 距离过期 1h~6h: 1h 后检查
          - 距离过期 < 1h: 10 分钟后检查
          - 没有过期信息: 默认 6 小时
        """
        try:
            cache = _load_token_cache(self.username)
            exp = cache.get("access_token_exp_timestamp", 0)
            if not exp:
                return 6 * 3600
            now = int(time.time())
            remain = exp - now
            if remain <= 0:
                return 300  # 已过期, 5分钟后再试(避免频密)
            if remain > 6 * 3600:
                return 3 * 3600
            if remain > 3600:
                return 3600
            return 600
        except Exception:
            return 6 * 3600

    def _refresh_cycle(self):
        try:
            global steam_client
            with steam_client_mutex:
                if not steam_client:
                    return
                # 如果会话还活着且 access_token 也未临期则直接返回
                cache = _load_token_cache(self.username)
                access_exp = cache.get("access_token_exp_timestamp", 0)
                now = int(time.time())
                need_refresh = False
                if access_exp and access_exp - now < 3600:  # 1 小时内过期
                    need_refresh = True

                if not steam_client.is_session_alive():
                    logger.info("检测到会话已失效, 尝试刷新会话...")
                    # 优先使用 refresh_token
                    cache = _load_token_cache(self.username)
                    refresh_token = cache.get("refresh_token")
                    steamid = cache.get("steamid")
                    if refresh_token and steamid:
                        logger.info("尝试使用 refresh_token 刷新 access_token...")
                        try:
                            auth_info = steam_client.loginByRefreshToken(refresh_token, steamid, steam_client.steam_guard)
                            if auth_info and isinstance(auth_info, dict):
                                _save_token_cache(self.username, auth_info)
                                logger.info("后台 refresh_token 刷新成功")
                                return
                            else:
                                raise Exception("loginByRefreshToken 未返回有效 auth_info")
                        except Exception as e:
                            handle_caught_exception(e, known=True)
                            logger.warning("使用 refresh_token 刷新失败: %s", e)
                    # refresh_token 失败后回退 relogin
                    logger.info("refresh_token 刷新失败或不可用, 尝试使用账密重新登录...")
                    try:
                        auth_info = steam_client.relogin()
                        if auth_info and isinstance(auth_info, dict):
                            _save_token_cache(self.username, auth_info)
                            logger.info("使用账密重新登录成功")
                            return
                        else:
                            raise Exception("relogin 未返回有效 auth_info")
                    except Exception as e:
                        handle_caught_exception(e, known=True)
                        logger.error("会话失效，刷新失败")
                        send_notification("Steam 会话刷新失败", "会话失效后 refresh_token 与重登录均失败，请检查账号或网络")
                        return

                if need_refresh:
                    # 使用 refresh_token 刷新
                    cache = _load_token_cache(self.username)
                    refresh_token = cache.get("refresh_token")
                    steamid = cache.get("steamid")
                    if refresh_token and steamid:
                        logger.info("尝试使用 refresh_token 刷新 access_token...")
                        try:
                            auth_info = steam_client.loginByRefreshToken(refresh_token, steamid, steam_client.steam_guard)
                            if auth_info and isinstance(auth_info, dict):
                                _save_token_cache(self.username, auth_info)
                                logger.info("后台 refresh_token 刷新成功")
                                return
                            else:
                                raise Exception("loginByRefreshToken 未返回有效 auth_info")
                        except Exception as e:
                            handle_caught_exception(e, known=True)
                            logger.warning("使用 refresh_token 刷新失败: %s", e)

                    # 再次尝试 relogin
                    try:
                        auth_info = steam_client.relogin()
                        if auth_info and isinstance(auth_info, dict):
                            _save_token_cache(self.username, auth_info)
                            logger.info("relogin 成功(刷新阶段)")
                            return
                    except Exception as e:
                        handle_caught_exception(e, known=True)

                    logger.error("后台刷新失败，无法延长会话")
                    send_notification("Steam 会话维持失败", "自动刷新与重登录均失败，请检查账号或网络")
        except requests.exceptions.RequestException:
            logger.error('无法检查Steam会话状态，请检查网络连接或代理设置')
        except Exception as e:
            handle_caught_exception(e, known=False)

    def stop(self):
        self.stop_event.set()

# ================== 登录主流程 ==========================

def login_to_steam(config: dict):
    """
    登录策略 (优先级):
    1) 缓存的 access_token (未过期)
    2) refresh_token 登录
    3) 账密登录
    """
    global steam_client, token_refresh_thread

    # 读取Steam账号信息
    try:
        with open(STEAM_ACCOUNT_INFO_FILE_PATH, "r", encoding=get_encoding(STEAM_ACCOUNT_INFO_FILE_PATH)) as f:
            try:
                steam_account_info = json5.loads(f.read())
            except Exception as e:
                handle_caught_exception(e, known=True)
                logger.error("检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + "格式错误, 请检查配置文件格式是否正确! ")
                pause()
                return None
    except FileNotFoundError:
        logger.error("未检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + ", 请添加后再进行操作!")
        pause()
        return None

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
    if not _check_proxy_availability(config):
        pause()
        return None

    token_cache = _load_token_cache(username)
    now = int(time.time())

    # 1. 尝试使用缓存 access_token
    access_token = token_cache.get("access_token")
    access_exp = token_cache.get("access_token_exp_timestamp", 0)
    steamid_cache = token_cache.get("steamid")
    if access_token and steamid_cache and access_exp and access_exp - now > 60:
        logger.info("检测到缓存的未过期 access_token, 尝试直接恢复会话...")
        try:
            if config.get("use_proxies", False):
                client = SteamClient(api_key="", proxies=config["proxies"])
            else:
                client = SteamClient(api_key="")
            _setup_client_session(client, config)
            if client.set_and_verify_access_token(steamid_cache, access_token, steam_account_info):
                logger.info("使用缓存 access_token 登录成功")
                steam_client = client
                static.STEAM_ACCOUNT_NAME = client.username or username
                static.STEAM_64_ID = client.get_steam64id_from_cookies()
                # 启动刷新线程
                if token_refresh_thread is None or not token_refresh_thread.is_alive():
                    _start_token_refresh_thread(username, config)
                return steam_client
            else:
                logger.warning("缓存 access_token 已失效，进入 refresh_token 流程")
        except Exception as e:
            handle_caught_exception(e, known=True)
            logger.warning("使用缓存 access_token 恢复失败")

    # 2. 尝试 refresh_token 登录
    refresh_token = token_cache.get("refresh_token")
    refresh_exp = token_cache.get("refresh_token_exp_timestamp", 0)
    if refresh_token and steamid_cache:
        if refresh_exp and refresh_exp <= now:
            logger.warning("refresh_token 已过期，将回退到账密登录")
        else:
            remaining = refresh_exp - now if refresh_exp else None
            if remaining:
                hours = remaining // 3600
                if hours > 0:
                    logger.info(f"refresh_token 预计还有 ~{hours} 小时过期")
            logger.info("尝试使用 refresh_token 登录...")
            try:
                if config.get("use_proxies", False):
                    client = SteamClient(api_key="", proxies=config["proxies"])
                else:
                    client = SteamClient(api_key="")
                _setup_client_session(client, config)
                auth_info = client.loginByRefreshToken(refresh_token, steamid_cache, steam_account_info)
                if auth_info and client.is_session_alive():
                    logger.info("使用 refresh_token 登录成功")
                    steam_client = client
                    _save_token_cache(username, auth_info)
                    static.STEAM_ACCOUNT_NAME = client.username or username
                    static.STEAM_64_ID = client.get_steam64id_from_cookies()
                    if token_refresh_thread is None or not token_refresh_thread.is_alive():
                        _start_token_refresh_thread(username, config)
                    return steam_client
                else:
                    logger.warning("refresh_token 登录失败，将回退到账密登录")
            except Exception as e:
                handle_caught_exception(e, known=True)
                logger.warning("refresh_token 登录失败，将回退到账密登录")

    # 3. 账密登录
    logger.info("正在使用账密登录Steam...")
    try:
        if config.get("use_proxies", False):
            client = SteamClient(api_key="", proxies=config["proxies"])
        else:
            client = SteamClient(api_key="")
        _setup_client_session(client, config)
        if config['use_proxies'] and config['steam_local_accelerate']:
            logger.warning('检测到你已经同时开启内置加速和代理功能！正常情况下不推荐通过这种方式使用软件')
        logger.info("正在登录...")
        auth_info = client.login(username, password, steam_account_info)
        if client.is_session_alive():
            logger.info("账密登录成功")
            steam_client = client
            if auth_info and isinstance(auth_info, dict):
                _save_token_cache(username, auth_info)
            static.STEAM_ACCOUNT_NAME = client.username
            static.STEAM_64_ID = client.get_steam64id_from_cookies()
            if token_refresh_thread is None or not token_refresh_thread.is_alive():
                _start_token_refresh_thread(username, config)
            return steam_client
        else:
            logger.error("登录失败")
            return None
    except FileNotFoundError as e:
        handle_caught_exception(e, known=True)
        logger.error("未检测到" + STEAM_ACCOUNT_INFO_FILE_PATH + ", 请添加后再进行操作! ")
        pause()
        return None
    except (SSLCertVerificationError, SSLError):
        if config["steam_local_accelerate"]:
            logger.error("登录失败. 你开启了本地加速, 但是未关闭SSL证书验证. 请在配置文件中将steam_login_ignore_ssl_error设置为true")
        else:
            logger.error("登录失败. SSL证书验证错误! 若您确定网络环境安全, 可尝试将配置文件中的steam_login_ignore_ssl_error设置为true\n")
        pause()
        return None
    except (requests.exceptions.ConnectionError, TimeoutError):
        logger.error(
            "网络错误! \n强烈建议使用Steamauto内置加速，仅需在配置文件中将steam_login_ignore_ssl_error和steam_local_accelerate设置为true即可使用 \n注意: 使用游戏加速器并不能解决问题，请使用代理软件如Clash/Proxifier等"
        )
        pause()
        return None
    except (ValueError, ApiException):
        logger.error("登录失败. 请检查" + STEAM_ACCOUNT_INFO_FILE_PATH + "的格式或内容是否正确!\n")
        pause()
        return None
    except (TypeError, AttributeError):
        logger.error("登录失败.可能原因如下：\n 1 代理问题，不建议同时开启proxy和内置代理，或者是代理波动，可以重试\n2 Steam服务器波动，无法登录")
        pause()
        return None
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error("登录失败. 请检查" + STEAM_ACCOUNT_INFO_FILE_PATH + "的格式或内容是否正确!\n")
        pause()
        return None

def _start_token_refresh_thread(username: str, config: dict):
    global token_refresh_thread
    try:
        token_refresh_thread = TokenRefreshThread(username, config)
        token_refresh_thread.start()
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error("启动 TokenRefreshThread 失败")

# ================== 业务操作 ===========================

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

        # 网络错误重试
        if isinstance(e, RequestException):
            if network_retry_count < max_network_retries:
                logger.warning(f"接受报价号{tradeOfferId}遇到网络错误，正在重试 ({network_retry_count + 1}/{max_network_retries})...")
                handle_caught_exception(e, "SteamClient", known=True)
                time.sleep(network_retry_delay)
                return accept_trade_offer(
                    client, mutex, tradeOfferId, retry=False, desc=desc, network_retry_count=network_retry_count + 1
                )
            else:
                logger.error(f"接受报价号{tradeOfferId}网络错误重试次数已达到上限({max_network_retries})，操作失败")
                handle_caught_exception(e, "SteamClient", known=True)
                send_notification(f'报价号：{tradeOfferId}\n{desc}', title='接受报价失败(网络错误)')
                return False

        if isinstance(e, ValueError):
            if 'Accepted' in str(e):
                logger.warning(f'报价号 {tradeOfferId} 已经处理过，无需再次处理')
                handle_caught_exception(e, "SteamClient", known=True)
                return True
        if isinstance(e, (steampy.exceptions.ConfirmationExpected, steampy.exceptions.InvalidCredentials)):
            logger.error(f"接受报价号{tradeOfferId}失败：会话或凭据无效，放弃本次处理")
            handle_caught_exception(e, "SteamClient", known=True)
            send_notification(f'报价号：{tradeOfferId}\n{desc}', title='接受报价失败(会话无效)')
            return False
        if isinstance(e, KeyError):
            logger.error(f"接受报价号{tradeOfferId}失败！未找到报价号或报价号已过期")
            return False

        # 其它错误统一处理
        handle_caught_exception(e, "SteamClient")
        logger.error(f"接受报价号{tradeOfferId}失败！")

        if 'substring not found' in str(e):
            logger.error(f'由于Steam风控，报价号 {tradeOfferId} 处理失败，请检查IP/加速器/梯子')
            handle_caught_exception(e, "SteamClient", known=True)
            return False


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
