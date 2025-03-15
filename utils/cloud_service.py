import os
import platform
import signal
import sys
import threading
import time
import uuid

import requests
from colorama import Fore, Style

import utils.static as static
from utils.logger import PluginLogger, handle_caught_exception
from utils.notifier import send_notification
from utils.tools import calculate_sha256, pause

logger = PluginLogger('CloudService')



def get_platform_info():
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        if machine == "amd64":
            return "windows x64"
        else:
            return f"windows {machine}"
    elif system == "linux":
        if machine == "x86_64":
            return "linux x64"
        else:
            return f"linux {machine}"
    elif system == "darwin":
        if machine == "x86_64" or machine == "arm64":
            return "mac x64" if machine == "x86_64" else "mac arm64"
        else:
            return f"mac {machine}"
    else:
        return f"{system} {machine}"


def get_user_uuid():
    app_dir = os.path.expanduser("~/.steamauto")
    uuid_file = os.path.join(app_dir, "uuid.txt")
    if not os.path.exists(app_dir):
        os.makedirs(app_dir)
    if not os.path.exists(uuid_file):
        with open(uuid_file, "w") as f:
            user_uuid = str(uuid.uuid4())
            f.write(user_uuid)
    else:
        with open(uuid_file, "r") as f:
            user_uuid = f.read().strip()
    return user_uuid


session = requests.Session()
session.headers.update({'User-Agent': f'Steamauto {static.CURRENT_VERSION} ({get_platform_info()}) {get_user_uuid()}'})


def compare_version(ver1, ver2):
    version1_parts = ver1.split(".")
    version2_parts = ver2.split(".")

    for i in range(max(len(version1_parts), len(version2_parts))):
        v1 = int(version1_parts[i]) if i < len(version1_parts) else 0
        v2 = int(version2_parts[i]) if i < len(version2_parts) else 0

        if v1 < v2:
            return -1
        elif v1 > v2:
            return 1

    return 0

def get_uu_uk_from_cloud():
    try:
        data = session.get('https://steamauto.jiajiaxd.com/tools/getUUuk').json()
        return data['uk']
    except Exception as e:
        logger.warning('云服务异常，无法获取UK，将使用默认配置')
        return ''


def parseBroadcastMessage(message):
    message = message.replace('<red>', Fore.RED)
    message = message.replace('<green>', Fore.GREEN)
    message = message.replace('<yellow>', Fore.YELLOW)
    message = message.replace('<blue>', Fore.BLUE)
    message = message.replace('<magenta>', Fore.MAGENTA)
    message = message.replace('<cyan>', Fore.CYAN)
    message = message.replace('<white>', Fore.WHITE)
    message = message.replace('<reset>', Style.RESET_ALL)
    message = message.replace('<bold>', Style.BRIGHT)
    message = message.replace('<br>', '\n')
    return message


def autoUpdate(downloadUrl, sha256=''):
    import tqdm

    try:
        with session.get(downloadUrl, stream=True, timeout=30) as response:
            response.raise_for_status()

            # 获取文件名
            content_disposition = response.headers.get('Content-Disposition')
            if content_disposition and 'filename=' in content_disposition:
                filename = content_disposition.split('filename=')[1].strip('\"')
            else:
                # 如果没有 Content-Disposition 头部，从 URL 中提取文件名
                filename = downloadUrl.split('/')[-1]
                if not filename.endswith('.exe'):
                    filename += '.exe'  # 确保文件是可执行的

            total_size = int(response.headers.get('Content-Length', 0))
            downloaded_size = 0

            # 下载新版本的可执行文件
            with open(filename, 'wb') as file, tqdm.tqdm(
                desc=f'下载 {filename}',
                total=total_size,
                unit='B',
                unit_scale=True,
                unit_divisor=1024,
                miniters=1,
                dynamic_ncols=True,
            ) as bar:
                for data in response.iter_content(chunk_size=1024):
                    if not data:
                        break
                    file.write(data)
                    downloaded_size += len(data)
                    bar.update(len(data))
            logger.info('下载完成: %s', filename)
    except Exception as e:
        logger.error('下载失败')
        return False

    if sha256:
        logger.info('正在校验文件...')
        if calculate_sha256(filename) != sha256:
            logger.error('文件校验失败，将不会更新')
            return False
        logger.info('文件校验通过')

    # 创建update.txt，写入旧版本的路径，以便更新后删除旧版本
    with open('update.txt', 'w') as f:
        f.write(sys.executable)
    os.startfile(filename)  # type: ignore
    os._exit(0)
    sys.exit(0)
    pid = os.getpid()
    os.kill(pid, signal.SIGTERM)

    return True


