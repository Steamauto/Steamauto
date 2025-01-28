import os
import platform
import signal

import requests
import tqdm
from colorama import Fore, Style

from utils.logger import handle_caught_exception, logger
from utils.static import BUILD_INFO, CURRENT_VERSION
from utils.tools import pause


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
    return message


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
    
def autoUpdate(downloadUrl):
    """
    自动更新当前程序。
    
    参数:
    - downloadUrl (str): 新版本可执行文件的下载URL。
    
    返回:
    - bool: 更新是否成功发起。
    """
    import os
    import subprocess
    import sys
    try:
        with requests.get(downloadUrl, stream=True, timeout=30) as response:
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
        handle_caught_exception(e)
        logger.error('下载失败')
        return False

    # 获取当前执行文件的路径
    if getattr(sys, 'frozen', False):
        current_executable = sys.executable
    else:
        current_executable = os.path.abspath(__file__)

    # 获取新版本文件的完整路径
    new_version_path = os.path.abspath(filename)

    # 确保新文件存在
    if not os.path.exists(new_version_path):
        logger.error('新版本文件不存在: %s', new_version_path)
        return False

    # 创建批处理文件内容
    update_script_content = f"""
@echo off
:loop
tasklist | find /i "{os.path.basename(current_executable)}" > nul
if not errorlevel 1 (
    timeout /t 1 > nul
    goto loop
)
move /y "{new_version_path}" "{current_executable}" > nul
start "" "{current_executable}"
del "%~f0"
"""

    # 批处理文件路径
    update_script_path = os.path.join(os.path.dirname(current_executable), 'update.bat')

    try:
        # 写入批处理文件
        with open(update_script_path, 'w', encoding='utf-8') as f:
            f.write(update_script_content)
        logger.info('更新脚本已创建: %s', update_script_path)
        
        # 启动批处理文件
        subprocess.Popen(['cmd', '/c', 'start', '', update_script_path], shell=True)
        logger.info('启动更新脚本，并准备退出当前程序。')
        
        # 退出当前程序
        sys.exit()
    except Exception as e:
        handle_caught_exception(e)
        logger.error('更新失败：无法创建或启动更新脚本。')
        return False

    return True
    
    

def checkVersion():
    logger.info('正在检测当前版本是否为最新...')
    try:
        response = requests.get(
            'https://steamauto.jiajiaxd.com/versions/',
            params={'clientVersion': CURRENT_VERSION, 'platform': get_platform_info()},
            timeout=5,
        )
        if response.status_code != 200:
            logger.error('由于服务端内部错误，无法检测版本')
            return False
        response = response.json()
        
        if response['broadcast']:
            logger.info('Steamauto 官方公告：\n'+parseBroadcastMessage(response['broadcast']['message']))
            
        if not response['latest']:
            logger.warning(f'当前版本不是最新版本 最新版本为{response["latestVersion"]}')
        else:
            logger.info('当前版本为最新版本')
            return True
        logger.info('更新日志：\n'+response['changelog'])
        
        
        
        if response['significance'] == 'minor':
            logger.info('最新版本为小版本更新')
        elif response['significance'] == 'normal':
            logger.info('最新版本为普通版本更新，建议更新')
        elif response['significance'] == 'important':
            logger.warning('最新版本为重要版本更新，强烈建议更新')
        elif response['significance'] == 'critical':
            logger.error('最新版本为关键版本更新，可能包含重要修复，在更新前程序不会继续运行')
        if 'windows' in get_platform_info() and BUILD_INFO != '正在使用源码运行':
            logger.info('由于使用Windows平台，将自动下载更新')
            logger.info('下载地址：'+response['downloadUrl'])
            autoUpdate(response['downloadUrl'])
        elif response['significance'] == 'critical':
            logger.critical('由于版本过低，程序将退出')
            pause()
            pid = os.getpid()
            os.kill(pid, signal.SIGTERM)
            
    except Exception as e:
        handle_caught_exception(e)
        logger.error('检测版本失败 将继续运行')
        return False

