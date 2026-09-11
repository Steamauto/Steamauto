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
# 轻量 CLI 前置分发
# ---------------------------------------------------------------------------
# status / config / logs 等子命令只需要 utils.cli 那一小撮轻量模块。若走完整
# import 链，会顺带创建日志文件、加载 Steam 客户端与插件，既慢又会在 logs/ 里
# 留下一堆无意义的日志。因此这里在**导入重型依赖之前**就完成分发。
# 只有 run（前台运行）才继续往下执行本模块的其余代码。
if __name__ == "__main__":
    _argv = list(sys.argv[1:])
    _first = _argv[0] if _argv else ""
    _daemon_flag = "-d" in _argv or "--daemon" in _argv
    _is_light = _first in ("start", "stop", "restart", "status", "logs", "config", "ctl")
    _is_help = _first in ("-h", "--help")
    _is_daemon_run = _first in ("run", "") and _daemon_flag
    if _first == "-d" or _first == "--daemon":
        _is_daemon_run = True
    if _is_light or _is_help or _is_daemon_run:
        from utils.cli import main as _cli_main

        sys.exit(_cli_main(_argv))

import json5
from colorama import Fore, Style

import utils.static as static
from steampy.client import SteamClient
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
_ACTIVE_PLUGINS = []
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
    else:
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
    global config
    plugins_enabled = []
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
                plugins_enabled.append(plugin_instance)

    return plugins_enabled


def plugins_check(plugins_enabled):
    if len(plugins_enabled) == 0:
        logger.error("未启用任何插件, 请检查" + CONFIG_FILE_PATH + "是否正确! ")
        return []
    ok_plugins = []
    for plugin in plugins_enabled:
        try:
            if plugin.init():
                logger.error("插件 " + type(plugin).__name__ + " 初始化失败，已跳过")
            else:
                ok_plugins.append(plugin)
        except Exception as e:
            handle_caught_exception(e, known=True)
            logger.error("插件 " + type(plugin).__name__ + " 初始化异常，已跳过")
    return ok_plugins


def init_plugins_and_start(plugins_enabled):
    echo("初始化完成, 开始运行插件!", dual=True)
    time.sleep(0.1)
    if len(plugins_enabled) == 1:
        exit_code.set(plugins_enabled[0].exec())
    else:
        threads = []
        for plugin in plugins_enabled:
            threads.append(threading.Thread(target=plugin.exec, name="plugin-%s" % type(plugin).__name__))
        for thread in threads:
            random_jitter = random.randint(0, 10)
            thread.daemon = True
            thread.start()
            logger.info(f"插件线程 {thread.name} 已启动，等待 {random_jitter} 秒后启动下一个插件线程...")
            if not runtime.interruptible_sleep(random_jitter):
                break
        # 等待插件线程收尾。收到停止请求后只再宽限 SHUTDOWN_GRACE 秒，
        # 避免某个卡在网络请求里的插件把停止流程无限拖住。
        grace_deadline = None
        for thread in threads:
            while thread.is_alive():
                thread.join(timeout=0.5)
                if runtime.is_shutdown_requested():
                    if grace_deadline is None:
                        grace_deadline = time.monotonic() + SHUTDOWN_GRACE
                    elif time.monotonic() > grace_deadline:
                        logger.warning("插件线程 %s 未在 %s 秒内收尾，放弃等待", thread.name, SHUTDOWN_GRACE)
                        break
    if exit_code.get() != 0 and not runtime.is_shutdown_requested():
        logger.warning("所有插件都已经退出！这不是一个正常情况，请检查配置文件！")


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
        echo("正在退出...若无响应，请再按一次 Ctrl+C，或使用 python Steamauto.py stop --force", dual=True)
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
    return {
        "pid": os.getpid(),
        "version": CURRENT_VERSION,
        "uptime": round(time.time() - _RUNTIME_START, 1),
        "started_at": _RUNTIME_START,
        "mode": "daemon" if os.environ.get("STEAMAUTO_DAEMON") == "1" else "foreground",
        "plugins": [type(p).__name__ for p in _ACTIVE_PLUGINS],
        "log_file": getattr(f_handler, "baseFilename", None),
        "config_file": CONFIG_FILE_PATH,
        "shutdown_requested": runtime.is_shutdown_requested(),
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


def _teardown_runtime(control_server=None):
    """退出前收尾：关控制通道、清状态文件、刷日志。"""
    try:
        if control_server is not None:
            control_server.stop()
    finally:
        daemon.clear_state()
        flush_logging()


# 主函数
def main():
    global config
    # GUI/后台启动的子进程无交互终端：出错时不等待按键（pause 自动跳过）
    if os.environ.get("STEAMAUTO_NO_PAUSE") == "1":
        static.no_pause = True
    # 初始化
    init_status = init_files_and_params()
    if init_status == 0:
        pause()
        return 1
    elif init_status == 1:
        pause()
        return 0

    runtime.clear_shutdown()
    _setup_runtime_state()
    _register_hot_appliers()
    control_server = _start_control_server()
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
        # 仅用于获取启用的插件
        import_all_plugins()
        plugins_enabled = get_plugins_enabled(steam_client, steam_client_mutex.get(steam_client.username))
        # 检查插件是否正确初始化：失败插件跳过，成功插件继续运行
        plugins_enabled = plugins_check(plugins_enabled)
        if len(plugins_enabled) == 0:
            echo("所有插件都无法初始化, Steamauto即将退出！", dual=True)
            pause()
            return 1

        _ACTIVE_PLUGINS[:] = plugins_enabled
        if steam_client is not None:
            send_notification(steam_client, "Steamauto 已经成功登录Steam并开始运行")
            echo("Steamauto 已开始运行，插件数：%d" % len(plugins_enabled), dual=True)
            init_plugins_and_start(plugins_enabled)

        if runtime.is_shutdown_requested():
            echo("已按要求停止全部插件，程序退出。", dual=True)
            return 0
        echo("由于所有插件已经关闭,程序即将退出...", dual=True)
        pause()
        return 1
    finally:
        _teardown_runtime(control_server)


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