def getAds():
    try:
        response = session.get(
            'https://steamauto.jiajiaxd.com/ads/get/',
        )
        response.raise_for_status()
        ads = response.json()
        if len(ads) > 0:
            print('')
        for ad in ads:
            if ad.get('stop', 0):
                print(f'{parseBroadcastMessage(ad["message"])}\n(滞留 {ad["stop"]} 秒)\n')
                if not hasattr(os, "frozen"):
                    print('源码模式运行, 不进行暂停')
                else:
                    time.sleep(ad['stop'])
            else:
                print(f'{parseBroadcastMessage(ad["message"])}\n')

    except Exception as e:
        logger.warning('云服务无法连接，建议检查网络连接')
        handle_caught_exception(e, known=True)
        return False


def checkVersion():
    logger.info('正在检测当前版本是否为最新...')
    try:
        response = session.get(
            'https://steamauto.jiajiaxd.com/versions/',
            params={'clientVersion': static.CURRENT_VERSION, 'platform': get_platform_info()},
            timeout=5,
        )
        if response.status_code != 200:
            logger.error('由于服务端内部错误，无法检测版本')
            return False
        response = response.json()

        if not response['latest']:
            logger.warning(f'当前版本不是最新版本 最新版本为{response["latestVersion"]}')
            logger.warning('更新日志：' + response['changelog'].replace('\\n', '\n'))
        else:
            static.is_latest_version = True
            logger.info('当前版本为最新版本')

        if response['broadcast']:
            print('=' * 50 + '\n')
            print('Steamauto 官方公告：')
            print(parseBroadcastMessage(response['broadcast']['message']))
            print('\n' + '=' * 50)

        if response['latest']:
            return True
        static.is_latest_version = False
        if response['significance'] == 'minor':
            logger.info('最新版本为小版本更新，可自由选择是否更新')
        elif response['significance'] == 'normal':
            logger.warning('最新版本为普通版本更新，建议更新')
        elif response['significance'] == 'important':
            logger.warning('最新版本为重要版本更新，强烈建议更新')
        elif response['significance'] == 'critical':
            logger.error('最新版本为关键版本更新，可能包含重要修复，在更新前程序不会继续运行')
        if 'windows' in get_platform_info() and static.BUILD_INFO != '正在使用源码运行' and hasattr(sys, 'frozen'):
            logger.info('当前为独立打包程序且运行在Windows平台，将自动下载更新')
            if response.get('downloadUrl') and response.get('sha256'):
                logger.info('下载地址：' + response['downloadUrl'])
                autoUpdate(response['downloadUrl'], sha256=response['sha256'])
            elif response.get('message'):
                logger.warning(response['message'])
            else:
                logger.error('服务器未返回下载地址或sha256值，无法更新')
        elif response['significance'] == 'critical':
            logger.critical('由于版本过低，程序将退出')
            pause()
            pid = os.getpid()
            os.kill(pid, signal.SIGTERM)
        else:
            send_notification(
                title='Steamauto有新版本可用',
                message=f'当前版本：{static.CURRENT_VERSION}\n最新版本：{response["latestVersion"]}\n' f'更新日志：{response["changelog"]}',
            )

    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.warning('云服务无法连接，无法检测更新，建议检查网络连接')
        return False


def adsThread():
    while True:
        time.sleep(600)
        getAds()


def versionThread():
    while True:
        time.sleep(43200)
        checkVersion()


ad = threading.Thread(target=adsThread)
update = threading.Thread(target=versionThread)
ad.start()
update.start()
