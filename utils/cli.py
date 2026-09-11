"""命令行界面（D7）。

命令一览::

    python Steamauto.py                                  前台运行（等价于 run）
    python Steamauto.py run [-d|--daemon] [--port N]      运行；-d 转后台
    python Steamauto.py start [--port N]                  后台启动
    python Steamauto.py stop [--force]                    停止（默认优雅停止）
    python Steamauto.py restart [--port N] [--force]      重启
    python Steamauto.py status [--json]                   查看运行状态
    python Steamauto.py logs [-n N] [-f] [--console]      查看日志
    python Steamauto.py config get <key>                  读配置
    python Steamauto.py config set <key> <value> [--str] [--no-apply]
    python Steamauto.py config unset <key>
    python Steamauto.py config list [--json]              列出全部配置
    python Steamauto.py config reload                     让运行中的进程重读配置
    python Steamauto.py ctl <command> [k=v ...]           直接向控制通道发指令

本模块被设计为**轻量**：不导入 utils.logger（不产生日志文件）、不导入 Steam
客户端与插件，因此 `status`/`config` 之类命令开销极小。运行态相关逻辑在
`run` 时才惰性导入。
"""

import argparse
import json
import os
import sys

from utils import config_writer, control, daemon, runtime, static

# 需要在 import 重型依赖之前处理的轻量命令集合（见 Steamauto.py 顶部引导）
LIGHT_COMMANDS = ("start", "stop", "restart", "status", "logs", "config", "ctl")

DEFAULT_CONTROL_PORT = control.DEFAULT_PORT


# ------------------------------------------------------------------ 输出工具

def _p(msg=""):
    print(msg)


def _err(msg):
    print(msg, file=sys.stderr)


def _ok(msg):
    _p("[OK] %s" % msg)


# ------------------------------------------------------------------ 参数解析

