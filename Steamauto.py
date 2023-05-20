import datetime
import logging
import shutil
import sys
import json
import threading
import pickle
import signal
import time

from steampy.client import SteamClient
from steampy.exceptions import CaptchaRequired, ApiException
from requests.exceptions import SSLError
import requests
import colorlog

from plugins.BuffAutoAcceptOffer import BuffAutoAcceptOffer
from plugins.BuffAutoOnSale import BuffAutoOnSale
from plugins.UUAutoAcceptOffer import UUAutoAcceptOffer
from plugins.SteamAutoAcceptOffer import SteamAutoAcceptOffer
from utils.static import *

config = {}


def pause():
    if 'no_pause' in config and not config['no_pause']:
        logger.info('点击回车键继续...')
        input()


def login_to_steam():
    global config
    steam_client = None
    if not os.path.exists(STEAM_SESSION_PATH):
        logger.info('检测到首次登录Steam，正在尝试登录...登录完成后会自动缓存session')
    else:
        logger.info('检测到缓存的steam_session, 正在尝试登录...')
        try:
            with open(STEAM_SESSION_PATH, 'rb') as f:
                client = pickle.load(f)
                if config['steam_login_ignore_ssl_error']:
                    logger.warning('警告: 已经关闭SSL验证, 账号可能存在安全问题')
                    client._session.verify = False
                    requests.packages.urllib3.disable_warnings()
                else:
                    client._session.verify = True

                if client.is_session_alive():
                    logger.info('登录成功')
                    steam_client = client
        except requests.exceptions.ConnectionError:
            logger.error('使用缓存的session登录失败!可能是网络异常.')
            steam_client = None
        except EOFError:
            shutil.rmtree(SESSION_FOLDER)
            steam_client = None
    if steam_client is None:
        try:
            logger.info('正在登录Steam...')
            with open(STEAM_ACCOUNT_INFO_FILE_PATH, 'r', encoding='utf-8') as f:
                acc = json.load(f)
            client = SteamClient(acc.get('api_key'))
            if config['steam_login_ignore_ssl_error']:
                logger.warning('警告: 已经关闭SSL验证, 账号可能存在安全问题')
                client._session.verify = False
                requests.packages.urllib3.disable_warnings()
            SteamClient.login(client, acc.get('steam_username'), acc.get('steam_password'),
                              STEAM_ACCOUNT_INFO_FILE_PATH)
            with open(STEAM_SESSION_PATH, 'wb') as f:
                pickle.dump(client, f)
            logger.info('登录完成! 已经自动缓存session.')
            steam_client = client
        except FileNotFoundError:
            logger.error('未检测到' + STEAM_ACCOUNT_INFO_FILE_PATH + ', 请添加到'
                         + STEAM_ACCOUNT_INFO_FILE_PATH + '后再进行操作! ')
            pause()
            sys.exit()
        except (requests.exceptions.ConnectionError, TimeoutError):
            logger.error('\n网络错误! 请通过修改hosts/使用代理等方法代理Python解决问题. \n'
                         '注意: 使用游戏加速器并不能解决问题. 请尝试使用Proxifier及其类似软件代理Python.exe解决. ')
            pause()
            sys.exit()
        except SSLError:
            logger.error('登录失败. SSL证书验证错误! '
                         '若您确定网络环境安全, 可尝试将config.json中的steam_login_ignore_ssl_error设置为true\n')
            pause()
            sys.exit()
        except (ValueError, ApiException):
            logger.error('登录失败. 请检查' + STEAM_ACCOUNT_INFO_FILE_PATH + '的格式或内容是否正确!\n')
            pause()
            sys.exit()
        except CaptchaRequired:
            logger.error('登录失败. 触发Steam风控, 请尝试更换加速器节点.\n'
                         '若您不知道该使用什么加速器，推荐使用 Watt Toolkit 自带的免费Steam加速(请开启hosts代理模式).')
            pause()
            sys.exit()
    return steam_client


