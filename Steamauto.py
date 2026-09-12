import importlib
import importlib.util
import inspect
import os
import random
import re
import shutil
import signal
import sys
import threading
import time
from typing import no_type_check

# ---------------------------------------------------------------------------
# CLI 前置分发
# ---------------------------------------------------------------------------
# 命令行**全部为 `--` 长选项风格**（无子命令），因此一律交给 utils.cli：
#   · --status / --log / --login / --config 等只需 cli 那一小撮轻量模块，
#     若继续往下执行本模块，会顺带创建日志文件、加载 Steam 客户端与插件，
#     既慢又会在 logs/ 里留下一堆无意义的日志；
#   · --run / --start / --restart 需要跑服务，由 cli 决定何时 import 本模块
#     （见 utils.cli.cmd_run 的惰性导入）；
#   · **无参数也必须经过 cli** —— 否则拿不到「初始化后转后台」这一运行模式设置
#     （cli.cmd_run 会设置 STEAMAUTO_BG_HANDOFF，main 据此在初始化后转后台）。
if __name__ == "__main__":
    from utils.cli import main as _cli_main

    sys.exit(_cli_main(list(sys.argv[1:])))

import json5
from colorama import Fore, Style

import utils.static as static
from api.Steam.steampy.client import SteamClient
from utils import config_writer, control, daemon, runtime
from utils.code_updater import attempt_auto_update_github
from utils.logger import echo, f_handler, handle_caught_exception, logger
from utils.notifier import send_notification
from utils.old_version_patches import patch
from utils.static import (
    BUILD_INFO,
    CONFIG_FILE_PATH,
    CONFIG_FOLDER,
    CURRENT_VERSION,
    DEFAULT_CONFIG_JSON,
    DEFAULT_STEAM_ACCOUNT_JSON,
    INTERNAL_PLUGINS,
    PLUGIN_FOLDER,
    SESSION_FOLDER,
    STEAM_ACCOUNT_INFO_FILE_PATH,
)
from utils.steam_client import OfflineSteamClient, login_to_steam, steam_client_mutex
from utils.tools import calculate_sha256, exit_code, get_encoding, pause

config = {}

# 运行期状态：供控制通道的 ping/status 使用
_RUNTIME_START = time.time()
# 运行期引用（供控制通道查询真实状态）
_STEAM_CLIENT = None
_PLUGIN_RUNTIME = None
# 收到停止请求后，等待插件线程自行收尾的宽限时间（秒）
SHUTDOWN_GRACE = 15.0

# 支持运行时热改的配置键（改动后立即生效，无需重启）
HOT_KEYS = frozenset({
    "log_level",
    "log_retention_days",
    "no_pause",
    "manual_confirm_delivery",
    "console_echo.enable",
    "console_echo.min_level",
    "console_echo.dual_write_events",
})
# 插件轮询间隔类配置：插件改为每轮读取配置，因此热改同样立即生效
HOT_INTERVAL_PREFIXES = (
    "buff_auto_accept_offer.interval",
    "c5_auto_accept_offer.interval",
    "steam_auto_accept_offer.interval",
    "uu_auto_accept_offer.interval",
    "uu_auto_lease_item.interval",
    "uu_auto_sell_item.interval",
    "uu_auto_sell_item.sell_interval",
    "ecosteam.auto_accept_offer.interval",
    "ecosteam.sync_interval",
    "ecosteam.qps",
)


def is_hot_key(key: str) -> bool:
    """该配置键是否支持运行时热应用。"""
    return key in HOT_KEYS or key in HOT_INTERVAL_PREFIXES


