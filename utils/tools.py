import chardet

from utils.logger import logger
from utils.static import config


# 用于解决读取文件时的编码问题
def get_encoding(file_path):
    with open(file_path, "rb") as f:
        data = f.read()
        charset = chardet.detect(data)["encoding"]
    return charset


def pause():
    if "no_pause" in config and not config["no_pause"]:
        logger.info("点击回车键继续...")
        input()


def compare_version(ver1, ver2):
    version1_parts = ver1.split('.')
    version2_parts = ver2.split('.')

    for i in range(max(len(version1_parts), len(version2_parts))):
        v1 = int(version1_parts[i]) if i < len(version1_parts) else 0
        v2 = int(version2_parts[i]) if i < len(version2_parts) else 0

        if v1 < v2:
            return -1
        elif v1 > v2:
            return 1

    return 0