def main():
    global config
    development_mode = False
    logger.info('欢迎使用Steamauto Github仓库:https://github.com/jiajiaxd/Steamauto')
    logger.info('若您觉得Steamauto好用, 请给予Star支持, 谢谢! ')
    logger.info('正在检查更新...')
    try:
        response_json = requests.get('https://buffbot.jiajiaxd.com/latest', timeout=5)
        data = response_json.json()
        logger.info(f"\n最新版本日期: {data['date']}\n{data['message']}\n请自行检查是否更新! ")
    except requests.exceptions.Timeout:
        logger.info('检查更新超时, 跳过检查更新')
    logger.info('正在初始化...')
    if not os.path.exists(CONFIG_FILE_PATH):
        if not os.path.exists(EXAMPLE_CONFIG_FILE_PATH):
            logger.error(
                '未检测到' + EXAMPLE_CONFIG_FILE_PATH + ', 请前往GitHub进行下载, 并保证文件和程序在同一目录下. ')
            pause()
            sys.exit()
        shutil.copy(EXAMPLE_CONFIG_FILE_PATH, CONFIG_FILE_PATH)
        logger.info('检测到首次运行, 已为您生成' + CONFIG_FILE_PATH + ', 请按照README提示填写配置文件! ')
        pause()
    with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
    if not os.path.exists(STEAM_ACCOUNT_INFO_FILE_PATH):
        with open(STEAM_ACCOUNT_INFO_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'steamid': '', 'shared_secret': '', 'identity_secret': '', 'api_key': '',
                                'steam_username': '', 'steam_password': ''}, indent=4))
            logger.info(
                '检测到首次运行, 已为您生成' + STEAM_ACCOUNT_INFO_FILE_PATH + ', 请按照README提示填写配置文件! ')
    if 'development_mode' in config and config['development_mode']:
        development_mode = True
    if development_mode:
        logger.info('开发者模式已开启')
    steam_client = None
    if development_mode:
        logger.info('开发者模式已开启, 跳过Steam登录')
    else:
        steam_client = login_to_steam()
    plugins_enabled = []
    if 'buff_auto_accept_offer' in config and 'enable' in config['buff_auto_accept_offer'] and \
            config['buff_auto_accept_offer']['enable']:
        buff_auto_accept_offer = BuffAutoAcceptOffer(logger, steam_client, config)
        plugins_enabled.append(buff_auto_accept_offer)
    if 'buff_auto_on_sale' in config and 'enable' in config['buff_auto_on_sale'] and \
            config['buff_auto_on_sale']['enable']:
        buff_auto_on_sale = BuffAutoOnSale(logger, steam_client, config)
        plugins_enabled.append(buff_auto_on_sale)
    if 'uu_auto_accept_offer' in config and 'enable' in config['uu_auto_accept_offer'] and \
            config['uu_auto_accept_offer']['enable']:
        uu_auto_accept_offer = UUAutoAcceptOffer(logger, steam_client, config)
        plugins_enabled.append(uu_auto_accept_offer)
    if 'steam_auto_accept_offer' in config and 'enable' in config['steam_auto_accept_offer'] and \
            config['steam_auto_accept_offer']['enable']:
        steam_auto_accept_offer = SteamAutoAcceptOffer(logger, steam_client, config)
        plugins_enabled.append(steam_auto_accept_offer)
    if len(plugins_enabled) == 0:
        logger.error('未启用任何插件, 请检查' + CONFIG_FILE_PATH + '是否正确! ')
        pause()
        sys.exit()
    first_run = False
    for plugin in plugins_enabled:
        if plugin.init():
            first_run = True
    if first_run:
        logger.info('首次运行, 请按照README提示填写配置文件! ')
        pause()
        sys.exit(0)
    logger.info('初始化完成, 开始运行插件!')
    print('\n')
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
    logger.info('正在退出...')
    sys.exit()


if __name__ == '__main__':
    signal.signal(signal.SIGINT, exit_app)
    logger = logging.getLogger('Steamauto')
    logger.setLevel(logging.DEBUG)
    s_handler = logging.StreamHandler()
    s_handler.setLevel(logging.INFO)
    log_formatter = colorlog.ColoredFormatter(fmt='%(log_color)s[%(asctime)s] - %(levelname)s: %(message)s',
                                              datefmt='%Y-%m-%d %H:%M:%S',
                                              log_colors={
                                                  'DEBUG': 'cyan',
                                                  'INFO': 'green',
                                                  'WARNING': 'yellow',
                                                  'ERROR': 'red',
                                                  'CRITICAL': 'bold_red'
                                              })
    s_handler.setFormatter(log_formatter)
    logger.addHandler(s_handler)
    if not os.path.exists(LOGS_FOLDER):
        os.mkdir(LOGS_FOLDER)
    f_handler = logging.FileHandler(os.path.join(LOGS_FOLDER, datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S') +
                                                 '.log'), encoding='utf-8')
    f_handler.setLevel(logging.DEBUG)
    f_handler.setFormatter(log_formatter)
    logger.addHandler(f_handler)
    if not os.path.exists(DEV_FILE_FOLDER):
        os.mkdir(DEV_FILE_FOLDER)
    if not os.path.exists(SESSION_FOLDER):
        os.mkdir(SESSION_FOLDER)
    main()
