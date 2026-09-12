"""命令行界面。

**命令清单的唯一事实来源是 `_HELP_SECTIONS` + `_HELP_NOTES`**，由 `render_help()`
渲染成树状（`python Steamauto.py --help` 输出它），避免文档与实际命令各自过期。

设计要点：

- 本模块刻意**轻量**：不导入 utils.logger（不产生日志文件）、不导入 Steam 客户端
  与插件，因此 `status` / `--log` / `--login` 这类命令开销极小。
  需要跑服务的 `run` / `start` 才惰性 `import Steamauto`。
- 两种写法并存：子命令（`status` / `logs` / `config` …）与 flag
  （`--status` / `--log` / `--login` / `--logout` / `--help`）。
- 无参数启动 = 前台完成初始化后**自动转后台**并把控制台交还（见 Steamauto.main 的
  handoff）；需前台常驻请用 `run`。
- 含中文的输出一律按**显示宽度**对齐（`_display_width` / `_pad`），
  因为中文在终端占 2 列而 `len()` 只数 1。
"""

import argparse
import json
import os
import sys
import unicodedata

from utils import config_writer, control, daemon, static

DEFAULT_CONTROL_PORT = control.DEFAULT_PORT


# ------------------------------------------------------- 终端显示宽度（中文对齐）
# 放在模块最前：--help 渲染（render_help）与状态渲染都要用。


def _display_width(text):
    """终端**显示宽度**：东亚宽/全角字符占 2 列。

    中文/全角标点在终端里占 2 列，而 `len()` 只数 1。因此用
    `"%-22s" % name` 这类按字符数填充的格式化会让含中文的列错位 ——
    必须按显示宽度计算补白。
    """
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(text))


def _pad(text, cols):
    """按显示宽度右侧补空格（中文对齐必需）。"""
    text = str(text)
    return text + " " * max(0, cols - _display_width(text))


def _clip(text, cols):
    """按显示宽度截断，超宽时以 … 结尾。"""
    text = str(text)
    if _display_width(text) <= cols:
        return text
    out, width = "", 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width + cw > cols - 1:
            break
        out += ch
        width += cw
    return out + "…"


# ------------------------------------------------------------------ 输出工具

def _p(msg=""):
    print(msg)


def _err(msg):
    print(msg, file=sys.stderr)


def _ok(msg):
    _p("[OK] %s" % msg)


# ------------------------------------------------------------------ 参数解析

