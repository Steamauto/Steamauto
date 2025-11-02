import os

from utils.logger import handle_caught_exception, logger
from utils.static import PLUGIN_FOLDER


def patch():
    if os.path.exists("update.txt"):
        with open("update.txt", "r") as f:
            old_version_path = f.read()
        try:
            os.remove(old_version_path)
            os.remove("update.txt")
            logger.info("自动更新完毕！已删除旧版本文件")
        except Exception as e:
            handle_caught_exception(e, known=True)
            logger.error("无法删除旧版本文件 请手动删除！")

    need_to_delete = [
        f"{PLUGIN_FOLDER}/UUAutoSell.py",
        f"{PLUGIN_FOLDER}/UUAutoLease.py",
    ]
    for path in need_to_delete:
        if os.path.exists(path):
            try:
                os.remove(path)
                logger.info(f"已删除旧版本文件 {path}")
            except Exception as e:
                handle_caught_exception(e, known=True)
                logger.error(f"无法删除旧版本文件 {path} 请手动删除！")
