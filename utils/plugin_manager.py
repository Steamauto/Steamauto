import os
import sys
import importlib.util
import inspect
import re
import shutil

from utils.static import INTERNAL_PLUGINS, PLUGIN_FOLDER # Assuming PLUGIN_FOLDER is 'plugins'
from utils.file_utils import calculate_sha256
from utils.plugin_base import PluginBase # Added import for PluginBase
# We need logger, but it's passed in, so no direct import here unless for type hinting.
# Similarly for ConfigManager, SteamClient, and steam_client_mutex.

class PluginManager:
    def __init__(self, config_manager, logger, steam_client, steam_client_mutex):
        self.config_manager = config_manager
        self.logger = logger
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        
        self.plugin_folder_path = self._determine_plugin_folder_path()
        self.plugin_modules = self._import_plugins()

    def _camel_to_snake(self, name):
        if name == "ECOsteamPlugin":  # Special handling from original
            return "ecosteam"
        if name == "ECOsteam":  # Special handling from original
            return "ecosteam"
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\\1_\\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\\1_\\2', s1).lower()

    def _determine_plugin_folder_path(self):
        base_path = os.path.dirname(os.path.abspath(sys.argv[0] if hasattr(sys, 'frozen') else __file__))
        # Navigate up one level if __file__ is in utils
        if os.path.basename(base_path) == 'utils':
            base_path = os.path.dirname(base_path)

        if hasattr(sys, '_MEIPASS'):
            # PyInstaller bundle path
            bundle_plugin_folder = os.path.join(sys._MEIPASS, PLUGIN_FOLDER)
            # Local plugin folder (e.g., beside executable)
            local_plugin_folder = os.path.join(os.path.dirname(sys.executable), PLUGIN_FOLDER)

            if not os.path.exists(local_plugin_folder):
                self.logger.info(f"正在创建本地插件文件夹: {local_plugin_folder}")
                shutil.copytree(bundle_plugin_folder, local_plugin_folder)
            else:
                # Update existing plugins from bundle if necessary
                bundle_plugins = os.listdir(bundle_plugin_folder)
                for plugin_file_name in bundle_plugins:
                    bundle_plugin_path = os.path.join(bundle_plugin_folder, plugin_file_name)
                    local_plugin_path = os.path.join(local_plugin_folder, plugin_file_name)

                    if not os.path.exists(local_plugin_path):
                        self.logger.info(f"正在从程序包复制新插件: {plugin_file_name}")
                        shutil.copy(bundle_plugin_path, local_plugin_path)
                    elif os.path.isfile(bundle_plugin_path) and os.path.isfile(local_plugin_path): # Ensure they are files
                        try:
                            bundle_hash = calculate_sha256(bundle_plugin_path)
                            local_hash = calculate_sha256(local_plugin_path)
                            if bundle_hash != local_hash:
                                if plugin_file_name not in self.config_manager.get('plugin_whitelist', []):
                                    self.logger.info(f"插件 '{plugin_file_name}' 有更新，自动更新中。")
                                    shutil.copy(bundle_plugin_path, local_plugin_path)
                                else:
                                    self.logger.info(f"插件 '{plugin_file_name}' 与程序包中的版本不同，但已在白名单中。跳过更新。")
                        except Exception as e:
                            self.logger.error(f"检查/更新插件 {plugin_file_name} 时出错: {e}")
            final_plugin_path = local_plugin_folder
        else:
            # Development environment path
            final_plugin_path = os.path.join(base_path, PLUGIN_FOLDER)
        
        if not os.path.exists(final_plugin_path):
            self.logger.info(f"正在创建插件文件夹: {final_plugin_path}")
            os.makedirs(final_plugin_path, exist_ok=True)
            
        self.logger.info(f"插件文件夹路径已确定: {final_plugin_path}")
        return final_plugin_path

    def _import_module_from_file(self, module_name, file_path):
        try:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None:
                raise ImportError(f"Cannot create module spec from path '{file_path}'")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules[module_name] = module # Important for subsequent imports/lookups
            return module
        except Exception as e:
            # Using self.logger which was passed in __init__
            self.logger.error(f"从 '{file_path}' 导入模块 '{module_name}' 时出错: {e}")
            # from utils.logger import handle_caught_exception # Not available directly
            # handle_caught_exception(e, known=True) # Cannot call this directly
            return None

    def _import_plugins(self):
        plugin_modules = {}
        if not os.path.exists(self.plugin_folder_path):
            self.logger.warning(f"插件文件夹不存在: {self.plugin_folder_path}")
            return plugin_modules

        plugin_files = [f for f in os.listdir(self.plugin_folder_path) if f.endswith(".py") and f != "__init__.py"]

        for plugin_file in plugin_files:
            # Create a unique module name to avoid conflicts. Using PLUGIN_FOLDER as a prefix.
            module_name = f"{PLUGIN_FOLDER}.{plugin_file[:-3]}"
            file_path = os.path.join(self.plugin_folder_path, plugin_file)
            module = self._import_module_from_file(module_name, file_path)
            if module:
                plugin_modules[module_name] = module
        
        self.logger.info(f"已导入 {len(plugin_modules)} 个插件模块。")
        return plugin_modules

    def get_enabled_plugins(self):
        enabled_plugins = []
        
        for module_name, plugin_module in self.plugin_modules.items():
            for cls_name, cls_obj in inspect.getmembers(plugin_module, inspect.isclass):
                if not inspect.isclass(cls_obj) or cls_obj is PluginBase: # Skip PluginBase itself if listed
                    continue

                plugin_key = self._camel_to_snake(cls_name)

                plugin_config_settings = self.config_manager.get(plugin_key)
                is_enabled_in_config = plugin_config_settings and plugin_config_settings.get("enable")
                is_custom_plugin_auto_enabled = (plugin_config_settings is None) and (plugin_key not in INTERNAL_PLUGINS)

                if not (is_enabled_in_config or is_custom_plugin_auto_enabled):
                    continue

                # Prepare constructor arguments
                init_kwargs = {}
                sig = inspect.signature(cls_obj.__init__)
                params = sig.parameters

                if issubclass(cls_obj, PluginBase):
                    if "plugin_name" in params: init_kwargs["plugin_name"] = plugin_key
                    if "main_logger" in params: init_kwargs["main_logger"] = self.logger
                    if "config" in params: init_kwargs["config"] = self.config_manager.get_all_config()
                    if "steam_client" in params: init_kwargs["steam_client"] = self.steam_client
                    if "steam_client_mutex" in params: init_kwargs["steam_client_mutex"] = self.steam_client_mutex
                else:
                    # For other/older plugins
                    if "logger" in params: init_kwargs["logger"] = self.logger
                    if "config" in params: init_kwargs["config"] = self.config_manager.get_all_config()
                    if "steam_client" in params: init_kwargs["steam_client"] = self.steam_client
                    if "steam_client_mutex" in params: init_kwargs["steam_client_mutex"] = self.steam_client_mutex
                
                final_kwargs = {k: v for k, v in init_kwargs.items() if k in params}
                
                required_params = [p.name for p in params.values() if p.default == inspect.Parameter.empty and p.name != 'self']
                missing_params = [p for p in required_params if p not in final_kwargs]
                
                if missing_params:
                    self.logger.error(f"无法实例化来自模块 {module_name} 的插件 {cls_name}: 缺少必要的构造函数参数: {missing_params}")
                    self.logger.debug(f"已提供参数: {list(final_kwargs.keys())}，签名要求参数: {list(params.keys())}")
                    continue

                if not hasattr(cls_obj, "init"):
                    self.logger.debug(f"类 {cls_name} (模块 {module_name}) 没有 init 方法，已跳过。")
                    continue
                init_sig = inspect.signature(cls_obj.init)
                if len(init_sig.parameters) != 1: # Expects only 'self'
                    self.logger.debug(f"类 {cls_name} (模块 {module_name}) 的 init 方法签名不符合预期，已跳过。")
                    continue
                    
                try:
                    plugin_instance = cls_obj(**final_kwargs)
                    enabled_plugins.append(plugin_instance)
                    if is_custom_plugin_auto_enabled: # Log for custom plugin after successful instantiation
                         self.logger.info(f"已加载自定义插件: {plugin_key} (类: {cls_name})")
                    self.logger.info(f"已成功从类 '{cls_name}' 实例化插件 '{plugin_key}'。")
                except TypeError as e:
                    self.logger.error(f"实例化来自模块 {module_name} 的插件 {cls_name} 时出错: {e}。")
                    self.logger.debug(f"尝试使用关键字参数调用: {final_kwargs}，构造函数签名参数: {list(params.keys())}")
                except Exception as e:
                    self.logger.error(f"实例化来自模块 {module_name} 的插件 {cls_name} 时发生意外错误: {e}", exc_info=True)
                
        self.logger.info(f"找到 {len(enabled_plugins)} 个已启用的插件实例。")
        return enabled_plugins
        
    def check_plugins_initialization(self, enabled_plugins):
        if not enabled_plugins:
            # Original message: "未启用任何插件, 请检查" + CONFIG_FILE_PATH + "是否正确! "
            # CONFIG_FILE_PATH is not directly available, but config_manager.config_file_path could be used if needed.
            self.logger.error("没有启用或加载任何插件。请检查您的配置。")
            return 2 # Code for no plugins enabled

        self.logger.info(f"正在为 {len(enabled_plugins)} 个插件检查初始化状态...")
        for plugin in enabled_plugins:
            try:
                # The original logic: `if plugin.init(): return 0` means plugin.init() returning True is an error.
                if plugin.init(): 
                    self.logger.error(f"插件 '{getattr(plugin, '__class__', {}).__name__}' 初始化失败 (init() 返回 True)。")
                    return 0 # Error in initialization
            except Exception as e:
                self.logger.error(f"插件 '{getattr(plugin, '__class__', {}).__name__}' 初始化过程中发生异常: {e}")
                return 0 # Error in initialization
        
        self.logger.info("所有已启用的插件均已成功初始化。")
        return 1 # All plugins initialized successfully