def build_parser():
    parser = argparse.ArgumentParser(
        prog="Steamauto",
        description="Steamauto 命令行：运行控制、日志查看与运行时配置修改",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-d", "--daemon", action="store_true", help="等价于 start（后台启动）")
    parser.add_argument("--port", type=int, help="覆盖控制通道端口（默认取配置 control.port）")
    sub = parser.add_subparsers(dest="command", metavar="<命令>")

    p_run = sub.add_parser("run", help="运行（默认前台）")
    p_run.add_argument("-d", "--daemon", action="store_true", help="转后台启动")
    p_run.add_argument("--port", type=int, help="覆盖控制通道端口")

    p_start = sub.add_parser("start", help="后台启动")
    p_start.add_argument("--port", type=int, help="覆盖控制通道端口")

    p_stop = sub.add_parser("stop", help="停止运行中的进程")
    p_stop.add_argument("--force", action="store_true", help="强制结束（不优雅）")
    p_stop.add_argument("--timeout", type=float, default=daemon.STOP_TIMEOUT, help="等待优雅退出的秒数")

    p_restart = sub.add_parser("restart", help="重启")
    p_restart.add_argument("--port", type=int, help="覆盖控制通道端口")
    p_restart.add_argument("--force", action="store_true", help="停止阶段强制结束")

    p_status = sub.add_parser("status", help="查看运行状态")
    p_status.add_argument("--json", action="store_true", help="以 JSON 输出，便于脚本处理")

    p_logs = sub.add_parser("logs", help="查看日志")
    p_logs.add_argument("-n", "--lines", type=int, default=50, help="显示末尾行数（默认 50）")
    p_logs.add_argument("-f", "--follow", action="store_true", help="持续跟随输出（Ctrl+C 退出）")
    p_logs.add_argument("--console", action="store_true", help="查看后台运行的控制台回显日志")
    p_logs.add_argument("--file", help="指定日志文件路径")

    p_cfg = sub.add_parser("config", help="查看/修改配置（保留注释，运行中可热改）")
    cfg_sub = p_cfg.add_subparsers(dest="config_command", metavar="<子命令>")

    c_get = cfg_sub.add_parser("get", help="读取配置值")
    c_get.add_argument("key", help="点分路径，如 buff_auto_accept_offer.interval")

    c_set = cfg_sub.add_parser("set", help="修改配置值")
    c_set.add_argument("key")
    c_set.add_argument("value")
    c_set.add_argument("--str", action="store_true", help="强制按字符串写入（不解析为数字/布尔/数组）")
    c_set.add_argument("--no-apply", action="store_true", help="只写文件，不通知运行中的进程热应用")

    c_unset = cfg_sub.add_parser("unset", help="删除配置项")
    c_unset.add_argument("key")
    c_unset.add_argument("--no-apply", action="store_true", help="只写文件，不通知运行中的进程")

    c_list = cfg_sub.add_parser("list", help="列出全部配置")
    c_list.add_argument("--json", action="store_true", help="以 JSON 输出")

    cfg_sub.add_parser("reload", help="让运行中的进程重新读取配置文件")

    p_ctl = sub.add_parser("ctl", help="直接向控制通道发指令")
    p_ctl.add_argument("ctl_command", help="指令名，如 ping / shutdown")
    p_ctl.add_argument("ctl_args", nargs="*", help="参数，形如 key=value")

    return parser


# ------------------------------------------------------------------ 控制通道

def _control_endpoint(port=None):
    """解析要连接的控制通道 (host, port)。优先命令行参数，其次运行状态文件，最后配置。"""
    if port:
        return control.DEFAULT_HOST, int(port)
    state = daemon.read_state()
    if state.get("port"):
        return state.get("host") or control.DEFAULT_HOST, int(state["port"])
    cfg = config_writer.load_config(static.CONFIG_FILE_PATH)
    ctrl = cfg.get("control") if isinstance(cfg.get("control"), dict) else {}
    return control.DEFAULT_HOST, int(ctrl.get("port") or DEFAULT_CONTROL_PORT)


def _request(command, args=None, port=None):
    host, real_port = _control_endpoint(port)
    return control.request(command, args=args, host=host, port=real_port)


# ------------------------------------------------------------------ 各子命令

def cmd_run(args):
    """前台运行（带 --daemon 则转后台）。"""
    if getattr(args, "daemon", False):
        return cmd_start(args)
    if getattr(args, "port", None):
        os.environ["STEAMAUTO_CONTROL_PORT"] = str(args.port)
    import Steamauto  # 惰性导入：仅 run 时才加载网络/插件/日志等重型依赖

    try:
        return Steamauto.main() or 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:  # noqa: BLE001
        _err("运行失败：%s" % (e,))
        return 1


def cmd_start(args):
    port = getattr(args, "port", None)
    ok, msg = daemon.spawn_background(port=port)
    if ok:
        _ok(msg)
        return 0
    _err(msg)
    return 1


def cmd_stop(args):
    if getattr(args, "force", False):
        ok, msg = daemon.kill_running()
    else:
        ok, msg = daemon.stop_running(timeout=getattr(args, "timeout", daemon.STOP_TIMEOUT))
    if ok:
        _ok(msg)
        return 0
    _err(msg)
    return 1


def cmd_restart(args):
    running, _ = daemon.is_running()
    if running:
        if getattr(args, "force", False):
            ok, msg = daemon.kill_running()
        else:
            ok, msg = daemon.stop_running()
        _p(msg if ok else "停止阶段：%s" % msg)
        if not ok:
            return 1
    return cmd_start(args)


def cmd_status(args):
    lines, data = daemon.describe_status()
    if getattr(args, "json", False):
        _p(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("running") else 3
    for line in lines:
        _p(line)
    return 0 if data.get("running") else 3


def cmd_logs(args):
    if args.file:
        path = args.file
    elif args.console:
        path = daemon.latest_log_file(include_console=True)
        # 控制台日志文件名带 console- 前缀，优先取它
        folder = static.LOGS_FOLDER
        if os.path.isdir(folder):
            console_logs = [os.path.join(folder, n) for n in os.listdir(folder) if n.startswith("console-") and n.endswith(".log")]
            if console_logs:
                path = max(console_logs, key=os.path.getmtime)
    else:
        path = daemon.latest_log_file(include_console=False)
    if not path or not os.path.exists(path):
        _err("未找到日志文件（目录：%s）" % static.LOGS_FOLDER)
        return 1
    if args.follow:
        _p("正在跟随 %s（Ctrl+C 退出）" % path)
        daemon.follow(path)
        return 0
    lines = daemon.tail(path, args.lines)
    _p("== %s（末尾 %d 行）==" % (path, len(lines)))
    for line in lines:
        _p(line)
    return 0


# ---- config ----

def _default_keys():
    """默认配置里的全部点分键（用于提示未知键）。"""
    try:
        cfg = config_writer.load_config(static.CONFIG_FILE_PATH)
    except Exception:
        return set()
    return {k for k, _ in config_writer.flatten(cfg)}


def _prepare_literal(raw, as_string):
    """把命令行原始值转成 (JSON5 字面量, Python 值)。"""
    if as_string:
        return config_writer.encode_value(str(raw)), str(raw)
    value = config_writer.coerce_value(raw)
    return config_writer.encode_value(value), value


def cmd_config_get(args):
    cfg = config_writer.load_config(static.CONFIG_FILE_PATH)
    found, value = config_writer.get_value(cfg, args.key)
    if not found:
        _err("配置项不存在：%s" % args.key)
        return 1
    if isinstance(value, (dict, list)):
        _p(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        _p(value)
    return 0


def cmd_config_set(args):
    path = static.CONFIG_FILE_PATH
    if not os.path.exists(path):
        _err("配置文件不存在：%s" % path)
        return 1
    prepared = _prepare_literal(args.value, args.str)
    literal = prepared[0]
    try:
        changed, created, used = config_writer.set_value(path, args.key, None, literal=literal)
    except config_writer.ConfigEditError as e:
        _err("修改失败（配置未改动）：%s" % (e,))
        return 2
    if not changed:
        _p("配置项 %s 已经是 %s，无需修改" % (args.key, used))
    else:
        _ok("已%s %s = %s" % ("新增" if created else "更新", args.key, used))

    keys = _default_keys()
    if args.key not in keys and not any(k.startswith(args.key + ".") for k in keys):
        _p("提示：%s 不在默认配置项中，请确认拼写是否正确" % args.key)

    if args.no_apply:
        return 0
    return _apply_runtime(args.key, action="set")


def cmd_config_unset(args):
    path = static.CONFIG_FILE_PATH
    if not os.path.exists(path):
        _err("配置文件不存在：%s" % path)
        return 1
    try:
        changed, removed = config_writer.remove_value(path, args.key)
    except config_writer.ConfigEditError as e:
        _err("删除失败（配置未改动）：%s" % (e,))
        return 2
    if not changed:
        _p("配置项 %s 不存在，无需删除" % args.key)
        return 0
    _ok("已删除 %s" % args.key)
    if args.no_apply:
        return 0
    return _apply_runtime(args.key, action="unset")


def cmd_config_list(args):
    cfg = config_writer.load_config(static.CONFIG_FILE_PATH)
    if args.json:
        _p(json.dumps(cfg, ensure_ascii=False, indent=2))
        return 0
    flat = config_writer.flatten(cfg)
    width = max((len(k) for k, _ in flat), default=0)
    for key, value in flat:
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        _p("%-*s = %s" % (width, key, value))
    return 0


def cmd_config_reload(args):
    running, state = daemon.is_running()
    if not running:
        # 未运行：至少确认文件能被解析
        try:
            config_writer.load_config(static.CONFIG_FILE_PATH)
        except Exception as e:  # noqa: BLE001
            _err("配置文件无法解析：%s" % (e,))
            return 2
        _p("Steamauto 未在运行；已确认配置文件可正常解析，启动后即生效")
        return 0
    ok, resp = _request("config.reload", port=state.get("port"))
    if not ok:
        _err("重载失败：%s" % resp)
        return 1
    _ok("已通知运行中的进程重读配置：%s" % json.dumps(resp, ensure_ascii=False))
    return 0


def _apply_runtime(key, action="set"):
    """把改动推给运行中的进程做热应用。"""
    running, state = daemon.is_running()
    if not running:
        _p("程序未在运行，配置将在下次启动时生效")
        return 0
    if not state.get("control_ok"):
        _p("程序正在运行但控制通道不可达，本次改动将在进程重启后生效")
        return 0
    command = "config.apply" if action == "set" else "config.reload"
    payload = {"key": key} if action == "set" else {}
    ok, resp = _request(command, payload, port=state.get("port"))
    if not ok:
        _p("已写入配置文件，但热应用失败：%s" % resp)
        _p("改动将在进程重启后生效")
        return 0
    if action == "set":
        if resp.get("applied"):
            _ok("已热生效：%s" % json.dumps(resp, ensure_ascii=False))
        else:
            _p("已写入配置文件；%s" % resp.get("reason", "该项需重启后生效"))
    else:
        _ok("已生效：%s" % json.dumps(resp, ensure_ascii=False))
    return 0


def cmd_ctl(args):
    if args.ctl_command == "list-commands":
        # 便于发现可用指令
        pass
    payload = {}
    for item in args.ctl_args:
        if "=" not in item:
            _err("参数需为 key=value 形式：%s" % item)
            return 2
        k, v = item.split("=", 1)
        payload[k] = config_writer.coerce_value(v)
    ok, resp = _request(args.ctl_command, payload)
    if not ok:
        _err(resp)
        return 1
    _p(json.dumps(resp, ensure_ascii=False, indent=2))
    return 0


# ------------------------------------------------------------------ 入口

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        # 无参数 = 前台运行，保持与改造前一致
        return cmd_run(argparse.Namespace(daemon=False, port=None))
    args = parser.parse_args(argv)

    if args.command is None:
        if args.daemon:
            return cmd_start(args)
        return cmd_run(args)

    handlers = {
        "run": cmd_run,
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "status": cmd_status,
        "logs": cmd_logs,
        "config": _dispatch_config,
        "ctl": cmd_ctl,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 2
    return handler(args)


def _dispatch_config(args):
    if not getattr(args, "config_command", None):
        _err("请指定 config 子命令：get / set / unset / list / reload")
        return 2
    table = {
        "get": cmd_config_get,
        "set": cmd_config_set,
        "unset": cmd_config_unset,
        "list": cmd_config_list,
        "reload": cmd_config_reload,
    }
    return table[args.config_command](args)


if __name__ == "__main__":
    sys.exit(main())