def handle_global_exception(exc_type, exc_value, exc_traceback):
    logger.exception(
        "程序发生致命错误，请将此界面截图，并提交最新的log文件到https://github.com/jiajiaxd/Steamauto/issues",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
    logger.error("由于出现致命错误，程序即将退出...")
    pause()


def set_exit_code(code):
    global exit_code
    exit_code = code


# 文件缺失或格式错误返回0，首次运行返回1，非首次运行返回2
def init_files_and_params() -> int:
    global config
    patch()
    # 启动横幅属于「必要回显」：只上控制台，不写入日志文件（避免日志被问候语淹没）。
    # 当前版本/编译信息是排查问题要用的关键业务事件，双写。
    echo("欢迎使用 Steamauto GitHub 仓库: https://github.com/Steamauto/Steamauto")
    echo("欢迎加入 Steamauto 官方QQ群 群号: 425721057")
    echo("若出现技术问题，请先查看README和常见问题解答，仍未解决请加入QQ群并在群内咨询！")
    echo("若您觉得Steamauto好用, 请给予Star支持, 感谢!")
    echo(f"{Fore.RED + Style.BRIGHT}！！！ 本程序完全{Fore.YELLOW}免费开源 {Fore.RED}若有人向你售卖，请立即投诉并申请退款 ！！！ \n")
    echo(f"当前版本: {CURRENT_VERSION}   编译信息: {BUILD_INFO}", dual=True)
    logger.info("Steamauto %s 启动，编译信息：%s，进程 PID：%s", CURRENT_VERSION, BUILD_INFO, os.getpid())
    try:
        with open(CONFIG_FILE_PATH, "r", encoding=get_encoding(CONFIG_FILE_PATH)) as f:
            config = json5.load(f)
    except:
        config = {}
    if config.get("source_code_auto_update", False):
        if not hasattr(sys, "_MEIPASS"):
            attempt_auto_update_github(CURRENT_VERSION)
    else:
        try:
            from utils import cloud_service

            cloud_service.checkVersion()
            cloud_service.getAds()
        except Exception as e:
            logger.warning("无法使用云服务")
    logger.info("正在初始化...")
    first_run = False
    if not os.path.exists(CONFIG_FOLDER):
        os.mkdir(CONFIG_FOLDER)
    if not os.path.exists(CONFIG_FILE_PATH):
        with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG_JSON)
        logger.info("检测到首次运行, 已为您生成" + CONFIG_FILE_PATH + ", 请按照README提示填写配置文件! ")
        first_run = True
    # 注意：**首次运行也要加载刚生成的默认配置**。
    # 否则 config 变量保持为空 {}，后续 get_plugins_enabled 会因为
    # 「plugin_key 不在 config 里、又属于内置插件」而判定无插件启用并直接退出。
    with open(CONFIG_FILE_PATH, "r", encoding=get_encoding(CONFIG_FILE_PATH)) as f:
        try:
            config = json5.load(f)
        except Exception as e:
            handle_caught_exception(e, known=True)
            logger.error("检测到" + CONFIG_FILE_PATH + "格式错误, 请检查配置文件格式是否正确, 或尝试重新生成配置文件并重新配置! ")
            return 0
    if not os.path.exists(STEAM_ACCOUNT_INFO_FILE_PATH):
        with open(STEAM_ACCOUNT_INFO_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_STEAM_ACCOUNT_JSON)
            logger.info("检测到首次运行, 已为您生成" + STEAM_ACCOUNT_INFO_FILE_PATH + ", 请按照README提示填写配置文件! ")
            first_run = True

    if not first_run:
        if "no_pause" in config:
            static.no_pause = config["no_pause"]
        # GUI 启动的子进程无交互终端：强制 no_pause（出错不等待按键，覆盖 config 设置）
        if os.environ.get("STEAMAUTO_NO_PAUSE") == "1":
            static.no_pause = True
        static.manual_confirm_delivery = config.get("manual_confirm_delivery", True)
        if "steam_login_ignore_ssl_error" not in config:
            config["steam_login_ignore_ssl_error"] = False
        if "steam_local_accelerate" not in config:
            config["steam_local_accelerate"] = False

    if first_run:
        return 1
    else:
        return 2


@no_type_check
def get_plugins_folder():
    base_path = os.path.dirname(os.path.abspath(__file__))
    if hasattr(sys, "_MEIPASS"):
        base_path = os.path.dirname(sys.executable)
        if not os.path.exists(os.path.join(base_path, PLUGIN_FOLDER)):
            shutil.copytree(os.path.join(sys._MEIPASS, PLUGIN_FOLDER), os.path.join(base_path, PLUGIN_FOLDER))
        else:
            plugins = os.listdir(os.path.join(sys._MEIPASS, PLUGIN_FOLDER))
            for plugin in plugins:
                plugin_absolute = os.path.join(sys._MEIPASS, PLUGIN_FOLDER, plugin)
                local_plugin_absolute = os.path.join(base_path, PLUGIN_FOLDER, plugin)
                if os.path.isdir(plugin_absolute):
                    continue
                if os.path.isdir(local_plugin_absolute):
                    continue
                if not os.path.exists(local_plugin_absolute):
                    shutil.copy(plugin_absolute, local_plugin_absolute)
                else:
                    local_plugin_sha256 = calculate_sha256(local_plugin_absolute)
                    plugin_sha256 = calculate_sha256(plugin_absolute)
                    if local_plugin_sha256 != plugin_sha256:
                        if plugin not in config.get("plugin_whitelist", []):
                            logger.info("检测到插件" + plugin + "有更新，已自动更新 如果不需要更新请在配置文件中将该插件加入白名单")
                            shutil.copy(plugin_absolute, local_plugin_absolute)
                        else:
                            logger.info("插件" + plugin + "与本地版本不同 由于已被加入白名单，不会自动更新")
    return os.path.join(base_path, PLUGIN_FOLDER)


def import_module_from_file(module_name, file_path):
    """
    从指定文件路径动态导入模块。

    参数：
        module_name (str): 模块的名称（应在当前环境中唯一）。
        file_path (str): 模块的文件路径。

    返回：
        module: 导入的模块对象。
    """
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None:
            raise ImportError(f"无法从路径 '{file_path}' 创建模块规格")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore
        sys.modules[module_name] = module
        return module
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error(f"导入模块 '{module_name}' 时出现错误")
        return None


