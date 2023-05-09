# =======================================
# !!!屎山警告!!! 等待重写
# =======================================

import datetime
import logging
import os
import shutil
import sys
import json
import threading

import apprise
import uuyoupinapi
from apprise.AppriseAsset import *
from steampy.client import SteamClient
from steampy.exceptions import CaptchaRequired
from requests.exceptions import SSLError, ConnectTimeout
import requests
import time
from colorama import Fore
import pickle

from plugins.BuffAutoAcceptOffer import BuffAutoAcceptOffer
from plugins.UUAutoAcceptOffer import UUAutoAcceptOffer
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
        logger.info('未检测到' + STEAM_SESSION_PATH + '文件存在')
    else:
        logger.info('检测到缓存的' + STEAM_SESSION_PATH + '文件存在, 正在尝试登录')
        with open(STEAM_SESSION_PATH, 'rb') as f:
            client = pickle.load(f)
            if config['ignoreSSLError']:
                logger.warning(Fore.YELLOW + '警告: 已经关闭SSL验证, 账号可能存在安全问题' + Fore.RESET)
                client._session.verify = False
                requests.packages.urllib3.disable_warnings()
            else:
                client._session.verify = True
            if client.is_session_alive():
                logger.info('登录成功\n')
                steam_client = client
    if steam_client is None:
        try:
            logger.info('正在登录Steam...')
            with open(STEAM_ACCOUNT_INFO_FILE_PATH, 'r', encoding='utf-8') as f:
                acc = json.load(f)
            client = SteamClient(acc.get('api_key'))
            if config['ignoreSSLError']:
                logger.warning(Fore.YELLOW + '\n警告: 已经关闭SSL验证, 账号可能存在安全问题\n' + Fore.RESET)
                client._session.verify = False
                requests.packages.urllib3.disable_warnings()
            SteamClient.login(client, acc.get('steam_username'), acc.get('steam_password'),
                              STEAM_ACCOUNT_INFO_FILE_PATH)
            with open('steam_session.pkl', 'wb') as f:
                pickle.dump(client, f)
            logger.info('登录完成! 已经自动缓存session.\n')
            steam_client = client
        except FileNotFoundError:
            logger.error(Fore.RED + '未检测到' + STEAM_ACCOUNT_INFO_FILE_PATH + ', 请添加到' + STEAM_ACCOUNT_INFO_FILE_PATH +
                         '后再进行操作! ' + Fore.RESET)
            pause()
            sys.exit()
        except (ConnectTimeout, TimeoutError):
            logger.error(Fore.RED + '\n网络错误! 请通过修改hosts/使用代理等方法代理Python解决问题. \n'
                                    '注意: 使用游戏加速器并不能解决问题. 请尝试使用Proxifier及其类似软件代理Python.exe解决. ' + Fore.RESET)
            pause()
            sys.exit()
        except SSLError:
            logger.error(Fore.RED + '登录失败. SSL证书验证错误! '
                                    '若您确定网络环境安全, 可尝试将config.json中的ignoreSSLError设置为true\n' + Fore.RESET)
            pause()
            sys.exit()
        except CaptchaRequired:
            logger.error(Fore.RED + '登录失败. 触发Steam风控, 请尝试更换加速器节点. \n' + Fore.RESET)
            pause()
            sys.exit()
    return steam_client


def main():
    global config
    development_mode = False
    logger.info('欢迎使用Buff-Bot Github仓库:https://github.com/jiajiaxd/Buff-Bot')
    logger.info('若您觉得Buff-Bot好用, 请给予Star支持, 谢谢! ')
    logger.info('正在检查更新...')
    try:
        response_json = requests.get('https://buffbot.jiajiaxd.com/latest', timeout=5)
        data = response_json.json()
        logger.info(f"最新版本日期: {data['date']}\n内容: {data['message']}\n请自行检查是否更新! ")
    except requests.exceptions.Timeout:
        logger.info('检查更新超时, 跳过检查更新')
    logger.info('正在初始化...')
    if not os.path.exists(CONFIG_FILE_PATH):
        if not os.path.exists(EXAMPLE_CONFIG_FILE_PATH):
            logger.error('未检测到' + EXAMPLE_CONFIG_FILE_PATH + ', 请前往GitHub进行下载, 并保证文件和程序在同一目录下. ')
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
            logger.info('检测到首次运行, 已为您生成' + STEAM_ACCOUNT_INFO_FILE_PATH + ', 请按照README提示填写配置文件! ')
    if 'dev' in config and config['dev']:
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
    if 'uu_auto_accept_offer' in config and 'enable' in config['uu_auto_accept_offer'] and \
            config['uu_auto_accept_offer']['enable']:
        uu_auto_accept_offer = UUAutoAcceptOffer(logger, steam_client, config)
        plugins_enabled.append(uu_auto_accept_offer)
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
    logger.info('初始化完成, 开始运行插件')
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


if __name__ == '__main__':
    logger = logging.getLogger('Buff-Bot')
    logger.setLevel(logging.DEBUG)
    s_handler = logging.StreamHandler()
    s_handler.setLevel(logging.INFO)
    log_formatter = logging.Formatter('[%(asctime)s] - %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    s_handler.setFormatter(log_formatter)
    logger.addHandler(s_handler)
    if not os.path.exists(LOGS_FOLDER):
        os.mkdir(LOGS_FOLDER)
    f_handler = logging.FileHandler(os.path.join(LOGS_FOLDER, datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S') +
                                                 '.log') , encoding='utf-8')
    f_handler.setLevel(logging.DEBUG)
    f_handler.setFormatter(log_formatter)
    logger.addHandler(f_handler)
    if not os.path.exists(DEV_FILE_FOLDER):
        os.mkdir(DEV_FILE_FOLDER)
    if not os.path.exists(SESSION_FOLDER):
        os.mkdir(SESSION_FOLDER)
    main()
