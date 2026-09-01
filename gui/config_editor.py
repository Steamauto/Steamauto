"""Steamauto GUI 的配置读写模块。

完全独立于 Steamauto 核心代码，只做文件级读写与 JSON5 校验。
路径常量与 utils/static.py 中的值保持一致。
"""
import json5
import os
from typing import Optional, Tuple, Union

# 项目根目录（gui/ 的上一级）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_FOLDER = "config"
LOGS_FOLDER = "logs"

CONFIG_FILE = os.path.join(CONFIG_FOLDER, "config.json5")
ACCOUNT_FILE = os.path.join(CONFIG_FOLDER, "steam_account_info.json5")

CONFIG_FILE_PATH = os.path.join(PROJECT_ROOT, CONFIG_FILE)
ACCOUNT_FILE_PATH = os.path.join(PROJECT_ROOT, ACCOUNT_FILE)
LOGS_FOLDER_PATH = os.path.join(PROJECT_ROOT, LOGS_FOLDER)

# 账号信息默认值（对应 utils/static.py 的 DEFAULT_STEAM_ACCOUNT_JSON）
ACCOUNT_DEFAULT = {
    "shared_secret": "",
    "identity_secret": "",
    "steam_username": "",
    "steam_password": "",
}


def read_text(path: str) -> Optional[str]:
    """读取文件原文，不存在返回 None。"""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def validate_json5(text: str) -> Tuple[bool, Union[object, str]]:
    """校验 JSON5 文本，返回 (ok, value_or_error)。"""
    try:
        value = json5.loads(text)
        return True, value
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def save_text(path: str, text: str) -> Tuple[bool, str]:
    """校验后写回文本（保留注释等原文），返回 (ok, msg)。"""
    ok, value = validate_json5(text)
    if not ok:
        return False, "JSON5 语法错误：" + str(value)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True, "保存成功"


def load_json5(path: str) -> Optional[dict]:
    """解析配置文件为 dict，失败返回 None。"""
    text = read_text(path)
    if text is None:
        return None
    ok, value = validate_json5(text)
    return value if ok and isinstance(value, dict) else None


def save_json5(path: str, obj: dict) -> bool:
    """将 dict 序列化写回（注意：会丢失原文注释）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = json5.dumps(obj, indent=2, ensure_ascii=False, trailing_commas=False)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True