def import_all_plugins():
    # 自动导入所有插件
    plugin_files = [f for f in os.listdir(get_plugins_folder()) if f.endswith(".py") and f != "__init__.py"]

    for plugin_file in plugin_files:
        module_name = f"{PLUGIN_FOLDER}.{plugin_file[:-3]}"
        import_module_from_file(module_name, os.path.join(get_plugins_folder(), plugin_file))


def camel_to_snake(name):
    if name == "ECOsteamPlugin":  # 特殊处理
        return "ecosteam"
    if name == "ECOsteam":  # 特殊处理
        return "ecosteam"
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def get_plugin_classes():
    plugin_classes = {}
    for name, obj in sys.modules.items():
        if name.startswith(f"{PLUGIN_FOLDER}.") and name != f"{PLUGIN_FOLDER}.__init__":
            plugin_name = name.replace(f"{PLUGIN_FOLDER}.", "")
            plugin_name = camel_to_snake(plugin_name)
            plugin_classes[plugin_name] = obj
    # 返回的文件结构：
    # {
    #     "[插件名]": [插件类],
    #     ...
    # }
    return plugin_classes


def get_plugins_enabled(steam_client: SteamClient, steam_client_mutex):
    """返回 {plugin_key: 插件实例}。

    保留 plugin_key 是必须的：运行期需要按平台定位插件（登录成功后只重启对应插件），
    按位置索引会与配置里的键对不上。
    """
    global config
    plugins_enabled = {}
    plugin_modules = get_plugin_classes()  # 获取所有插件类

    for plugin_key, plugin_module in plugin_modules.items():
        # 判断配置文件里是否存在 plugin_key 且已启用
        if (plugin_key in config and config[plugin_key].get("enable")) or ((plugin_key not in config) and (plugin_key not in INTERNAL_PLUGINS)):
            if plugin_key not in config:
                logger.info(f"已加载自定义插件 {plugin_key}")
            # 遍历插件模块里的所有类
            for cls_name, cls_obj in inspect.getmembers(plugin_module, inspect.isclass):
                # 根据构造函数的形参，对号入座。用kwargs可以避免顺序不一致的问题
                init_signature = inspect.signature(cls_obj.__init__)
                init_kwargs = {}
                unknown_class = False

                for param_name, param in init_signature.parameters.items():
                    if param_name == "logger":
                        init_kwargs[param_name] = logger
                    elif param_name == "steam_client":
                        init_kwargs[param_name] = steam_client
                    elif param_name == "steam_client_mutex":
                        init_kwargs[param_name] = steam_client_mutex
                    elif param_name == "config":
                        init_kwargs[param_name] = config
                    elif param_name == "self":
                        continue
                    else:
                        # 根本不认识这个类
                        unknown_class = True
                        break
                if unknown_class:
                    continue

                # 确定这个类有init()函数 并且这个函数为无参数的
                if not hasattr(cls_obj, "init"):
                    continue
                init_signature = inspect.signature(cls_obj.init)
                if len(init_signature.parameters) != 1:
                    continue
                plugin_instance = cls_obj(**init_kwargs)
                if plugin_key in plugins_enabled:
                    logger.warning("插件 %s 存在多个可用类，仅使用 %s" % (plugin_key, type(plugins_enabled[plugin_key]).__name__))
                    continue
                plugins_enabled[plugin_key] = plugin_instance

    return plugins_enabled


def plugin_init_ok(instance):
    """对单个插件跑一次 init()。

    :return: (ok: bool, reason: str)。注意 Steamauto 的约定是
             `init()` 返回 **True 表示失败**（历史语义，勿改）。
    """
    try:
        if instance.init():
            return False, "初始化失败"
    except Exception as e:
        handle_caught_exception(e, known=True)
        return False, "初始化异常: %s" % (e,)
    return True, ""


def plugins_check(plugins_enabled):
    """对一组插件做初始化检查，返回初始化成功的插件列表。

    保留「列表进出」的原始语义（既有调用方与测试依赖它）。
    """
    if len(plugins_enabled) == 0:
        logger.error("未启用任何插件, 请检查" + CONFIG_FILE_PATH + "是否正确! ")
        return []
    ok_plugins = []
    for plugin in plugins_enabled:
        ok, reason = plugin_init_ok(plugin)
        if ok:
            ok_plugins.append(plugin)
        else:
            logger.error("插件 " + type(plugin).__name__ + " " + reason + "，已跳过")
    return ok_plugins


