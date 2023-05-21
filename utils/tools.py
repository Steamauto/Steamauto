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
