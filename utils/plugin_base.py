# utils/plugin_base.py
from utils.logger import PluginLogger # Assuming PluginLogger is in utils.logger

class PluginBase:
    def __init__(self, plugin_name: str, main_logger, config: dict, steam_client, steam_client_mutex):
        """
        Initializes the base plugin.

        Args:
            plugin_name (str): The name of the plugin (snake_case), used for PluginLogger.
            main_logger: The main application logger instance.
            config (dict): The application configuration dictionary.
            steam_client: The SteamClient instance.
            steam_client_mutex: The mutex for SteamClient operations.
        """
        self.plugin_name = plugin_name
        # Although main_logger is passed, PluginBase immediately sets up its own context-specific logger.
        # main_logger could be used for very early logging if needed, or just be available.
        self.logger = PluginLogger(self.plugin_name) 
        self.config = config # Full config, plugins can derive their specific section
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        
        # Example for plugins to get their specific config:
        # self.plugin_config = self.config.get(self.plugin_name)
        
        self.logger.info(f"插件 '{self.plugin_name}' 基类已初始化。")

    def init(self) -> bool:
        """
        Perform plugin-specific initialization.
        This method should be overridden by subclasses if specific initialization is needed.
        
        Returns:
            bool: False if initialization is successful, True if an error occurs.
                  (This matches the existing convention in plugins_check).
        """
        self.logger.debug(f"正在为插件 '{self.plugin_name}' 执行基类 init() 方法。")
        return False # Default successful initialization

    def exec(self):
        """
        Main execution logic for the plugin.
        This method MUST be overridden by subclasses.
        """
        self.logger.error(f"插件 '{self.plugin_name}' 未实现 exec() 方法。")
        raise NotImplementedError(f"{self.plugin_name} must implement the exec() method.")
