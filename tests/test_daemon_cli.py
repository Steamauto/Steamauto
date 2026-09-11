"""后台运行 / 日志双通道 / 运行时改配置 的回归测试。

用法（在项目根目录）::

    python -m pytest -q
    python -m unittest tests.test_daemon_cli -v

覆盖：
- config_writer：保留注释的定点配置编辑（改值/新增/删除/无引号键/CRLF）
- logger 双通道：echo 只上控制台、技术日志只进文件、ANSI 不落盘
- runtime：可中断 sleep、热应用注册表
- daemon：状态文件、PID 存活判定
- control：回环控制通道往返、token 鉴权、异常回传
- cli：参数解析与各子命令装配
- plugins：7 个插件的关停循环改造（静态源码校验，防止回退）

注意：本分支（plus）不包含 gui/，因此没有也不应有任何 gui 依赖。
"""

import io
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from utils import config_writer, control, daemon, runtime  # noqa: E402
from utils import logger as log_mod  # noqa: E402
from utils.static import DEFAULT_CONFIG_JSON  # noqa: E402

PLUGIN_FILES = [
    "BuffAutoAcceptOffer.py",
    "C5AutoAcceptOffer.py",
    "ECOsteam.py",
    "SteamAutoAcceptOffer.py",
    "UUAutoAcceptOffer.py",
    "UUAutoLeaseItem.py",
    "UUAutoSellItem.py",
]


def _comment_lines(text):
    """统计 // 注释行数，用于验证注释未被吞掉。"""
    return sum(1 for line in text.splitlines() if line.strip().startswith("//"))


def _make_record(level=logging.INFO, msg="msg", **flags):
    record = logging.LogRecord("steamauto-test", level, __file__, 0, msg, None, None)
    for key, value in flags.items():
        setattr(record, key, value)
    return record


# ============================================================ 配置定点编辑

