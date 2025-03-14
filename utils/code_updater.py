import io
import os
import shutil
import subprocess
import sys
import zipfile

import requests

from utils.tools import compare_version

GITHUB_REPO_OWNER = "Steamauto"
GITHUB_REPO_NAME = "Steamauto"
from utils.logger import handle_caught_exception, logger


GITHUB_REPO_OWNER = "Steamauto"
GITHUB_REPO_NAME = "Steamauto"

def attempt_auto_update_github(current_version: str):
    """
    检查 GitHub 上是否有新版本。如果有，则下载 zip 包、解压缩、覆盖当前目录，并重启脚本。
    """

    # 1) 访问 GitHub releases/latest 接口
    url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/latest"
    try:
        response = requests.get(url, timeout=10)
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            logger.warning(f"GitHub API 请求失败: {e}，跳过自动更新。")
            return

        release_data = response.json()
        latest_version = release_data.get("tag_name", None)
        zip_url = None

        # 如果你在 GitHub 发布时使用 "tag_name"（例如 "v1.2.3"）：
        # 将 latest_version 与 current_version 比较
        # 这里只是一个简单的对比，根据需要修改 compare_version。
        if latest_version and compare_version(current_version, latest_version.lstrip("v")) == -1:
            # 2) 查找 .zip 资源或使用 "source_code.zip" 作为备用
            zip_url = release_data.get("zipball_url", None)

            if not zip_url:
                logger.warning("无法找到最新版本对应的 zip 下载链接。")
                return

            logger.info(f"尝试从 {zip_url} 下载新版本...")

            # 3) 下载并解压到临时文件夹
            r = requests.get(zip_url, timeout=30)
            try:
                r.raise_for_status()
            except requests.exceptions.HTTPError as e:
                logger.warning(f"下载失败: {e}，跳过自动更新。")
                return

            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                temp_update_folder = os.path.join(os.getcwd(), "__steamauto_update_temp__")
                if os.path.exists(temp_update_folder):
                    shutil.rmtree(temp_update_folder)
                os.makedirs(temp_update_folder, exist_ok=True)
                zf.extractall(temp_update_folder)

            # 4) 找到解压后的根文件夹（GitHub 通常命名为 <repo>-<commit>）
            #    在 temp_update_folder 中定位该文件夹
            extracted_subfolders = [f for f in os.listdir(temp_update_folder)
                                    if os.path.isdir(os.path.join(temp_update_folder, f))]
            if not extracted_subfolders:
                logger.warning("在下载的 zip 中未找到任何子文件夹。跳过自动更新。")
                return
            root_extracted = os.path.join(temp_update_folder, extracted_subfolders[0])

            # 5) 将更新后的文件复制覆盖到当前目录
            #    或者把它放到单独的文件夹以便从那里运行。
            #    出于安全考虑，可以先备份或跳过特定文件夹。
            logger.info("将更新文件复制到当前目录...")
            copy_over(root_extracted, os.getcwd(), skip_folders=["__steamauto_update_temp__", "venv", ".git", ".idea"])

            requirements_file = os.path.join(os.getcwd(), "requirements.txt")
            if os.path.exists(requirements_file):
                try:
                    logger.info("正在从 requirements.txt 安装或更新依赖包...")
                    # 建议通过 'python -m pip ...' 来调用 pip
                    subprocess.check_call([
                        sys.executable, "-m", "pip",
                        "install", "-r", "requirements.txt"
                    ])
                    logger.info("依赖包已更新成功。")
                except Exception as e:
                    handle_caught_exception(e)
                    logger.warning("无法更新依赖。可能需要手动安装。")

            # 6) 清理
            shutil.rmtree(temp_update_folder)
            logger.info("更新已成功。正在重启...")

            # 7) 从更新后的代码中重新启动脚本
            #    在 Windows 上: python main.py ...
            #    在类 Unix 系统上: python3 main.py ...
            #    或者简单地使用相同参数重新执行当前 Python：
            python_executable = sys.executable
            os.execl(python_executable, python_executable, *sys.argv)

        else:
            logger.info("未发现新版本，跳过自动更新。")

    except Exception as e:
        handle_caught_exception(e)
        logger.warning("由于异常导致自动更新失败。")

def copy_over(src: str, dst: str, skip_folders=None):
    """
    递归地将 src 中的所有文件/文件夹复制到 dst，
    跳过 skip_folders 中指定的任何内容。
    """
    if skip_folders is None:
        skip_folders = []
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        # 跳过 skip_folders 中指定的项目
        if item in skip_folders:
            continue
        if os.path.isdir(s):
            if not os.path.exists(d):
                os.makedirs(d, exist_ok=True)
            copy_over(s, d, skip_folders=skip_folders)
        else:
            shutil.copy2(s, d)