class PluginRuntime:
    """插件运行期管理：跟踪实例/线程，支持「登录后动态启动某个平台插件」。

    为什么需要它：平台登录失败的插件若在启动阶段被直接丢弃，用户之后补登录
    （`--login uu`）就没有任何东西能接上——只能重启整个程序。因此这里把失败
    插件**保留**下来，登录成功后由控制通道的 `plugin.retry` 重新 init 并起线程。
    """

    def __init__(self, plugins_map):
        self.map = dict(plugins_map)   # plugin_key -> instance
        self.threads = {}              # plugin_key -> Thread
        self.failed = {}               # plugin_key -> reason
        self._lock = threading.Lock()

    # ---- 查询 ----
    def started_keys(self):
        return sorted(k for k, t in self.threads.items() if t.is_alive())

    def failed_keys(self):
        return sorted(self.failed)

    def pending_keys(self):
        """尚未成功运行的插件键（含从未启动与启动失败的）。"""
        return sorted(set(self.map) - set(self.started_keys()))

    def is_running(self, key):
        t = self.threads.get(key)
        return bool(t and t.is_alive())

    # ---- 启动 ----
    def start(self, key):
        """初始化并启动某个插件线程。返回 (ok: bool, message: str)。"""
        with self._lock:
            instance = self.map.get(key)
            if instance is None:
                known = ", ".join(sorted(self.map)) or "（无）"
                return False, "未启用插件 %s；当前已加载：%s" % (key, known)
            if self.is_running(key):
                return True, "%s 已在运行" % key

            ok, reason = plugin_init_ok(instance)
            if not ok:
                self.failed[key] = reason
                return False, "%s %s" % (key, reason)

            self.failed.pop(key, None)
            thread = threading.Thread(target=instance.exec, name="plugin-%s" % key, daemon=True)
            self.threads[key] = thread
            thread.start()
            return True, "已启动 %s（%s）" % (key, type(instance).__name__)

    def start_all(self, keys=None, jitter=10):
        """依次启动插件（保留随机间隔以免同时打接口，最后一个不等）。"""
        order = list(keys if keys is not None else sorted(self.map))
        started, skipped = [], []
        for index, key in enumerate(order):
            ok, msg = self.start(key)
            if ok:
                started.append(key)
                logger.info("插件线程已启动：%s", msg)
            else:
                skipped.append(key)
                logger.error("插件 %s 启动失败：%s", key, msg)
            if index < len(order) - 1:
                delay = random.randint(0, jitter)
                if not runtime.interruptible_sleep(delay) and runtime.is_shutdown_requested():
                    break
        return started, skipped

    def wait_all(self, grace=None):
        """等待插件线程收尾（沿用原有关停宽限逻辑）。"""
        grace = SHUTDOWN_GRACE if grace is None else grace
        for key in list(self.started_keys()):
            thread = self.threads.get(key)
            if thread is None:
                continue
            deadline = None
            while thread.is_alive():
                thread.join(timeout=0.5)
                if runtime.is_shutdown_requested():
                    if deadline is None:
                        deadline = time.monotonic() + grace
                    elif time.monotonic() > deadline:
                        logger.warning("插件线程 %s 未在 %s 秒内收尾，放弃等待", thread.name, grace)
                        break


tried_exit = False


def flush_logging():
    """把日志缓冲区刷到磁盘（优雅退出时必须调用，否则日志可能丢最后几行）。"""
    for handler in list(logger.handlers):
        try:
            handler.flush()
        except Exception:
            pass


def exit_app(signal_, frame):
    """Ctrl+C / SIGTERM：第一次请求优雅退出，第二次强制退出。"""
    global tried_exit
    if not tried_exit:
        tried_exit = True
        runtime.request_shutdown()
        echo("正在退出...若无响应，请再按一次 Ctrl+C，或使用 python Steamauto.py --stop --force", dual=True)
        return
    logger.warning("程序已经强制退出")
    flush_logging()
    os._exit(exit_code.get())


# ---------------------------------------------------------------- 运行时支撑