class TestConfigWriter(unittest.TestCase):
    """D5.A 的核心：只改目标值，原样保留注释/缩进/行尾。"""

    def setUp(self):
        self.orig = DEFAULT_CONFIG_JSON
        self.comments = _comment_lines(self.orig)

    def test_set_scalar_keeps_comments(self):
        text, created = config_writer.set_value_in_text(self.orig, ["log_retention_days"], "14")
        self.assertFalse(created)
        self.assertEqual(_comment_lines(text), self.comments)
        self.assertEqual(json.loads(json.dumps(__import__("json5").loads(text)))["log_retention_days"], 14)
        self.assertIn("// 本地日志保留天数", text)

    def test_set_nested_scalar(self):
        text, _ = config_writer.set_value_in_text(self.orig, ["buff_auto_accept_offer", "interval"], "600")
        self.assertIn('"interval": 600', text)
        self.assertEqual(_comment_lines(text), self.comments)

    def test_set_array_element(self):
        text, _ = config_writer.set_value_in_text(self.orig, ["uu_auto_sell_item", "name", "0"], '"AK47"')
        import json5

        self.assertEqual(json5.loads(text)["uu_auto_sell_item"]["name"][0], "AK47")
        self.assertEqual(_comment_lines(text), self.comments)

    def test_add_root_key(self):
        text, created = config_writer.set_value_in_text(self.orig, ["brand_new_key"], '"hello"')
        import json5

        self.assertTrue(created)
        self.assertEqual(json5.loads(text)["brand_new_key"], "hello")
        self.assertEqual(_comment_lines(text), self.comments)

    def test_add_nested_key_creates_parent(self):
        old = '{\n  "a": 1\n}\n'
        text, created = config_writer.set_value_in_text(old, ["parent", "child"], "true")
        import json5

        self.assertTrue(created)
        self.assertEqual(json5.loads(text)["parent"]["child"], True)

    def test_unset_middle_and_last_member(self):
        import json5

        text, _ = config_writer.set_value_in_text(self.orig, ["brand_new_key"], '"hello"')
        text, removed = config_writer.remove_value_in_text(text, ["brand_new_key"])
        self.assertTrue(removed)
        self.assertNotIn("brand_new_key", text)
        self.assertTrue(json5.loads(text))

        text2, removed2 = config_writer.remove_value_in_text(text, ["source_code_auto_update"])
        self.assertTrue(removed2)
        self.assertNotIn("source_code_auto_update", text2)
        self.assertTrue(json5.loads(text2))

    def test_idempotent(self):
        text, _ = config_writer.set_value_in_text(self.orig, ["log_retention_days"], "14")
        text2, changed = config_writer.set_value_in_text(text, ["log_retention_days"], "14")
        self.assertEqual(text, text2)
        self.assertFalse(changed)

    def test_unquoted_keys_supported(self):
        """JSON5 允许无引号键，真实配置文件就是这么写的。"""
        import json5

        raw = '{\n  log_level: "info",\n  no_pause: false\n}\n'
        text, _ = config_writer.set_value_in_text(raw, ["no_pause"], "true")
        self.assertTrue(json5.loads(text)["no_pause"])
        self.assertIn("log_level:", text)  # 无引号键形态保留

    def test_preserves_crlf(self):
        raw = '{\r\n  "a": 1,\r\n  "b": 2\r\n}\r\n'
        text, created = config_writer.set_value_in_text(raw, ["c"], "3")
        self.assertTrue(created)
        self.assertEqual(text.count("\n") - text.count("\r\n"), 0)

    def test_invalid_edit_is_rejected(self):
        with self.assertRaises(config_writer.ConfigEditError):
            config_writer.validate_text("{ not valid json5 ")

    def test_file_roundtrip_and_validation(self):
        """写盘接口：改动后仍是合法 JSON5，且注释守恒。"""
        tmp = tempfile.mkdtemp(prefix="sa-cfg-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "config.json5")
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(self.orig)
        changed, created, literal = config_writer.set_value(path, "no_pause", True)
        self.assertTrue(changed)
        after = config_writer.read_text(path)
        self.assertEqual(_comment_lines(after), self.comments)
        self.assertTrue(config_writer.load_config(path)["no_pause"])

    def test_get_value_and_flatten(self):
        import json5

        data = json5.loads(self.orig)
        self.assertEqual(config_writer.get_value(data, "log_level"), (True, "info"))
        self.assertEqual(config_writer.get_value(data, "nope.x"), (False, None))
        self.assertTrue(any(k == "buff_auto_accept_offer.interval" for k, _ in config_writer.flatten(data)))

    def test_split_path(self):
        self.assertEqual(config_writer.split_path("a.b.0.c"), ["a", "b", "0", "c"])
        self.assertEqual(config_writer.split_path(""), [])

    def test_coerce_and_encode(self):
        self.assertIs(config_writer.coerce_value("true"), True)
        self.assertEqual(config_writer.coerce_value("14"), 14)
        self.assertEqual(config_writer.coerce_value("info"), "info")
        self.assertEqual(config_writer.coerce_value("[1,2]"), [1, 2])
        self.assertEqual(config_writer.encode_value("中文"), '"中文"')
        self.assertEqual(config_writer.encode_value(True), "true")


# ============================================================ 日志双通道

class TestLogChannels(unittest.TestCase):
    """需求 2：必要回显移出日志文件；日志文件不回显到命令行。"""

    def setUp(self):
        log_mod.set_log_level("info")
        log_mod.set_console_echo_settings(enable=True, min_level="warning", dual_events=True)

    def test_console_filter_allows_echo_and_warnings(self):
        f = log_mod.ConsoleEchoFilter()
        self.assertTrue(f.filter(_make_record(logging.INFO, "x", **{log_mod.ECHO_FLAG: True})))
        self.assertFalse(f.filter(_make_record(logging.INFO, "x")))
        self.assertFalse(f.filter(_make_record(logging.DEBUG, "x")))
        self.assertTrue(f.filter(_make_record(logging.WARNING, "x")))
        self.assertTrue(f.filter(_make_record(logging.ERROR, "x")))

    def test_console_filter_respects_disable(self):
        log_mod.set_console_echo_settings(enable=False)
        try:
            self.assertFalse(log_mod.ConsoleEchoFilter().filter(_make_record(logging.ERROR, "x")))
        finally:
            log_mod.set_console_echo_settings(enable=True)

    def test_file_filter_excludes_console_only_echo(self):
        f = log_mod.FileChannelFilter()
        self.assertFalse(f.filter(_make_record(logging.INFO, "x", **{log_mod.ECHO_FLAG: True})))
        self.assertTrue(f.filter(_make_record(logging.INFO, "x")))
        self.assertTrue(f.filter(_make_record(logging.WARNING, "x")))
        self.assertTrue(
            f.filter(_make_record(logging.INFO, "x", **{log_mod.ECHO_FLAG: True, log_mod.DUAL_FLAG: True}))
        )

    def test_file_filter_strips_ansi(self):
        record = _make_record(logging.WARNING, "\x1b[31mred\x1b[0m")
        log_mod.FileChannelFilter().filter(record)
        self.assertEqual(record.msg, "red")

    def test_file_channel_end_to_end(self):
        """真实落盘验证：echo 不进文件、技术日志进文件、dual 双写。"""
        uniq = "UNIT-%d" % time.time_ns()
        log_mod.echo("ECHOONLY-" + uniq)
        log_mod.echo("ECHODUAL-" + uniq, dual=True)
        log_mod.logger.info("TECHINFO-" + uniq)
        log_mod.logger.warning("WARN-" + uniq)
        log_mod.echo("\x1b[31mANSI" + uniq + "\x1b[0m")
        log_mod.f_handler.flush()
        with io.open(log_mod.f_handler.baseFilename, "r", encoding="utf-8") as f:
            data = f.read()
        self.assertNotIn("ECHOONLY-" + uniq, data)
        self.assertIn("ECHODUAL-" + uniq, data)
        self.assertIn("TECHINFO-" + uniq, data)
        self.assertIn("WARN-" + uniq, data)
        self.assertNotIn("\x1b", data)
        self.assertNotIn("ANSI" + uniq + "\x1b", data)

    def test_console_channel_end_to_end(self):
        """真实控制台通道验证：技术 INFO 不上控制台。"""
        uniq = "UNIT-%d" % time.time_ns()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(1)
        handler.addFilter(log_mod.ConsoleEchoFilter())
        handler.setFormatter(logging.Formatter("%(message)s"))
        log_mod.logger.addHandler(handler)
        try:
            log_mod.echo("CONSOLEONLY-" + uniq)
            log_mod.logger.info("TECHONLY-" + uniq)
            log_mod.logger.warning("WARNCONSOLE-" + uniq)
        finally:
            log_mod.logger.removeHandler(handler)
        out = stream.getvalue()
        self.assertIn("CONSOLEONLY-" + uniq, out)
        self.assertIn("WARNCONSOLE-" + uniq, out)
        self.assertNotIn("TECHONLY-" + uniq, out)

    def test_dual_write_can_be_disabled(self):
        log_mod.set_console_echo_settings(dual_events=False)
        try:
            uniq = "UNIT-%d" % time.time_ns()
            log_mod.echo("DUALOFF-" + uniq, dual=True)
            log_mod.f_handler.flush()
            with io.open(log_mod.f_handler.baseFilename, "r", encoding="utf-8") as f:
                data = f.read()
            self.assertNotIn("DUALOFF-" + uniq, data)
        finally:
            log_mod.set_console_echo_settings(dual_events=True)

    def test_file_filter_threshold(self):
        """普通记录按 log_level 过滤，echo(dual=True) 不受其限制。"""
        f = log_mod.FileChannelFilter()
        log_mod.set_log_level("warning")
        try:
            self.assertFalse(f.filter(_make_record(logging.INFO, "info")))
            self.assertFalse(f.filter(_make_record(logging.DEBUG, "debug")))
            self.assertTrue(f.filter(_make_record(logging.WARNING, "warn")))
            self.assertTrue(f.filter(_make_record(logging.ERROR, "err")))
            # 关键：dual 业务事件必须冒过等级门限留档
            self.assertTrue(
                f.filter(_make_record(logging.INFO, "evt", **{log_mod.ECHO_FLAG: True, log_mod.DUAL_FLAG: True}))
            )
            # 纯回显仍不进文件
            self.assertFalse(f.filter(_make_record(logging.INFO, "echo", **{log_mod.ECHO_FLAG: True})))
        finally:
            log_mod.set_log_level("info")

    def test_dual_event_survives_strict_log_level(self):
        """回归：log_level=error 时，dual 业务事件仍必须落盘。"""
        uniq = "UNIT-%d" % time.time_ns()
        log_mod.set_log_level("error")
        try:
            log_mod.echo("DUAL-AT-ERROR-" + uniq, dual=True)
            log_mod.logger.warning("SUPPRESSED-" + uniq)
            log_mod.f_handler.flush()
            with io.open(log_mod.f_handler.baseFilename, "r", encoding="utf-8") as f:
                data = f.read()
            self.assertIn("DUAL-AT-ERROR-" + uniq, data)
            self.assertNotIn("SUPPRESSED-" + uniq, data)
        finally:
            log_mod.set_log_level("info")

    def test_set_log_level(self):
        level = log_mod.set_log_level("debug")
        self.assertEqual(level, logging.DEBUG)
        self.assertEqual(log_mod.log_level, logging.DEBUG)
        self.assertTrue(log_mod.FileChannelFilter().filter(_make_record(logging.DEBUG, "x")))
        log_mod.set_log_level("info")

    def test_read_log_settings_defaults(self):
        s = log_mod.read_log_settings({})
        self.assertEqual(s["level"], logging.INFO)
        self.assertTrue(s["echo_enable"])
        self.assertTrue(s["dual_events"])
        s2 = log_mod.read_log_settings({"console_echo": {"enable": False, "min_level": "error"}})
        self.assertFalse(s2["echo_enable"])
        self.assertEqual(s2["echo_min_level"], logging.ERROR)

    def test_apply_log_settings(self):
        settings = log_mod.apply_log_settings({"log_level": "error", "console_echo": {"min_level": "info"}})
        self.assertEqual(settings["level"], logging.ERROR)
        log_mod.apply_log_settings({"log_level": "info", "console_echo": {"min_level": "warning"}})


# ============================================================ runtime

class TestRuntime(unittest.TestCase):
    def setUp(self):
        runtime.clear_shutdown()

    def tearDown(self):
        runtime.clear_shutdown()

    def test_interruptible_sleep_completes(self):
        start = time.monotonic()
        self.assertTrue(runtime.interruptible_sleep(0.3, step=0.05))
        self.assertGreaterEqual(time.monotonic() - start, 0.2)

    def test_interruptible_sleep_interrupted(self):
        def stopper():
            time.sleep(0.1)
            runtime.request_shutdown()

        threading.Thread(target=stopper, daemon=True).start()
        start = time.monotonic()
        self.assertFalse(runtime.interruptible_sleep(10.0, step=0.05))
        self.assertLess(time.monotonic() - start, 3.0)

    def test_hot_applier_registry(self):
        seen = []
        runtime.register_hot_applier("unit.test.key", seen.append)
        self.assertTrue(runtime.is_hot_key("unit.test.key"))
        self.assertIn("unit.test.key", runtime.hot_keys())
        applied, err = runtime.apply_hot_config("unit.test.key", 42)
        self.assertTrue(applied, err)
        self.assertEqual(seen, [42])
        applied, err = runtime.apply_hot_config("unit.unknown.key", 1)
        self.assertFalse(applied)
        self.assertIn("重启", err)

    def test_hot_applier_exception_reported(self):
        def boom(_v):
            raise RuntimeError("nope")

        runtime.register_hot_applier("unit.boom.key", boom)
        applied, err = runtime.apply_hot_config("unit.boom.key", 1)
        self.assertFalse(applied)
        self.assertIn("nope", err)


# ============================================================ daemon / 状态

class TestDaemonState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sa-run-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._patches = {
            "RUN_FOLDER": os.path.join(self.tmp, "run"),
            "PID_FILE": os.path.join(self.tmp, "run", "steamauto.pid"),
            "STATE_FILE": os.path.join(self.tmp, "run", "state.json"),
            "LOGS_FOLDER": os.path.join(self.tmp, "logs"),
        }
        self._orig = {}
        for key, value in self._patches.items():
            self._orig[key] = getattr(daemon.static, key)
            setattr(daemon.static, key, value)
        os.makedirs(self._patches["RUN_FOLDER"], exist_ok=True)
        os.makedirs(self._patches["LOGS_FOLDER"], exist_ok=True)

    def tearDown(self):
        for key, value in self._orig.items():
            setattr(daemon.static, key, value)

    def test_state_roundtrip(self):
        daemon.write_state(pid=1234, port=45917, version="test")
        state = daemon.read_state()
        self.assertEqual(state["pid"], 1234)
        self.assertEqual(state["port"], 45917)
        daemon.clear_state()
        self.assertEqual(daemon.read_state(), {})

    def test_read_pid_prefers_state_then_pidfile(self):
        daemon.write_state(pid=111)
        self.assertEqual(daemon.read_pid(), 111)
        daemon.clear_state()
        with io.open(self._patches["PID_FILE"], "w", encoding="utf-8") as f:
            f.write("222")
        self.assertEqual(daemon.read_pid(), 222)

    def test_pid_alive(self):
        self.assertTrue(daemon.pid_alive(os.getpid()))
        self.assertFalse(daemon.pid_alive(0))
        self.assertFalse(daemon.pid_alive(None))
        self.assertFalse(daemon.pid_alive(999999999))

    def test_is_running_false_without_state(self):
        running, state = daemon.is_running()
        self.assertFalse(running)

    def test_describe_status_not_running(self):
        lines, data = daemon.describe_status()
        self.assertFalse(data["running"])
        self.assertTrue(any("未运行" in line for line in lines))

    def test_latest_log_and_tail(self):
        path = os.path.join(self._patches["LOGS_FOLDER"], "2026-01-01-00-00-00.log")
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join("line%d" % i for i in range(1, 21)))
        self.assertEqual(daemon.latest_log_file(), path)
        self.assertEqual(daemon.tail(path, 3), ["line18", "line19", "line20"])

    def test_console_log_path_prefix(self):
        path = daemon.console_log_path()
        self.assertTrue(os.path.basename(path).startswith("console-"))


