# ConfigManager.py
import json5
import os
import shutil
from utils.logger import logger
from utils.static import DEFAULT_CONFIG_JSON


class ConfigManager:
    def __init__(self, user_config_path):
        self.example_config = DEFAULT_CONFIG_JSON
        self.user_config_path = user_config_path
        self.config = {}

    def load_config(self):
        example_config = json5.loads(DEFAULT_CONFIG_JSON)
        self.config = self.load_json5(self.user_config_path)
        if not self.config:
            logger.error("无法读取配置文件，请检查文件是否存在或格式是否正确。")
            return False
        if self.config == {}:
            return False
        self.update_config(example_config)
        return True

    @staticmethod
    def load_json5(file_path):
        if not os.path.exists(file_path):
            return {}
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json5.load(f)
        except Exception as e:
            logger.error(f"解析 {file_path} 时出错: {e}", exc_info=True)
            return {}

    def save_json5(self):
        self.save_json5_to_file(self.config, self.user_config_path)

    @staticmethod
    def save_json5_to_file(data, file_path):
        with open(file_path, 'w', encoding='utf-8') as f:
            json5.dump(data, f, indent=4, ensure_ascii=False, quote_keys=True,trailing_commas=False)

    def backup_config(self):
        if os.path.exists(self.user_config_path):
            backup_path = f"{self.user_config_path}.bak"
            shutil.copyfile(self.user_config_path, backup_path)
            logger.info(f"已创建配置文件备份: {backup_path}")

    def merge_configs(self, example, user, path=""):
        for key, value in example.items():
            current_path = f"{path}.{key}" if path else key
            if key not in user:
                user[key] = value
                logger.info(f"添加缺失字段: {current_path} = {value}")
            else:
                if isinstance(value, dict) and isinstance(user[key], dict):
                    self.merge_configs(value, user[key], current_path)
        return user

    def update_config(self, example_config):
        missing_fields = []
        self.find_missing(example_config, self.config, missing_fields)

        if not missing_fields:
            logger.info("配置文件已是最新，无需更新。")
            return

        logger.info(f"检测到 {len(missing_fields)} 个缺失字段，将自动添加到配置文件中。")
        self.backup_config()
        self.config = self.merge_configs(example_config, self.config)
        self.save_json5()
        logger.info(f"已更新配置文件，添加了 {len(missing_fields)} 个新字段。")

    def find_missing(self, example, user, missing, path=""):
        for key, value in example.items():
            current_path = f"{path}.{key}" if path else key
            if key not in user:
                missing.append((current_path, value))
            else:
                if isinstance(value, dict) and isinstance(user[key], dict):
                    self.find_missing(value, user[key], missing, current_path)