def _setup_runtime_state():
    """写 PID 与状态文件，供 CLI 的 status/stop 使用。"""
    state = {
        "pid": os.getpid(),
        "version": CURRENT_VERSION,
        "started_at": _RUNTIME_START,
        "mode": "daemon" if os.environ.get("STEAMAUTO_DAEMON") == "1" else "foreground",
        "log_file": getattr(f_handler, "baseFilename", None),
        "console_log": os.environ.get("STEAMAUTO_CONSOLE_LOG"),
    }
    try:
        os.makedirs(static.RUN_FOLDER, exist_ok=True)
        with open(static.PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        logger.warning("写入 PID 文件失败：%s", e)
    daemon.write_state(**state)


def _register_hot_appliers():
    """注册支持热改的配置键 -> 应用函数。"""
    import utils.logger as log_mod

    runtime.register_hot_applier("log_level", log_mod.set_log_level)
    runtime.register_hot_applier(
        "log_retention_days",
        lambda v: setattr(log_mod, "log_retention_days", int(v or 0)),
    )
    runtime.register_hot_applier("no_pause", lambda v: setattr(static, "no_pause", bool(v)))
    runtime.register_hot_applier(
        "manual_confirm_delivery",
        lambda v: setattr(static, "manual_confirm_delivery", bool(v)),
    )
    runtime.register_hot_applier(
        "console_echo.enable",
        lambda v: log_mod.set_console_echo_settings(enable=bool(v)),
    )
    runtime.register_hot_applier(
        "console_echo.min_level",
        lambda v: log_mod.set_console_echo_settings(min_level=v),
    )
    runtime.register_hot_applier(
        "console_echo.dual_write_events",
        lambda v: log_mod.set_console_echo_settings(dual_events=bool(v)),
    )


def _update_config_value(key, value):
    """就地更新内存中的 config 字典。

    插件在构造时拿到的是**同一个 dict 引用**（见 get_plugins_enabled 的 kwargs
    注入），所以原地修改后，插件下一轮读取配置即可看到新值。
    """
    parts = config_writer.split_path(key)
    if not parts:
        return
    cur = config
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


# ---- 控制通道指令处理 ----

def _ctl_ping(_args):
    started = _PLUGIN_RUNTIME.started_keys() if _PLUGIN_RUNTIME else []
    pending = _PLUGIN_RUNTIME.pending_keys() if _PLUGIN_RUNTIME else []
    failed = _PLUGIN_RUNTIME.failed_keys() if _PLUGIN_RUNTIME else []
    return {
        "pid": os.getpid(),
        "version": CURRENT_VERSION,
        "uptime": round(time.time() - _RUNTIME_START, 1),
        "started_at": _RUNTIME_START,
        "mode": "daemon" if os.environ.get("STEAMAUTO_DAEMON") == "1" else "foreground",
        "plugins": started,
        "plugins_pending": pending,
        "plugins_failed": failed,
        "log_file": getattr(f_handler, "baseFilename", None),
        "config_file": CONFIG_FILE_PATH,
        "shutdown_requested": runtime.is_shutdown_requested(),
    }


def _ctl_account_status(args):
    """返回各平台（以及 Steam）的账号状态。

    运行中的进程是**最优状态源**：它持有真实会话，能直接回答「现在能不能用」，
    而不像本地探测只能看文件在不在。
    """
    from utils import accounts

    live = bool(args.get("live", True))
    target = args.get("platform")
    if target:
        name = accounts.resolve(target)
        if name is None:
            if str(target).lower() == "steam":
                return {"steam": _steam_state_live()}
            raise ValueError("未知平台：%s" % target)
        return {"accounts": {name: accounts.account_state(name, cfg=config, live=live)}}

    return {
        "accounts": {p: accounts.account_state(p, cfg=config, live=live) for p in accounts.platforms()},
        "steam": _steam_state_live(),
    }


def _steam_state_live():
    """用本进程持有的 steam_client 给出真实的 Steam 会话状态。"""
    from utils import accounts

    user = accounts.steam_username()
    info = {
        "platform": "steam",
        "display": "Steam",
        "configured": bool(user),
        "logged_in": False,
        "connected": False,
        "account": user or None,
        "source": "运行中的进程",
        "error": None,
    }
    client = _STEAM_CLIENT
    if user and client is not None and not isinstance(client, OfflineSteamClient):
        try:
            alive = bool(client.is_session_alive())
            info["logged_in"] = alive
            info["connected"] = alive
            if not alive:
                info["error"] = "会话已失效（正在自动刷新或需重新登录）"
        except Exception as e:  # noqa: BLE001
            info["error"] = "会话检测失败：%s" % (e,)
    elif not user:
        info["error"] = "未配置 Steam 用户名（可选；不影响买卖/上架等免鉴权功能）"
    else:
        info["error"] = "当前为离线模式（未登录 Steam）；买卖/上架等功能不受影响"
    return info


def _ctl_plugin_retry(args):
    """登录成功后动态启动某个平台插件（D1b）。

    同时 `request_wake()` 打断插件正在进行的 interval 等待，让它立刻进入下一轮
    （否则最多要等一个完整 interval 才生效）。
    """
    from utils import accounts

    platform = args.get("platform")
    key = args.get("plugin_key")
    if not key and platform:
        name = accounts.resolve(platform) or platform
        key = accounts.PLUGIN_KEY.get(name)
    if not key:
        raise ValueError("缺少 plugin_key 或 platform 参数")

    if _PLUGIN_RUNTIME is None:
        return {"ok": False, "message": "插件管理器未就绪"}

    ok, message = _PLUGIN_RUNTIME.start(key)
    if args.get("wake"):
        runtime.request_wake()
    echo("平台登录后重试插件 %s：%s" % (key, message), dual=True)
    return {
        "ok": ok,
        "plugin": key,
        "message": message,
        "plugins": _PLUGIN_RUNTIME.started_keys(),
        "plugins_pending": _PLUGIN_RUNTIME.pending_keys(),
        "wake": bool(args.get("wake")),
    }


def _ctl_shutdown(_args):
    runtime.request_shutdown()
    return {"message": "已收到停止请求，正在优雅退出", "pid": os.getpid()}


def _ctl_config_get(args):
    key = args.get("key") or ""
    found, value = config_writer.get_value(config, key)
    if not found:
        raise ValueError("配置项不存在: %s" % key)
    return {"key": key, "value": value}


def _ctl_config_apply(args):
    """把配置文件里的新值应用到运行中的进程。"""
    key = args.get("key") or ""
    if not key:
        raise ValueError("缺少 key 参数")
    new_cfg = config_writer.load_config(CONFIG_FILE_PATH)
    found, value = config_writer.get_value(new_cfg, key)
    if not found:
        return {"applied": False, "needs_restart": True, "reason": "配置项 %s 在配置文件中不存在" % key}
    _update_config_value(key, value)

    applied, err = runtime.apply_hot_config(key, value)
    if applied:
        return {"applied": True, "key": key, "value": value, "reason": "已立即生效"}
    if key in HOT_INTERVAL_PREFIXES:
        # 插件每轮重新读取配置，所以改 interval 无需额外回调即可生效
        return {"applied": True, "key": key, "value": value, "reason": "插件每轮读取配置，已立即生效"}
    if key in HOT_KEYS:
        return {"applied": False, "needs_restart": True, "reason": err or "热应用失败"}
    return {"applied": False, "needs_restart": True, "reason": "该配置项需重启程序后生效"}


def _ctl_config_reload(_args):
    """整体重读配置文件并重新应用。"""
    import utils.logger as log_mod

    new_cfg = config_writer.load_config(CONFIG_FILE_PATH)
    config.clear()
    config.update(new_cfg)
    settings = log_mod.apply_log_settings(config)
    static.no_pause = bool(config.get("no_pause", static.no_pause))
    static.manual_confirm_delivery = config.get("manual_confirm_delivery", True)
    return {"reloaded": True, "keys": len(config), "log_level": settings["level"]}


def _ctl_log_level(args):
    import utils.logger as log_mod

    level = args.get("level")
    if not level:
        raise ValueError("缺少 level 参数")
    return {"level": log_mod.set_log_level(level)}


def _start_control_server():
    """按配置启动回环控制通道；未启用或启动失败时返回 None。"""
    cfg_control = config.get("control")
    if not isinstance(cfg_control, dict):
        cfg_control = {}
    if cfg_control.get("enable", True) is False:
        echo("控制通道已按配置关闭（将无法使用 stop/status/config 等运行时命令）")
        return None
    raw_port = os.environ.get("STEAMAUTO_CONTROL_PORT") or cfg_control.get("port") or control.DEFAULT_PORT
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = control.DEFAULT_PORT

    handlers = {
        "ping": _ctl_ping,
        "shutdown": _ctl_shutdown,
        "account.status": _ctl_account_status,
        "plugin.retry": _ctl_plugin_retry,
        "config.get": _ctl_config_get,
        "config.apply": _ctl_config_apply,
        "config.reload": _ctl_config_reload,
        "log.level": _ctl_log_level,
    }
    server = control.ControlServer(handlers, port=port, logger=logger)
    ok, err = server.start()
    if not ok:
        echo("控制通道未能启动：%s" % err)
        return None
    daemon.write_state(port=server.bound_port, host=server.host)
    logger.info("控制通道已启动：%s:%s", server.host, server.bound_port)
    return server


def _teardown_runtime(control_server=None, handed_off=False):
    """退出前收尾：关控制通道、清状态文件、刷日志。

    handed_off=True 表示服务已移交给后台进程 —— 此时**不能**清状态文件，
    那是后台刚写好的，清掉会让它失联。
    """
    try:
        if control_server is not None:
            control_server.stop()
    finally:
        if not handed_off:
            daemon.clear_state()
        flush_logging()


# 主函数
def _is_interactive():
    """当前是否处于「有人看着的交互终端」。

    后台进程（daemon）与 GUI 子进程都没有交互终端，不能走扫码/短信这类引导。
    终端判定统一走 accounts.stdin_is_interactive（它额外排除了 Windows 上
    NUL 设备被误判为终端的情况），这里只再要求 stdout 也是终端。
    """
    from utils import accounts

    if static.no_pause:
        return False
    if not accounts.stdin_is_interactive():
        return False
    try:
        return bool(sys.stdout and sys.stdout.isatty())
    except Exception:
        return False


def _onboard_first_run():
    """首次运行引导：提示登录 BUFF / UU；两者都失败则转到后台继续运行。

    :return: True 表示继续在本进程启动服务；False 表示不需要（已转后台或已提示）。
    """
    from utils import accounts

    echo("=" * 62, dual=True)
    echo("检测到首次运行。", dual=True)
    echo("提示：未登录 Steam 也能使用各平台的买卖/上架/改价/行情功能；", dual=True)
    echo("      仅「自动发货」需要 Steam 会话（未登录时转为人工确认）。", dual=True)
    echo("=" * 62, dual=True)
    echo("")

    if not _is_interactive():
        echo("当前无交互终端，跳过登录引导。", dual=True)
        echo("之后可执行：python Steamauto.py --login uu   （或 buff / c5 / eco）", dual=True)
        return True

    # 配置文件已生成但默认可能没启用对应插件，这里先提示
    results = {}
    for platform in ("buff", "uu"):
        plugin_key = accounts.PLUGIN_KEY[platform]
        echo("---- 登录 %s ----" % accounts.DISPLAY[platform])
        ok, msg, _detail = accounts.login(platform)
        results[platform] = ok
        if ok:
            echo("[成功] %s" % msg, dual=True)
            section = config.get(plugin_key)
            if isinstance(section, dict) and not section.get("enable"):
                echo("提示：%s 的插件当前未启用，需要执行：" % accounts.DISPLAY[platform])
                echo("      python Steamauto.py --config --set %s.enable true" % plugin_key)
        else:
            echo("[失败] %s" % msg, dual=True)
        echo("")

    if any(results.values()):
        echo("至少一个平台登录成功，继续启动。", dual=True)
        return True

    echo("BUFF 与 UU 均未登录成功。为避免占用终端，将转到后台继续运行。", dual=True)
    echo("之后可随时补登录（凭据保存后会自动通知后台进程，无需重启）：", dual=True)
    echo("    python Steamauto.py --login buff", dual=True)
    echo("    python Steamauto.py --login uu", dual=True)
    echo("查看各平台状态：python Steamauto.py --status account", dual=True)

    ok, msg = daemon.spawn_background()
    if ok:
        echo("[OK] %s" % msg, dual=True)
    else:
        echo("转入后台失败：%s" % msg, dual=True)
        echo("可手动执行：python Steamauto.py --start", dual=True)
    return False


def _serve_until_shutdown(poll=1.0):
    """主服务循环：保持进程与控制通道存活，直到收到关停请求。

    三种情形：
    - 有插件在跑 → 等它们结束（沿用带宽限的 wait_all）；
    - 插件全退出但仍有「待登录」的平台 → 继续待命，等 `--login` 通过控制通道
      把对应插件动态启动起来（D1b）；
    - 没有任何待启动插件 → 返回，让调用方走正常退出流程。
    """
    while not runtime.is_shutdown_requested():
        if _PLUGIN_RUNTIME.started_keys():
            _PLUGIN_RUNTIME.wait_all()
        if runtime.is_shutdown_requested():
            return
        pending = _PLUGIN_RUNTIME.pending_keys()
        if not pending:
            return
        # 待命：插件实例仍在（等登录），不退出进程，只维持控制通道
        runtime.interruptible_sleep(poll)


def _other_instance_pid():
    """返回**另一个**正在运行的 Steamauto 实例的 PID；没有则返回 None。

    注意要排除自身：前台进程会把自己的 PID 写进 state 文件，
    因此 `is_running()` 在 handoff 阶段必然为真，那是自己。
    """
    running, existing = daemon.is_running()
    if not running:
        return None
    pid = existing.get("pid")
    if pid is None or pid == os.getpid():
        return None
    return pid


def _should_handoff_to_background():
    """是否应当在初始化完成后转入后台。

    仅「无参数启动」（cli 会设 STEAMAUTO_BG_HANDOFF=1）且当前不是后台进程时为真；
    `run` 子命令保持传统前台常驻行为（便于盯日志调试）。
    """
    if os.environ.get("STEAMAUTO_BG_HANDOFF") != "1":
        return False
    if os.environ.get("STEAMAUTO_DAEMON") == "1":
        return False  # 后台进程自身不再二次转后台
    return True


def _handoff_to_background(plugin_count, control_server=None):
    """把服务移交后台进程。返回 True 表示移交完成，调用方应结束当前前台进程。

    取舍说明：不做「实时转发后台输出到本终端」，后台的输出落在它自己的
    控制台日志里（logs/console-*.log），用户可随时用 `--log` 翻阅。

    **必须先让出运行时资源**：前台此刻已经写了 PID/state 文件并绑定了控制端口，
    若不先释放，spawn 出的后台进程会因「PID 已被占用」被判定为已在运行而启动失败，
    且控制端口也会冲突。
    """
    # 二次防线：正常情况下 main 开头的单实例检查已拦住，这里再确认一次
    other_pid = _other_instance_pid()
    if other_pid is not None:
        echo("检测到 Steamauto 已在运行（PID %s），本次启动取消。" % other_pid, dual=True)
        return True

    if control_server is not None:
        control_server.stop()
    daemon.clear_state()

    ok, msg = daemon.spawn_background()
    if not ok:
        logger.error("转入后台失败：%s", msg)
        echo("转入后台失败：%s" % msg, dual=True)
        echo("可手动执行：python Steamauto.py --start", dual=True)
        return True  # 仍然结束前台，避免用户以为已经后台运行却有两个实例

    echo("=" * 62, dual=True)
    echo("已转入后台运行（插件数：%d），控制台交还。" % plugin_count, dual=True)
    echo("  翻阅日志：python Steamauto.py --log")
    echo("  运行状态：python Steamauto.py --status")
    echo("  停止运行：python Steamauto.py --stop")
    echo("  前台运行：python Steamauto.py --run    （需盯日志时用）")
    echo("=" * 62, dual=True)
    return True


def main():
    global config, _STEAM_CLIENT, _PLUGIN_RUNTIME
    # GUI/后台启动的子进程无交互终端：出错时不等待按键（pause 自动跳过）
    if os.environ.get("STEAMAUTO_NO_PAUSE") == "1":
        static.no_pause = True
    # 初始化
    init_status = init_files_and_params()
    if init_status == 0:
        pause()
        return 1
    elif init_status == 1:
        # 首次运行：先做登录引导，再决定是否在本进程继续启动服务
        if not _onboard_first_run():
            pause()
            return 0

    runtime.clear_shutdown()
    runtime.clear_wake()

    # 单实例保护：已有进程在跑时不应再起一个（两个实例会重复操作同一批平台账号）。
    # **必须在 _setup_runtime_state() 之前判断** —— 后者会用自己的 PID 覆盖 state 文件，
    # 之后就再也看不到已有实例了（实测会导致「初始化后转后台」再拉起一个后台进程）。
    other_pid = _other_instance_pid()
    if other_pid is not None:
        echo("检测到 Steamauto 已在运行（PID %s），本次启动取消。" % other_pid, dual=True)
        echo("  查看状态：python Steamauto.py --status")
        echo("  重启服务：python Steamauto.py --restart")
        return 0

    _setup_runtime_state()
    _register_hot_appliers()
    control_server = _start_control_server()
    handed_off = False
    try:
        steam_client = login_to_steam(config)
        if steam_client is None:
            # 降级运行：Steam 登录失败不退出，使用离线占位客户端，仅运行无需 Steam session 的功能
            username = ""
            try:
                with open(STEAM_ACCOUNT_INFO_FILE_PATH, "r", encoding=get_encoding(STEAM_ACCOUNT_INFO_FILE_PATH)) as f:
                    username = json5.loads(f.read()).get("steam_username", "")
            except Exception:
                pass
            echo("Steam 登录失败，进入离线模式（用户名：%s），仅运行无需 Steam session 的功能，发货转为人工确认" % (username or "未设置"), dual=True)
            steam_client = OfflineSteamClient(username)
            if steam_client_mutex.get(username) is None:
                steam_client_mutex[username] = threading.Lock()
        _STEAM_CLIENT = steam_client

        # 仅用于获取启用的插件
        import_all_plugins()
        plugins_map = get_plugins_enabled(steam_client, steam_client_mutex.get(steam_client.username))
        if not plugins_map:
            echo("未启用任何插件, 请检查配置文件是否正确！", dual=True)
            pause()
            return 1

        # 插件管理器：失败插件不再丢弃，留给「登录后动态启动」
        _PLUGIN_RUNTIME = PluginRuntime(plugins_map)
        echo("初始化完成, 开始运行插件!", dual=True)

        # 无参数启动时：初始化完成后转入后台，把控制台还给用户。
        # 放在 start_all() 之前，避免前台与后台各跑一遍插件（重复请求平台接口）。
        if _should_handoff_to_background():
            _handoff_to_background(len(plugins_map), control_server)
            handed_off = True
            return 0

        time.sleep(0.1)
        started, skipped = _PLUGIN_RUNTIME.start_all()
        for key in skipped:
            echo("插件 %s 未能启动（%s）" % (key, _PLUGIN_RUNTIME.failed.get(key, "未启用")), dual=True)

        if started:
            if steam_client is not None:
                send_notification(steam_client, "Steamauto 已经成功登录Steam并开始运行")
            echo("Steamauto 已开始运行，插件数：%d" % len(started), dual=True)
        else:
            # 关键：不再直接退出。多为「平台未登录」导致，需要留一个活着的进程
            # 让用户之后用 --login 补登录（登录成功会通过控制通道动态启动插件）。
            echo("所有插件都未能启动（多为平台未登录），进入待命状态。", dual=True)
            echo("补登录后会自动启动对应插件，无需重启：", dual=True)
            echo("    python Steamauto.py --login buff", dual=True)
            echo("    python Steamauto.py --login uu", dual=True)
            echo("查看状态：python Steamauto.py --status account", dual=True)
        if skipped:
            echo("待启动插件：%s" % ", ".join(skipped), dual=True)

        _serve_until_shutdown()

        if runtime.is_shutdown_requested():
            echo("已按要求停止全部插件，程序退出。", dual=True)
            return 0
        echo("由于所有插件已经关闭,程序即将退出...", dual=True)
        pause()
        return 1
    finally:
        _teardown_runtime(control_server, handed_off=handed_off)


# 程序运行开始处
if __name__ == "__main__":
    sys.excepthook = handle_global_exception
    signal.signal(signal.SIGINT, exit_app)
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, exit_app)
        except (ValueError, OSError, AttributeError):
            pass
    if not os.path.exists(SESSION_FOLDER):
        os.mkdir(SESSION_FOLDER)
    _exit = main()
    exit_code.set(_exit)  # type: ignore
    flush_logging()
    sys.exit(_exit)
