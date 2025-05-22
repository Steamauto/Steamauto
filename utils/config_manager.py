import os
import json5 # Kept for DEFAULT_CONFIG_JSON_REFERENCE parsing in extreme fallback
import toml
from utils.logger import logger
# Import DEFAULT_CONFIG_TOML and the reference JSON string
from utils.static import DEFAULT_CONFIG_TOML, DEFAULT_CONFIG_JSON_REFERENCE, DEFAULT_STEAM_ACCOUNT_JSON, CONFIG_FOLDER, STEAM_ACCOUNT_INFO_FILE_PATH
# DEFAULT_JSON_CONFIG_PATH_FOR_REFERENCE is also available from static if needed for the old json path
from utils.file_utils import get_encoding

# The main config path is now TOML, defined in static.py as CONFIG_FILE_PATH
from utils.static import CONFIG_FILE_PATH as TOML_CONFIG_FILE_PATH 
# The old JSON config path, for conversion logic
OLD_JSON_CONFIG_PATH = os.path.join(CONFIG_FOLDER, "config.json5")


class ConfigManager:
    def __init__(self, steam_account_file_path=STEAM_ACCOUNT_INFO_FILE_PATH):
        # Define paths for both JSON (old) and TOML (new) config files
        self.json_config_file_path = OLD_JSON_CONFIG_PATH 
        self.toml_config_file_path = TOML_CONFIG_FILE_PATH # This is 'config/config.toml'
        
        self.steam_account_file_path = steam_account_file_path
        self.config = {}
        self.steam_account_info = {}
        
        self._ensure_config_folder()
        self._maybe_convert_json_to_toml() 
        self._load_config() 
        self._load_steam_account_info()

    def _ensure_config_folder(self):
        if not os.path.exists(CONFIG_FOLDER):
            try:
                os.makedirs(CONFIG_FOLDER, exist_ok=True)
                logger.info(f"已创建配置文件夹: {CONFIG_FOLDER}")
            except Exception as e:
                logger.error(f"创建配置文件夹 {CONFIG_FOLDER} 时出错: {e}")

    def _maybe_convert_json_to_toml(self):
        if os.path.exists(self.toml_config_file_path):
            logger.debug(f"找到 {os.path.basename(self.toml_config_file_path)}。跳过 JSON5 到 TOML 的转换。")
            return

        if os.path.exists(self.json_config_file_path):
            logger.info(f"找到已存在的 {os.path.basename(self.json_config_file_path)}，尝试转换为 TOML...")
            try:
                json_encoding = get_encoding(self.json_config_file_path)
                with open(self.json_config_file_path, "r", encoding=json_encoding) as f_json:
                    json_data = json5.load(f_json)
                
                # Dump the data to TOML format (this will not have comments from DEFAULT_CONFIG_TOML)
                # If comments are desired for converted files, a more complex merge or template would be needed.
                # For now, direct conversion is simplest. The new default TOML will have comments.
                with open(self.toml_config_file_path, "w", encoding="utf-8") as f_toml:
                    toml.dump(json_data, f_toml)
                logger.info(f"成功将 {os.path.basename(self.json_config_file_path)} 转换为 {os.path.basename(self.toml_config_file_path)}。")

                backup_json_path = self.json_config_file_path + ".bak"
                try:
                    os.rename(self.json_config_file_path, backup_json_path)
                    logger.info(f"已将原始文件 {os.path.basename(self.json_config_file_path)} 重命名为 {os.path.basename(backup_json_path)}。")
                except OSError as e_rename:
                    logger.error(f"重命名 {os.path.basename(self.json_config_file_path)} 为备份文件失败: {e_rename}。如果转换成功，请手动删除或备份 .json5 文件。")
            except Exception as e:
                logger.error(f"JSON5 到 TOML 转换过程中出错: {e}。将继续加载/创建默认 TOML 配置。", exc_info=True)
        else:
            logger.debug(f"未找到 {os.path.basename(self.json_config_file_path)}。无需转换。")

    def _load_config(self):
        config_loaded_successfully = False
        if os.path.exists(self.toml_config_file_path):
            try:
                with open(self.toml_config_file_path, "r", encoding="utf-8") as f:
                    self.config = toml.load(f)
                logger.info(f"已从 {self.toml_config_file_path} 加载配置")
                config_loaded_successfully = True
            except toml.TomlDecodeError as e:
                logger.error(f"解码 TOML 文件 {self.toml_config_file_path} 时出错: {e}。将回退到默认配置。")
            except IOError as e:
                 logger.error(f"读取 TOML 文件 {self.toml_config_file_path} 时出错: {e}。将回退到默认配置。")
            except Exception as e:
                logger.error(f"加载 TOML 文件 {self.toml_config_file_path} 时发生意外错误: {e}。将回退到默认配置。", exc_info=True)
        
        if not config_loaded_successfully:
            logger.info(f"未找到 {self.toml_config_file_path} 或加载失败。正在创建带注释的默认 TOML 配置。")
            try:
                # Write the DEFAULT_CONFIG_TOML string (which includes comments) directly to the file
                with open(self.toml_config_file_path, "w", encoding="utf-8") as f_toml_default:
                    f_toml_default.write(DEFAULT_CONFIG_TOML)
                logger.info(f"已使用 DEFAULT_CONFIG_TOML 字符串创建带注释的默认配置文件: {self.toml_config_file_path}。")
                
                # Now load from this newly created file to populate self.config
                with open(self.toml_config_file_path, "r", encoding="utf-8") as f_read_default:
                    self.config = toml.load(f_read_default) # Load the TOML data structure
                config_loaded_successfully = True
            except IOError as e_write:
                logger.error(f"写入默认 TOML 配置到 {self.toml_config_file_path} 时出错: {e_write}。尝试内存内回退。")
                # Fallback to parsing DEFAULT_CONFIG_JSON_REFERENCE if writing DEFAULT_CONFIG_TOML fails
                try:
                    self.config = json5.loads(DEFAULT_CONFIG_JSON_REFERENCE)
                    logger.info("由于 TOML 写入失败，已使用来自 DEFAULT_CONFIG_JSON_REFERENCE 的内存内默认配置。")
                except json5.JSONDecodeError as e_json5_fallback:
                    logger.error(f"严重错误: 解析 DEFAULT_CONFIG_JSON_REFERENCE 字符串失败: {e_json5_fallback}。配置将为空。")
                    self.config = {}
            except toml.TomlDecodeError as e_toml_load_default: # If the DEFAULT_CONFIG_TOML string itself is invalid
                 logger.error(f"严重错误: DEFAULT_CONFIG_TOML 字符串无效: {e_toml_load_default}。配置将为空。")
                 self.config = {}
            except Exception as e_default_create:
                logger.error(f"创建或加载默认 TOML 配置时发生意外错误: {e_default_create}。配置将为空。", exc_info=True)
                self.config = {}

        # Ensure specific keys exist, applying defaults if necessary.
        # This is important if the loaded TOML (even default) somehow misses these.
        if not isinstance(self.config.get("steam_login_ignore_ssl_error"), bool): # Check type as well
            self.config["steam_login_ignore_ssl_error"] = False 
            logger.info("已应用默认配置 'steam_login_ignore_ssl_error' (false) 到配置中。")
        if not isinstance(self.config.get("steam_local_accelerate"), bool): # Check type, though TOML might handle it. "" is not bool.
            # The default in TOML is `false`, so this might adjust if it was, e.g. `""` from an old JSON.
            # For a fresh TOML from DEFAULT_CONFIG_TOML, this key should be correctly typed.
            # If it's loaded as string e.g. "false", this needs more robust type coercion or schema validation.
            # For now, assuming TOML load provides correct types or it's handled by user.
            # If the key is entirely missing:
            if "steam_local_accelerate" not in self.config :
                 self.config["steam_local_accelerate"] = False # Default from TOML string if key was absent
                 logger.info("由于键值缺失，已应用默认配置 'steam_local_accelerate' (false) 到配置中。")
            elif not isinstance(self.config.get("steam_local_accelerate"), bool):
                 logger.warning(f"配置项 'steam_local_accelerate' 不是布尔值: {self.config.get('steam_local_accelerate')}。将使用其当前值。")


    def _load_steam_account_info(self): # Remains unchanged, still uses JSON5
        if not os.path.exists(self.steam_account_file_path):
            try:
                with open(self.steam_account_file_path, "w", encoding="utf-8") as f:
                    f.write(DEFAULT_STEAM_ACCOUNT_JSON)
                logger.info(f"已创建默认 Steam 帐户信息文件: {self.steam_account_file_path}")
                self.steam_account_info = json5.loads(DEFAULT_STEAM_ACCOUNT_JSON)
            except Exception as e:
                logger.error(f"创建或写入默认 Steam 帐户信息文件 {self.steam_account_file_path} 时出错: {e}。将直接加载默认 Steam 帐户信息。")
                try:
                    self.steam_account_info = json5.loads(DEFAULT_STEAM_ACCOUNT_JSON)
                except Exception as e_default_steam:
                    logger.error(f"从字符串加载默认 Steam 帐户 JSON 时出错: {e_default_steam}。Steam 帐户信息将为空。")
                    self.steam_account_info = {}
        else:
            try:
                encoding = get_encoding(self.steam_account_file_path)
                with open(self.steam_account_file_path, "r", encoding=encoding) as f:
                    self.steam_account_info = json5.load(f)
                logger.info(f"已从文件加载 Steam 帐户信息: {self.steam_account_file_path}")
            except Exception as e:
                logger.error(f"加载 Steam 帐户信息文件 {self.steam_account_file_path} 时出错: {e}。将使用默认 Steam 帐户信息。")
                try:
                    self.steam_account_info = json5.loads(DEFAULT_STEAM_ACCOUNT_JSON)
                except Exception as e_default_steam_fallback:
                     logger.error(f"作为回退，从字符串加载默认 Steam 帐户 JSON 时出错: {e_default_steam_fallback}。Steam 帐户信息将为空。")
                     self.steam_account_info = {}

    def get(self, key, default=None):
        # Handle nested keys if needed, e.g., key = "section.subsection.key"
        if '.' in key:
            parts = key.split('.')
            value = self.config
            try:
                for part in parts:
                    value = value[part]
                return value
            except KeyError:
                return default
        return self.config.get(key, default)

    def get_steam_account_info(self):
        return self.steam_account_info

    def get_all_config(self):
        return self.config