def build_parser():
    """构建参数解析器 —— **全部为 `--` 长选项风格**，不再提供子命令写法。

    约定：命令词一律用 `--xxx`；值（平台名 / 配置键 / 行数）直接跟在后面，
    不加 `--`，例如 `--config --get buff_auto_accept_offer.interval`。
    """
    parser = argparse.ArgumentParser(
        prog="Steamauto",
        description="Steamauto 命令行：运行控制、账号管理、日志查看与运行时配置修改",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )

    # ---- 帮助 / 全局 ----
    parser.add_argument("-h", "--help", action="store_true", help="列出全部可用操作")
    parser.add_argument("--port", type=int, help="覆盖控制通道端口（默认取配置 control.port）")
    parser.add_argument("--instance", metavar="NAME", help="指定实例（数据目录 instances/<name>；default 为默认实例）")
    parser.add_argument("--instances", action="store_true", help="列出所有实例及运行状态")

    # ---- 运行控制 ----
    parser.add_argument("--run", action="store_true", help="前台运行（初始化后转后台；需常驻前台时用）")
    parser.add_argument("-d", "--daemon", action="store_true", help="配合 --run：直接后台启动（不做前台初始化）")
    parser.add_argument("--start", action="store_true", help="后台启动")
    parser.add_argument("--stop", action="store_true", help="停止运行中的进程（默认优雅停止）")
    parser.add_argument("--restart", action="store_true", help="重启")
    parser.add_argument("--force", action="store_true", help="配合 --stop/--restart：强制结束（不优雅）")
    parser.add_argument("--timeout", type=float, default=daemon.STOP_TIMEOUT, help="等待优雅退出的秒数")

    # ---- 状态 ----
    # 不带值（或 all）= 所有实例状态；<实例名> = 指定实例进程状态；account = 账号状态
    parser.add_argument(
        "--status",
        nargs="?",
        const="all",
        metavar="[<实例名>|all|account]",
        help="查看状态：默认 all（所有实例）；<实例名> = 指定实例进程状态；account = 账号状态",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（配合 --status）")
    parser.add_argument("--no-live", action="store_true", help="只读本地凭据，不联网校验（更快）")

    # ---- 账号 ----
    parser.add_argument("--login", metavar="PLATFORM", help="登录平台：buff/uu/c5/eco（可逗号分隔）")
    parser.add_argument("--logout", metavar="PLATFORM", help="登出平台：buff/uu/c5/eco（可逗号分隔）")

    # ---- 日志 ----
    parser.add_argument(
        "--log",
        nargs="?",
        const="",
        metavar="[N|console|app]",
        help="翻阅日志：可给行数（--log 200），或 console/app 指定来源；默认取最新日志",
    )
    parser.add_argument("-n", "--lines", type=int, default=50, help="配合 --log：显示末尾行数（默认 50）")
    parser.add_argument("-f", "--follow", action="store_true", help="配合 --log：持续跟随输出（Ctrl+C 退出）")
    parser.add_argument("--console", action="store_true", help="配合 --log：看后台运行的控制台日志")
    parser.add_argument("--file", metavar="PATH", help="配合 --log：指定日志文件路径")

    # ---- 配置 ----
    parser.add_argument("--config", action="store_true", help="配置操作（需配合 --get/--set/--unset/--list/--reload）")
    parser.add_argument("--get", metavar="KEY", help="读取配置值（点分路径）")
    parser.add_argument("--set", dest="set_pair", nargs="+", metavar="KEY VALUE...", help="修改配置值；多个值即数组，如 --set k A B 或 --set k '[\"A\",\"B\"]'")
    parser.add_argument("--unset", metavar="KEY", help="删除配置项")
    parser.add_argument("--list", action="store_true", help="列出全部配置")
    parser.add_argument("--reload", action="store_true", help="让运行中的进程重读配置")
    parser.add_argument("--str", action="store_true", help="配合 --set：强制按字符串写入")
    parser.add_argument("--no-apply", action="store_true", help="配合 --set/--unset：只写文件，不通知运行中的进程")

    # ---- 平台 API（只读命令；操作名与参数交给 utils.api_cli 解析）----
    # REMAINDER 捕获操作名 + 参数；真实执行走 cli.main 开头的 argv[0] 检测（api_cli），
    # 这里的 flag 让 `--help` 文档可被 parser 校验、也作 _dispatch 兜底。
    parser.add_argument("--buff", nargs=argparse.REMAINDER, help="BUFF 平台操作（balance/nickname/search/on-sale/sell-history/waiting-offer）")
    parser.add_argument("--uu", nargs=argparse.REMAINDER, help="UU 平台操作（nickname/inventory/on-sale/leased-out/wait-deliver/buy-order）")
    parser.add_argument("--c5", nargs=argparse.REMAINDER, help="C5 平台操作（balance/orders/check-key）")
    parser.add_argument("--eco", nargs=argparse.REMAINDER, help="ECO 平台操作（balance/on-sale/inventory）")

    # ---- 调试 ----
    parser.add_argument("--ctl", metavar="COMMAND", help="直接向控制通道发指令")
    parser.add_argument("ctl_args", nargs="*", help="配合 --ctl 的参数，形如 key=value")

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
    """运行服务。

    - 子进程（STEAMAUTO_DAEMON=1，由 spawn_background 拉起）：前台常驻跑服务。
    - 主进程：spawn 子进程；--run 跟随日志到前台（便于盯日志）。
    """
    if getattr(args, "daemon", False):
        return cmd_start(args)  # --run -d = 直接后台，不 follow

    # 子进程：前台常驻跑服务（不再 spawn，避免无限套娃）
    if os.environ.get("STEAMAUTO_DAEMON") == "1":
        import Steamauto  # 惰性导入：仅真正跑服务时才加载网络/插件/日志等重型依赖

        try:
            return Steamauto.main() or 0
        except KeyboardInterrupt:
            return 0
        except Exception as e:  # noqa: BLE001
            _err("运行失败：%s" % (e,))
            return 1

    # 主进程：spawn 子进程
    ok, msg = daemon.spawn_background(port=getattr(args, "port", None))
    if not ok:
        _err(msg)
        return 1
    _ok(msg)

    # --run（前台常驻语义）：跟随子进程日志到前台
    if getattr(args, "foreground", True):
        _follow_console_log()
    return 0


def _follow_console_log():
    """跟随后台子进程的控制台日志（Ctrl+C 退出）。"""
    path = daemon.latest_log_file("console") or daemon.latest_log_file("any")
    if path and os.path.exists(path):
        _p("正在跟随日志（Ctrl+C 退出）：%s" % path)
        daemon.follow(path)


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


def cmd_status_for_instance(name, args):
    """查看指定实例的进程状态（读该实例的 state 文件，不切换当前实例）。"""
    from utils import instance

    bd = instance.base_dir(name)
    state_file = os.path.join(bd, "run", "steamauto.state.json")
    state = {}
    try:
        with open(state_file, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        pass

    pid = state.get("pid")
    try:
        running = bool(pid) and daemon.pid_alive(int(pid))
    except (TypeError, ValueError):
        running = False

    if getattr(args, "json", False):
        data = dict(state)
        data.update({"instance": name, "running": running})
        _p(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if running else 3

    _p("实例：%s" % name)
    _p("数据目录：%s" % bd)
    if running:
        _p("状态：运行中（PID %s）" % pid)
        if state.get("version"):
            _p("版本：%s" % state["version"])
        if state.get("port"):
            _p("控制端口：%s" % state["port"])
        if state.get("log_file"):
            _p("日志文件：%s" % state["log_file"])
    else:
        _p("状态：未运行")
    return 0 if running else 3


def _show_log(kind="any", lines=50, follow=False, file=None):
    """展示日志。返回退出码。

    :param kind: "app" 应用日志 / "console" 后台控制台日志 / "any" 最新任意日志
    """
    if file:
        path = file
    else:
        # 指定类别找不到时回落到「最新任意日志」，避免空手而归
        path = daemon.latest_log_file(kind) or (daemon.latest_log_file("any") if kind != "any" else None)
    if not path or not os.path.exists(path):
        _err("未找到日志文件（目录：%s）" % static.LOGS_FOLDER)
        return 1
    if follow:
        _p("正在跟随 %s（Ctrl+C 退出）" % path)
        daemon.follow(path)
        return 0
    tail_lines = daemon.tail(path, lines)
    _p("== %s（末尾 %d 行）==" % (path, len(tail_lines)))
    for line in tail_lines:
        _p(line)
    return 0


def cmd_log_flag(args):
    """`--log [N|console|app] [-n N] [-f] [--console] [--file PATH]`：翻阅日志。

    取值优先级：
      1. `--log <N>`（行数）或 `--log console|app`（来源）
      2. `-n/--lines` 指定行数；`--console` 指定来源为控制台日志；`--file` 直接指定文件
    默认取「最新修改的日志文件」（不论应用日志还是控制台日志），
    因为转后台后用户最常问的是「刚才到底发生了什么」。
    """
    raw = (getattr(args, "log", "") or "").strip().lower()
    kind, lines = "any", getattr(args, "lines", 50)

    if raw:
        if raw.isdigit():
            lines = int(raw)          # --log 200 优先于 -n
        elif raw in ("console", "c"):
            kind = "console"
        elif raw in ("app", "a"):
            kind = "app"
        else:
            _err("无法识别的 --log 参数：%s（可用：行数 / console / app）" % raw)
            return 2

    # --console 仅在 --log 未指定来源时生效
    if kind == "any" and getattr(args, "console", False):
        kind = "console"

    return _show_log(
        kind=kind,
        lines=lines,
        follow=getattr(args, "follow", False),
        file=getattr(args, "file", None),
    )


# ---- config ----

def _default_keys():
    """默认配置里的全部点分键（用于提示未知键）。

    expand_arrays=True：把数组下标也纳入，这样 `--set <name>.0 x` 这类
    针对单个元素的写法不会被误报为未知键。
    """
    try:
        cfg = config_writer.load_config(static.CONFIG_FILE_PATH)
    except Exception:
        return set()
    return {k for k, _ in config_writer.flatten(cfg, expand_arrays=True) if k}


def _prepare_literal(raw, as_string=False):
    """把命令行原始值转成 (JSON5 字面量, Python 值)。

    raw 可以是单个字符串，也可以是列表 —— 后者来自 `--set <KEY> <V1> <V2> ...`
    这种「多值即成数组」的写法，方便在 PowerShell 里免去转义 JSON 引号的痛苦。
    """
    if isinstance(raw, list):
        values = [str(v) if as_string else config_writer.coerce_value(v) for v in raw]
        return config_writer.encode_value(values), values
    if as_string:
        return config_writer.encode_value(str(raw)), str(raw)
    value = config_writer.coerce_value(raw)
    return config_writer.encode_value(value), value


def _format_config_value(value):
    """把配置值格式化成给人看的形式。

    数组用**单行 JSON 数组**（`["A", "B"]`）—— 这是它最直观的「数组形态」，
    也便于直接复制回 `--config --set`。
    对象用多行缩进（嵌套结构可读）。
    """
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def cmd_config_get(args):
    """`--config --get <KEY>`：读取配置值。"""
    cfg = config_writer.load_config(static.CONFIG_FILE_PATH)
    found, value = config_writer.get_value(cfg, args.key)
    if not found:
        _err("配置项不存在：%s" % args.key)
        return 1
    _p(_format_config_value(value))
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
    """`--config --list`：列出全部配置。

    数组按数组形态显示（`["AK", "A1"]`），不拆成 `key.0` / `key.1` 条目。
    """
    cfg = config_writer.load_config(static.CONFIG_FILE_PATH)
    if args.json:
        _p(json.dumps(cfg, ensure_ascii=False, indent=2))
        return 0
    flat = [(k, v) for k, v in config_writer.flatten(cfg) if k]
    width = max((_display_width(k) for k, _ in flat), default=0)
    for key, value in flat:
        _p("%s = %s" % (_pad(key, width), _format_config_value(value)))
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
    """`--ctl <COMMAND> [k=v ...]`：直接向控制通道发指令。"""
    command = args.ctl
    payload = {}
    for item in args.ctl_args:
        if "=" not in item:
            _err("参数需为 key=value 形式：%s" % item)
            return 2
        k, v = item.split("=", 1)
        payload[k] = config_writer.coerce_value(v)
    ok, resp = _request(command, payload)
    if not ok:
        _err(resp)
        return 1
    _p(json.dumps(resp, ensure_ascii=False, indent=2))
    return 0


# ------------------------------------------------------------------ 帮助

#: 命令清单（`--help` 的唯一事实来源）：(分组名, [(命令写法, 说明)])
#: 渲染成树状；命令列按**显示宽度**对齐（中文占 2 列）。
#: 约定：命令词一律带 `--`；值（<平台>/<KEY>/N）不加。
_HELP_SECTIONS = [
    ("运行", [
        ("python Steamauto.py", "启动；初始化完成后自动转入后台"),
        ("python Steamauto.py --run [-d|--daemon]", "前台常驻运行；带 -d/--daemon 则直接后台启动"),
        ("python Steamauto.py --start", "后台启动"),
        ("python Steamauto.py --stop [--force]", "停止（默认优雅停止）"),
        ("python Steamauto.py --restart [--force]", "重启"),
        ("python Steamauto.py --status [<实例名>|all|account]", "查看实例状态（默认 all 所有实例；account 账号状态）"),
    ]),
    ("实例", [
        ("python Steamauto.py --instances", "列出所有实例及运行状态"),
        ("python Steamauto.py --instance <NAME> --run", "启动指定实例（独立数据目录，多开）"),
        ("python Steamauto.py --instance <NAME> --status", "查看指定实例状态"),
    ]),
    ("日志", [
        ("python Steamauto.py --log", "翻阅最新日志（末尾 50 行）"),
        ("python Steamauto.py --log 200", "翻阅末尾 200 行"),
        ("python Steamauto.py --log console", "看后台运行的控制台日志"),
        ("python Steamauto.py --log app", "看应用（技术）日志"),
        ("python Steamauto.py --log [-f|--follow]", "持续跟随输出（Ctrl+C 退出）"),
        ("python Steamauto.py --log -n 200", "等价写法：用 -n 指定行数"),
        ("python Steamauto.py --log --console", "等价写法：看后台控制台日志"),
        ("python Steamauto.py --log --file <PATH>", "查看指定日志文件"),
    ]),
    ("账号", [
        ("python Steamauto.py --status account [--json] [--no-live]", "查看各平台登录 / 连接状态"),
        ("python Steamauto.py --login <平台>", "登录（需交互终端：BUFF 扫码 / UU 短信）"),
        ("python Steamauto.py --logout <平台>", "登出（清除凭据与相关配置项）"),
    ]),
    ("配置", [
        ("python Steamauto.py --config --get <KEY>", "读取配置值（点分路径）"),
        ("python Steamauto.py --config --set <KEY> <VALUE> [--str] [--no-apply]", "修改配置值（保留注释）"),
        ("python Steamauto.py --config --unset <KEY>", "删除配置项"),
        ("python Steamauto.py --config --list [--json]", "列出全部配置"),
        ("python Steamauto.py --config --reload", "让运行中的进程重读配置"),
    ]),
    ("平台 API", [
        ("python Steamauto.py --buff <OP> [--table]", "BUFF 余额/搜索/在售/成交（--buff --help 看全部操作）"),
        ("python Steamauto.py --uu <OP> [--table]", "UU 库存/在售/待发货（--uu --help 看全部操作）"),
        ("python Steamauto.py --c5 <OP> [--table]", "C5 余额/订单（--c5 --help 看全部操作）"),
        ("python Steamauto.py --eco <OP> [--table]", "ECO 余额/在售/库存（--eco --help 看全部操作）"),
    ]),
    ("调试", [
        ("python Steamauto.py --ctl <COMMAND> [k=v ...]", "直接向控制通道发指令"),
        ("python Steamauto.py --help", "显示本帮助"),
    ]),
]

#: --help 末尾的补充说明
_HELP_NOTES = [
    "平台名（--login/--logout）可用：buff | uu | c5 | eco；逗号分隔多个，大小写不敏感",
    "  别名：buffapi | uuyoupin | c5game | ecosteam",
    "直接运行时先在前台完成初始化（你能看到登录与插件检查过程），",
    "  随后自动转入后台并把控制台交还；后台输出记录在日志文件里。",
    "未登录 Steam 也能使用各平台的买卖 / 上架 / 改价 / 行情功能；",
    "  仅「自动发货」需要 Steam 会话，未登录时会转为人工确认。",
    "登录成功后若程序正在后台运行，会自动通知它立即重试该平台，无需重启。",
]


def render_help():
    """把命令清单渲染成树状文本（`--help` 的输出）。

    树状规则：除最后一条分支外都用 `├─`；组内子项若其后还有分支，
    前缀带竖线 `│` 以延续视觉连接；最后一条分支用 `└─`，其子项用空格缩进。
    """
    lines = ["Steamauto 可用操作", ""]

    sections = list(_HELP_SECTIONS)
    for idx, (section, items) in enumerate(sections):
        # 「说明」也是树的一条分支，因此只有它（或没有说明时的最后一个分组）用 └─
        is_final_branch = idx == len(sections) - 1 and not _HELP_NOTES
        lines.append("%s %s" % ("└─" if is_final_branch else "├─", section))
        cont = "   " if is_final_branch else "│  "
        cmd_cols = max(_display_width(cmd) for cmd, _ in items)
        for j, (cmd, desc) in enumerate(items):
            lines.append(
                "%s%s %s  %s"
                % (cont, "└─" if j == len(items) - 1 else "├─", _pad(cmd, cmd_cols), desc)
            )

    if _HELP_NOTES:
        lines.append("└─ 说明")
        for note in _HELP_NOTES:
            lines.append("   %s" % note)

    return "\n".join(lines)


def cmd_help(_args=None):
    _p(render_help())
    return 0


def cmd_instances():
    """`--instances`：列出所有实例及其运行状态。"""
    from utils import instance

    entries = instance.list_instances()
    current = instance.current_name()
    name_cols = max(_display_width(e["name"]) for e in entries)
    _p("实例列表（数据目录隔离，互不影响）")
    for idx, e in enumerate(entries):
        mark = "└─" if idx == len(entries) - 1 else "├─"
        star = "*" if e["name"] == current else " "
        status = "运行中 (PID %s)" % e["pid"] if e["running"] else "未运行"
        _p("%s %s %s  %s" % (mark, star, _pad(e["name"], name_cols), status))
        _p("   └─ %s" % e["base_dir"])
    _p("")
    _p("* 为当前实例。启动/操作某实例：--instance <name> <命令>，如 --instance alice --run")
    return 0


def _collect_status(args):
    """收集各平台状态。优先问运行中的进程（D7），不可用时退回本地探测。

    :return: (accounts_map, steam_state, source_label)
    """
    from utils import accounts

    live = not getattr(args, "no_live", False)
    running, state = daemon.is_running()

    if running and state.get("control_ok"):
        ok, resp = control.request(
            "account.status", {"live": live}, port=state.get("port"), timeout=accounts.NET_TIMEOUT + 10
        )
        if ok and isinstance(resp, dict) and resp.get("accounts"):
            return resp.get("accounts") or {}, resp.get("steam") or {}, "运行中的进程（实时）"

    label = "本地探测"
    if running:
        label += "（控制通道不可达）"
    return accounts.all_account_states(live=live), accounts.steam_state(live=live), label


# ------------------------------------------------------------------ 账号

#: 状态字段标签（同一列组内宽度一致，保证后列对齐）
_LABELS = {
    "configured": ("已配置", "未配置"),
    "logged_in": ("已登录", "未登录"),
    "connected": ("连接可用", "未校验"),  # 未联网校验时不算失败，故用「未校验」
}


def _render_status(accounts_map, steam, source, live):
    """以树状结构展示各平台账号状态。

    对齐说明：平台名按**显示宽度**补白（中文占 2 列），状态字段用定宽标签，
    因此每列都能对齐；说明/账号放在子行，避免长文本把表格撑歪。

    树状符号（├ ─ └ 属 East_Asian_Width=Ambiguous，不同终端渲染宽度不同）只要
    每行前缀字符完全相同就不会错位，故无需按宽度补算。
    """
    from utils import accounts

    infos = [accounts_map.get(p) or accounts._blank_state(p) for p in accounts.platforms()]
    if steam:
        infos.append(steam)

    def name_of(info):
        return str(info.get("display") or info.get("platform") or "?")

    name_cols = max(_display_width(name_of(i)) for i in infos)

    _p("各平台账号状态")
    _p("├─ 来源：%s" % source)
    _p("├─ 联网校验：%s" % ("是" if live else "否"))

    for idx, info in enumerate(infos):
        last = idx == len(infos) - 1
        # 连接列是最后一列，无需补白（补了也会被 rstrip 丢弃）
        _p(
            "%s %s  %s  %s  %s"
            % (
                "└─" if last else "├─",
                _pad(name_of(info), name_cols),
                _LABELS["configured"][0 if info.get("configured") else 1],
                _LABELS["logged_in"][0 if info.get("logged_in") else 1],
                _LABELS["connected"][0 if info.get("connected") else 1],
            )
        )

        detail = []
        if info.get("account"):
            detail.append("账号：%s" % _clip(info["account"], 40))
        if info.get("balance") is not None:
            detail.append("可用余额：¥%s" % info["balance"])
        if info.get("error"):
            detail.append(str(info["error"]))
        if detail:
            _p("   └─ %s" % "｜".join(detail))

    _p("")
    _p("提示：登录用 `--login <平台>`；查看原始数据用 `--status account --json`。")


def cmd_account_status(args):
    """`--status account`：展示各平台登录 / 连接状态。

    主题（process/account）的判别已在 `_dispatch_status` 完成，这里不再重复校验。
    """
    from utils import accounts

    running, _state = daemon.is_running()
    if not running:
        _err("程序未运行，无法查询账号状态（余额/登录态等运行时数据需程序在运行）。")
        _err("请先启动：python Steamauto.py --start 或 --run")
        return 1

    accounts_map, steam, source = _collect_status(args)
    if getattr(args, "json", False):
        _p(
            json.dumps(
                {"source": source, "live": not getattr(args, "no_live", False), "accounts": accounts_map, "steam": steam},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    _render_status(accounts_map, steam, source, not getattr(args, "no_live", False))
    return 0


def _resolve_targets(raw, flag):
    from utils import accounts

    names, bad = accounts.parse_platforms(raw, default_all=False)
    if bad:
        _err("无法识别的平台：%s（可选：%s）" % (", ".join(bad), " / ".join(accounts.platforms())))
        return None
    if not names:
        _err("请指定平台，例如 %s uu" % flag)
        return None
    return names


def cmd_login(args):
    from utils import accounts

    names = _resolve_targets(args.login, "--login")
    if names is None:
        return 2

    overall = 0
    for name in names:
        _p("== 登录 %s ==" % accounts.DISPLAY[name])
        ok, msg, _detail = accounts.login(name)
        if ok:
            _ok(msg)
        else:
            _err("[失败] %s" % msg)
            overall = 1
            continue

        delivered, note = accounts.notify_runtime(name)
        if delivered:
            _ok("已通知运行中的进程立即重试该平台：%s" % note)
        elif note == "程序未在运行":
            _p("程序未在运行；凭据已保存，下次启动自动生效")
        else:
            _p("凭据已保存，但未能通知运行中的进程（%s）" % note)
            _p("若程序正在运行，可执行 `restart` 或等待其下一轮轮询")
    return overall


def cmd_logout(args):
    from utils import accounts

    names = _resolve_targets(args.logout, "--logout")
    if names is None:
        return 2

    overall = 0
    for name in names:
        _p("== 登出 %s ==" % accounts.DISPLAY[name])
        ok, msg, cleared = accounts.logout(name)
        if not ok:
            _err("[失败] %s" % msg)
            overall = 1
            continue
        _ok(msg)
        if cleared:
            for item in cleared:
                _p("    已清除：%s" % item)
        else:
            _p("    没有可清除的凭据")

        delivered, note = accounts.notify_runtime(name)
        if delivered:
            _ok("已通知运行中的进程：%s" % note)
        elif note != "程序未在运行":
            _p("未能通知运行中的进程（%s）" % note)
    return overall


# ------------------------------------------------------------------ 入口

def _parse_args(parser, argv):
    """解析参数；参数错误时给出帮助而不是直接崩溃。

    argparse 遇到非法参数会 print 错误后 `sys.exit(2)`，而子命令自己的 `-h`
    则是 `sys.exit(0)`。这里统一接住：正常退出（0）沿用其行为，出错时补一份
    操作列表，方便用户立刻看到正确用法。

    :return: Namespace，或 None 表示应当直接以失败退出。
    """
    try:
        return parser.parse_args(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
        if code == 0:
            raise
        _p("")
        _p("下面是全部可用操作：")
        cmd_help()
        return None


def _extract_instance(argv):
    """提取并移除 ``--instance <name>`` / ``--instance=<name>``。

    返回 (剩余 argv, 实例名或 None)。实例名在 parse 之前提取，因为切换实例
    会影响 static 路径，而 static 路径在 build_parser 之前就要被各模块使用。
    """
    name = None
    out = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--instance":
            if i + 1 < len(argv):
                name = argv[i + 1]
                i += 2
                continue
            out.append(a)  # 缺值：保留 --instance，交给 parser 报「expected one argument」
            i += 1
            continue
        if a.startswith("--instance="):
            name = a.split("=", 1)[1]
            i += 1
            continue
        out.append(a)
        i += 1
    return out, name


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # 1. 提取并激活 --instance <name>（不带则默认 default；影响所有命令的数据目录）
    argv, instance_name = _extract_instance(argv)
    from utils import instance

    try:
        instance.activate(instance_name or instance.DEFAULT_NAME)
    except ValueError as e:
        _err(str(e))
        return 2
    # 2. 平台 API 命令（--buff/--uu/--c5/--eco）有独立的子命令树与参数约定，
    #    不走主 parser（避免 REMAINDER 吞掉全局 flag），直接交给 api_cli。
    if argv and argv[0] in ("--buff", "--uu", "--c5", "--eco"):
        from utils import api_cli

        return api_cli.main(argv[0].lstrip("-"), argv[1:])
    parser = build_parser()
    if not argv:
        # 无参数 = 完整初始化后自动转后台（把控制台还给用户）。
        # 需前台常驻请用 `--run`。
        return cmd_run(argparse.Namespace(daemon=False, port=None, foreground=False))

    args = _parse_args(parser, argv)
    if args is None:
        return 2

    return _dispatch(args)


def _dispatch(args):
    """把解析结果路由到对应处理函数（全部为 `--` 长选项）。"""
    # 注意用 `is not None` 而非真值判断：`--login ""` 这种空值必须走 cmd_login
    # 去报错，否则会被静默忽略、fall-through 成「前台运行」。
    if getattr(args, "help", False):
        return cmd_help(args)

    # ---- 实例列表 ----
    if getattr(args, "instances", False):
        return cmd_instances()

    # ---- 平台 API（兜底：--buff 等不在 argv[0] 时，如 `--table --buff search x`）----
    for plat in ("buff", "uu", "c5", "eco"):
        rest = getattr(args, plat, None)
        if rest:
            from utils import api_cli

            return api_cli.main(plat, rest)

    # ---- 配置 ----
    if args.config or any((args.get, args.set_pair, args.unset, args.list, args.reload)):
        return _dispatch_config(args)

    # ---- 账号 ----
    if args.login is not None:
        return cmd_login(args)
    if args.logout is not None:
        return cmd_logout(args)

    # ---- 状态 ----
    if args.status is not None:
        return _dispatch_status(args)

    # ---- 日志 ----
    if args.log is not None or args.console or args.file:
        return cmd_log_flag(args)
    if args.follow:
        _err("--follow 需要配合 --log 使用，例如：python Steamauto.py --log -f")
        return 2

    # ---- 运行控制 ----
    if args.run:
        if args.daemon:
            return cmd_start(args)
        return cmd_run(argparse.Namespace(daemon=False, port=args.port, foreground=True))
    if args.daemon:
        # 允许 --daemon 单独使用（等价 --start）
        return cmd_start(args)
    if args.start:
        return cmd_start(args)
    if args.stop:
        return cmd_stop(args)
    if args.restart:
        return cmd_restart(args)

    # ---- 调试 ----
    if args.ctl is not None:
        return cmd_ctl(args)

    _err("未指定操作")
    _p("")
    return cmd_help(args)


def _dispatch_status(args):
    """`--status [<实例名>|all|account]`：默认 all（所有实例）；实例名 = 指定实例进程状态；account = 账号状态。"""
    topic = (args.status or "all").strip().lower()
    if topic in ("account", "accounts", "acct", "账号"):
        return cmd_account_status(args)
    if topic in ("all", "全部"):
        return cmd_instances()
    if topic in ("process", "proc", "进程"):
        # 兼容旧写法：process = 当前实例的进程状态
        from utils import instance

        topic = instance.current_name()
    from utils import instance

    try:
        name = instance.normalize(topic)
    except ValueError as e:
        _err(str(e))
        return 2
    return cmd_status_for_instance(name, args)


def _dispatch_config(args):
    """`--config --get/--set/--unset/--list/--reload`。

    flag 名与命令函数里的属性名不同（`--get <KEY>` → `key`、`--set <KEY> <VALUE>` →
    `key`/`value`），这里统一做适配，避免每个命令函数各自解析参数。
    """
    set_pair = args.set_pair
    if set_pair is not None and len(set_pair) < 2:
        _err("--set 至少需要两个值：--config --set <KEY> <VALUE> [<VALUE> ...]")
        return 2

    # 注意用 lambda 惰性构造：直接在列表里写 set_pair[0] 会在 set_pair 为 None 时
    # 立即抛 TypeError（列表元素是马上求值的，不是 lazy 的）。
    # 值形态：2 个 → 标量（或 JSON 字面量）；3 个及以上 → 数组（多值即成数组）。
    def _set_value():
        if len(set_pair) == 2:
            return set_pair[1]
        return list(set_pair[1:])

    ops = [
        (args.get is not None, cmd_config_get, lambda: {"key": args.get}),
        (set_pair is not None, cmd_config_set, lambda: {"key": set_pair[0], "value": _set_value()}),
        (args.unset is not None, cmd_config_unset, lambda: {"key": args.unset}),
        (args.list, cmd_config_list, lambda: {}),
        (args.reload, cmd_config_reload, lambda: {}),
    ]
    chosen = [(fn, make_extra) for flag, fn, make_extra in ops if flag]

    if not chosen:
        if args.config:
            _err("--config 需要配合 --get/--set/--unset/--list/--reload 使用")
        else:
            _err("未指定配置操作")
        return 2
    if len(chosen) > 1:
        _err("一次只能执行一个配置操作（--get/--set/--unset/--list/--reload 只能选一个）")
        return 2

    fn, make_extra = chosen[0]
    for name, value in make_extra().items():
        setattr(args, name, value)
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