# ============================================================ 控制通道

def _free_port():
    s = socket.socket()
    s.bind((control.DEFAULT_HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestControlChannel(unittest.TestCase):
    def setUp(self):
        self.token = "unit-test-token"
        self.handlers = {
            "ping": lambda args: {"pong": True, "echo": args.get("x")},
            "boom": self._boom,
        }
        self.server = control.ControlServer(
            self.handlers, port=_free_port(), token=self.token
        )
        ok, err = self.server.start()
        self.assertTrue(ok, err)
        self.port = self.server.bound_port

    def tearDown(self):
        self.server.stop()

    @staticmethod
    def _boom(_args):
        raise ValueError("handler 内部错误")

    def test_roundtrip(self):
        ok, data = control.request("ping", args={"x": 7}, port=self.port, token=self.token)
        self.assertTrue(ok)
        self.assertTrue(data["pong"])
        self.assertEqual(data["echo"], 7)

    def test_token_rejected(self):
        ok, err = control.request("ping", port=self.port, token="wrong-token")
        self.assertFalse(ok)
        self.assertIn("token", err)

    def test_unknown_command(self):
        ok, err = control.request("nope", port=self.port, token=self.token)
        self.assertFalse(ok)
        self.assertIn("未知命令", err)

    def test_handler_exception_returned(self):
        ok, err = control.request("boom", port=self.port, token=self.token)
        self.assertFalse(ok)
        self.assertIn("handler 内部错误", err)

    def test_connection_refused_when_not_listening(self):
        ok, err = control.request("ping", port=_free_port(), token=self.token, timeout=1.0)
        self.assertFalse(ok)
        self.assertIsInstance(err, str)

    def test_ensure_token_creates_file(self):
        with tempfile.TemporaryDirectory(prefix="sa-token-") as tmp:
            orig = daemon.static.CONTROL_TOKEN_FILE
            daemon.static.CONTROL_TOKEN_FILE = os.path.join(tmp, "run", "control_token.txt")
            try:
                t1 = control.ensure_token()
                t2 = control.ensure_token()
                self.assertTrue(t1)
                self.assertEqual(t1, t2)
                self.assertTrue(os.path.exists(daemon.static.CONTROL_TOKEN_FILE))
            finally:
                daemon.static.CONTROL_TOKEN_FILE = orig


# ============================================================ CLI

class TestCli(unittest.TestCase):
    def setUp(self):
        from utils import cli

        self.cli = cli
        self.parser = cli.build_parser()
        self.tmp = tempfile.mkdtemp(prefix="sa-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg_path = os.path.join(self.tmp, "config.json5")
        with io.open(self.cfg_path, "w", encoding="utf-8", newline="") as f:
            f.write('{\n  // 保留这条注释\n  "log_level": "info",\n  "no_pause": false\n}\n')
        self._orig_cfg = cli.static.CONFIG_FILE_PATH
        self._orig_state = daemon.static.STATE_FILE
        self._orig_pid = daemon.static.PID_FILE
        cli.static.CONFIG_FILE_PATH = self.cfg_path
        daemon.static.STATE_FILE = os.path.join(self.tmp, "state.json")
        daemon.static.PID_FILE = os.path.join(self.tmp, "steamauto.pid")

    def tearDown(self):
        self.cli.static.CONFIG_FILE_PATH = self._orig_cfg
        daemon.static.STATE_FILE = self._orig_state
        daemon.static.PID_FILE = self._orig_pid

    def test_parse_subcommands(self):
        self.assertEqual(self.parser.parse_args(["start"]).command, "start")
        args = self.parser.parse_args(["stop", "--force"])
        self.assertTrue(args.force)
        args = self.parser.parse_args(["config", "set", "a.b", "3"])
        self.assertEqual((args.config_command, args.key, args.value), ("set", "a.b", "3"))
        args = self.parser.parse_args(["logs", "-n", "5", "-f", "--console"])
        self.assertEqual(args.lines, 5)
        self.assertTrue(args.follow)
        self.assertTrue(args.console)
        self.assertEqual(self.parser.parse_args(["--daemon"]).daemon, True)

    def test_no_args_is_foreground_run_namespace(self):
        # 不实际启动：只验证分发到 cmd_run
        called = {}

        def fake_run(args):
            called["daemon"] = getattr(args, "daemon", None)
            return 0

        orig = self.cli.cmd_run
        self.cli.cmd_run = fake_run
        try:
            self.assertEqual(self.cli.main([]), 0)
        finally:
            self.cli.cmd_run = orig
        self.assertFalse(called["daemon"])

    def test_config_get(self):
        self.assertEqual(self.cli.main(["config", "get", "log_level"]), 0)
        self.assertEqual(self.cli.main(["config", "get", "missing.key"]), 1)

    def test_config_set_keeps_comments_and_value(self):
        rc = self.cli.main(["--port", "1", "config", "set", "no_pause", "true", "--no-apply"])
        self.assertEqual(rc, 0)
        text = config_writer.read_text(self.cfg_path)
        self.assertIn("// 保留这条注释", text)
        self.assertIn('"no_pause": true', text)

    def test_config_set_string_flag(self):
        rc = self.cli.main(["config", "set", "log_level", "debug", "--no-apply"])
        self.assertEqual(rc, 0)
        self.assertIn('"log_level": "debug"', config_writer.read_text(self.cfg_path))

    def test_config_set_unknown_key_warns_but_writes(self):
        self.assertEqual(self.cli.main(["config", "set", "totally_new", "1", "--no-apply"]), 0)
        self.assertIn("totally_new", config_writer.read_text(self.cfg_path))

    def test_config_unset(self):
        self.assertEqual(self.cli.main(["config", "unset", "no_pause", "--no-apply"]), 0)
        self.assertNotIn("no_pause", config_writer.read_text(self.cfg_path))

    def test_config_list_returns_zero(self):
        self.assertEqual(self.cli.main(["config", "list"]), 0)
        self.assertEqual(self.cli.main(["config", "list", "--json"]), 0)

    def test_config_reload_when_not_running(self):
        self.assertEqual(self.cli.main(["config", "reload"]), 0)

    def test_status_not_running_exit_code(self):
        # 3 = 未运行（便于脚本区分）
        self.assertEqual(self.cli.main(["status"]), 3)
        self.assertEqual(self.cli.main(["status", "--json"]), 3)

    def test_stop_when_not_running(self):
        self.assertEqual(self.cli.main(["stop"]), 0)

    def test_logs_missing_file(self):
        orig = self.cli.static.LOGS_FOLDER
        self.cli.static.LOGS_FOLDER = self.tmp
        try:
            self.assertEqual(self.cli.main(["logs"]), 1)
        finally:
            self.cli.static.LOGS_FOLDER = orig

    def test_ctl_requires_key_value(self):
        self.assertEqual(self.cli.main(["ctl", "ping", "badarg"]), 2)

    def test_help_lists_operations(self):
        """--help 走自定义实现（D4b 要求列出可用操作），返回 0 而非 SystemExit。"""
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.cli.main(["--help"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        for expected in ("--login", "--logout", "--status account", "start", "stop", "config"):
            self.assertIn(expected, out, "--help 未列出 %s" % expected)

    def test_short_help_flag(self):
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.cli.main(["-h"])
        self.assertEqual(rc, 0)
        self.assertIn("--login", buf.getvalue())

    def test_unknown_command_shows_help(self):
        """无效子命令：argparse 报错后应补一份操作列表，返回 2。"""
        import contextlib

        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = self.cli.main(["definitely-not-a-command"])
        self.assertEqual(rc, 2)
        self.assertIn("--login", buf.getvalue())

    def test_control_endpoint_prefers_state(self):
        daemon.write_state(pid=os.getpid(), port=49999, host="127.0.0.1")
        host, port = self.cli._control_endpoint()
        self.assertEqual(port, 49999)
        host, port = self.cli._control_endpoint(port=1234)
        self.assertEqual(port, 1234)

    def test_light_commands_declared(self):
        for name in ("start", "stop", "restart", "status", "logs", "config", "ctl"):
            self.assertIn(name, self.cli.LIGHT_COMMANDS)


# ============================================================ 插件改造

class TestPluginShutdownLoops(unittest.TestCase):
    """防止插件循环被改回 while True / time.sleep，导致关停与热改失效。"""

    def _read(self, name):
        with io.open(os.path.join(SRC, "plugins", name), "r", encoding="utf-8") as f:
            return f.read()

    def test_all_plugins_import_runtime(self):
        for name in PLUGIN_FILES:
            self.assertIn("from utils import runtime", self._read(name), name)

    def test_no_while_true_left(self):
        for name in PLUGIN_FILES:
            self.assertNotIn("while True:", self._read(name), name)

    def test_no_raw_time_sleep_left(self):
        for name in PLUGIN_FILES:
            self.assertNotIn("time.sleep(", self._read(name), name)

    def test_use_shutdown_event(self):
        for name in PLUGIN_FILES:
            self.assertIn("runtime.shutdown_event.is_set()", self._read(name), name)

    def test_use_interruptible_sleep(self):
        for name in PLUGIN_FILES:
            self.assertIn("runtime.interruptible_sleep(", self._read(name), name)


class TestEntryPointWiring(unittest.TestCase):
    """入口/配置装配的静态校验。"""

    def _read(self, rel):
        with io.open(os.path.join(SRC, rel), "r", encoding="utf-8") as f:
            return f.read()

    def test_steamauto_dispatches_light_commands_before_heavy_imports(self):
        src = self._read("Steamauto.py")
        head = src[: src.index("import json5")]
        self.assertIn("utils.cli", head)
        self.assertIn('"status"', head)

    def test_steamauto_registers_control_commands(self):
        src = self._read("Steamauto.py")
        for cmd in ("config.apply", "config.reload", "shutdown", "ping"):
            self.assertIn('"%s"' % cmd, src, cmd)

    def test_default_config_has_new_sections(self):
        import json5

        cfg = json5.loads(DEFAULT_CONFIG_JSON)
        self.assertIn("console_echo", cfg)
        self.assertIn("control", cfg)
        self.assertTrue(cfg["control"]["enable"])
        self.assertIsInstance(cfg["control"]["port"], int)

    def test_daemon_sets_no_pause_env(self):
        src = self._read("utils/daemon.py")
        self.assertIn("STEAMAUTO_NO_PAUSE", src)
        self.assertIn("STEAMAUTO_DAEMON", src)

    def test_pause_tolerates_no_tty(self):
        src = self._read("utils/tools.py")
        self.assertIn("EOFError", src)


class TestConsoleOutputHygiene(unittest.TestCase):
    """防止「后台控制台日志出现 ANSI 乱码」与「退出被非 daemon 线程吊住」回退。"""

    @staticmethod
    def _read(rel):
        with io.open(os.path.join(SRC, rel), "r", encoding="utf-8") as f:
            return f.read()

    def test_strip_ansi(self):
        self.assertEqual(log_mod.strip_ansi("\x1b[31mred\x1b[0m"), "red")
        self.assertEqual(log_mod.strip_ansi("plain"), "plain")
        self.assertEqual(log_mod.strip_ansi(123), 123)

    def test_echo_strips_ansi_for_non_tty(self):
        orig = log_mod._stdout_is_tty
        log_mod._stdout_is_tty = lambda: False
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(1)
        handler.addFilter(log_mod.ConsoleEchoFilter())
        handler.setFormatter(logging.Formatter("%(message)s"))
        log_mod.logger.addHandler(handler)
        try:
            log_mod.echo("\x1b[31mansi-echo\x1b[0m")
        finally:
            log_mod.logger.removeHandler(handler)
            log_mod._stdout_is_tty = orig
        out = stream.getvalue()
        self.assertIn("ansi-echo", out)
        self.assertNotIn("\x1b", out)

    def test_echo_keeps_ansi_for_tty(self):
        orig = log_mod._stdout_is_tty
        log_mod._stdout_is_tty = lambda: True
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(1)
        handler.addFilter(log_mod.ConsoleEchoFilter())
        handler.setFormatter(logging.Formatter("%(message)s"))
        log_mod.logger.addHandler(handler)
        try:
            log_mod.echo("\x1b[31mcolor-echo\x1b[0m")
        finally:
            log_mod.logger.removeHandler(handler)
            log_mod._stdout_is_tty = orig
        self.assertIn("\x1b", stream.getvalue())

    def test_console_formatter_matches_tty(self):
        """非终端时不应使用 ColoredFormatter，否则重定向的日志会有 ANSI。"""
        import colorlog

        is_colored = isinstance(log_mod.s_handler.formatter, colorlog.ColoredFormatter)
        self.assertEqual(is_colored, log_mod._stdout_is_tty())

    def test_cloud_service_threads_are_daemon(self):
        """这两个后台轮询线程必须 daemon，否则优雅退出时解释器会被吊住。

        断言模块**持有的线程对象**而非 `threading.enumerate()`：轮询线程一旦感知到
        关停请求就会按设计退出且无法复活（模块已缓存），用 enumerate 会因测试顺序
        而假失败；而 `.daemon` 属性与存活状态无关，仍是有效回归依据。
        """
        from utils import cloud_service

        for attr in ("ad", "update"):
            thread = getattr(cloud_service, attr, None)
            self.assertIsNotNone(thread, "cloud_service 缺少线程对象 %s" % attr)
            self.assertTrue(thread.daemon, "%s 不是 daemon 线程" % attr)
            self.assertEqual(thread.name, "adsThread" if attr == "ad" else "versionThread")

    def test_cloud_service_loops_respect_shutdown(self):
        src = self._read("utils/cloud_service.py")
        self.assertNotIn("while True:", src)
        self.assertIn("daemon=True", src)

    def test_test_run_is_sandboxed(self):
        """守卫：测试期间数据目录必须在临时目录，不能写进项目真实目录。

        没有 conftest 的 STEAMAUTO_BASE_DIR 隔离时，`utils.logger` 会在 import 阶段
        就把 FileHandler 建到真实 `logs/` —— 每跑一次 pytest 就丢一个垃圾日志文件。
        断言 `_BASE_DIR`（没有任何测试会去改它）而非 `LOGS_FOLDER`（会被个别测试
        临时 patch），这样守卫本身不受测试顺序影响。
        """
        import utils.static as st

        base = os.path.abspath(st._BASE_DIR)
        self.assertNotEqual(base, os.path.abspath(SRC), "测试把数据目录指向了项目根，会污染 logs/config")
        self.assertTrue(
            base.lower().startswith(tempfile.gettempdir().lower()),
            "数据目录未落在临时目录：%s" % base,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
