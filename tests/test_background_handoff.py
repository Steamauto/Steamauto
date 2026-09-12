"""后台转交（handoff）与 --log 的回归测试。

对应需求：
- 直接运行 `python Steamauto.py` 时，前台完成初始化后自动转入后台、把控制台交还
- `run` 子命令保持前台常驻（盯日志调试用）
- `--log` 翻阅最近的日志

覆盖的关键行为（均来自实测踩坑）：
1. `_should_handoff_to_background`：只在「无参数启动」且非后台进程时为真
2. 单实例保护：必须在写 state 之前判断，否则前台会用自身 PID 覆盖 state，
   导致「看不到已有实例」而拉起第二个后台进程（实测复现过）
3. `_teardown_runtime(handed_off=True)` 不能清 state（那是后台刚写的）
4. `--log` 的各种参数形态
5. `login_to_steam` 不得再自行 pause（否则前台初始化会停住等按键）

用法：python -m pytest tests/test_background_handoff.py -v
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import Steamauto  # noqa: E402
from utils import cli, daemon, static  # noqa: E402


def _run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class _TempLogs(unittest.TestCase):
    """把 LOGS_FOLDER / RUN_FOLDER 等重定向到临时目录（含 import 时绑定的模块）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sa-handoff-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        run_dir = os.path.join(self.tmp, "run")
        logs_dir = os.path.join(self.tmp, "logs")
        for d in (run_dir, logs_dir):
            os.makedirs(d, exist_ok=True)
        self.logs_dir, self.run_dir = logs_dir, run_dir

        self._paths = {
            "LOGS_FOLDER": logs_dir,
            "RUN_FOLDER": run_dir,
            "PID_FILE": os.path.join(run_dir, "steamauto.pid"),
            "STATE_FILE": os.path.join(run_dir, "state.json"),
            "CONTROL_TOKEN_FILE": os.path.join(run_dir, "control_token.txt"),
        }
        self._mods = (static, daemon.static, cli.static, Steamauto)
        self._orig = [(m, k, getattr(m, k)) for m in self._mods for k in self._paths if hasattr(m, k)]
        for mod in self._mods:
            for key, value in self._paths.items():
                if hasattr(mod, key):
                    setattr(mod, key, value)
        # main 开头 activate("default") 会 set_base_dir 覆盖路径 mock；mock 掉它
        import utils.instance as instance_mod

        self._orig_activate = instance_mod.activate
        instance_mod.activate = lambda name, create=True: (name, self.tmp)

    def tearDown(self):
        for mod, key, value in self._orig:
            setattr(mod, key, value)
        import utils.instance as instance_mod

        instance_mod.activate = self._orig_activate

    def write_log(self, name, content):
        path = os.path.join(self.logs_dir, name)
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return path


# ============================================================ 转后台判定

class TestShouldHandoff(unittest.TestCase):
    def setUp(self):
        self._orig = os.environ.get("STEAMAUTO_BG_HANDOFF")
        self._orig_daemon = os.environ.get("STEAMAUTO_DAEMON")

    def tearDown(self):
        for key, val in (("STEAMAUTO_BG_HANDOFF", self._orig), ("STEAMAUTO_DAEMON", self._orig_daemon)):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def _set(self, handoff=None, is_daemon=None):
        for key, val in (("STEAMAUTO_BG_HANDOFF", handoff), ("STEAMAUTO_DAEMON", is_daemon)):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_true_when_flag_set_and_not_daemon(self):
        self._set(handoff="1", is_daemon=None)
        self.assertTrue(Steamauto._should_handoff_to_background())

    def test_false_without_flag(self):
        """`run` 子命令不带这个标记 → 保持前台常驻。"""
        self._set(handoff=None)
        self.assertFalse(Steamauto._should_handoff_to_background())

    def test_false_inside_daemon(self):
        """后台进程自身不得二次转后台（会无限孵化）。"""
        self._set(handoff="1", is_daemon="1")
        self.assertFalse(Steamauto._should_handoff_to_background())


# ============================================================ 单实例保护

class TestOtherInstancePid(unittest.TestCase):
    def setUp(self):
        self._orig = daemon.is_running

    def tearDown(self):
        daemon.is_running = self._orig

    def test_none_when_not_running(self):
        daemon.is_running = lambda: (False, {})
        self.assertIsNone(Steamauto._other_instance_pid())

    def test_none_for_self(self):
        """前台已把自身 PID 写进 state，此时不应把自己当"别的实例"。"""
        daemon.is_running = lambda: (True, {"pid": os.getpid()})
        self.assertIsNone(Steamauto._other_instance_pid())

    def test_returns_pid_for_other_process(self):
        daemon.is_running = lambda: (True, {"pid": 4242})
        self.assertEqual(Steamauto._other_instance_pid(), 4242)

    def test_none_when_pid_missing(self):
        daemon.is_running = lambda: (True, {"control_ok": False})
        self.assertIsNone(Steamauto._other_instance_pid())


# ============================================================ teardown 语义

class TestTeardownKeepsHandedOffState(_TempLogs):
    def test_clears_state_when_not_handed_off(self):
        daemon.write_state(pid=1, port=2)
        self.assertTrue(os.path.exists(static.STATE_FILE))
        Steamauto._teardown_runtime(None, handed_off=False)
        self.assertFalse(os.path.exists(static.STATE_FILE), "未移交时应清理 state")

    def test_keeps_state_when_handed_off(self):
        """移交后 state 是后台进程写的，前台收尾不能删掉它。"""
        daemon.write_state(pid=999, port=2)
        Steamauto._teardown_runtime(None, handed_off=True)
        self.assertTrue(os.path.exists(static.STATE_FILE), "移交后不应清 state")
        self.assertEqual(daemon.read_state().get("pid"), 999)


# ============================================================ --log

class TestLogFlag(_TempLogs):
    def test_default_picks_latest_any(self):
        self.write_log("2026-01-01-00-00-00.log", "app-old\n")
        self.write_log("console-2026-01-01-00-00-01.log", "console-new\n")
        rc, out, _ = _run_cli(["--log"])
        self.assertEqual(rc, 0)
        self.assertIn("console-new", out, "默认应取最新修改的日志文件")

    def test_app_kind(self):
        self.write_log("2026-01-01-00-00-00.log", "APP-CONTENT\n")
        self.write_log("console-2026-01-01-00-00-01.log", "CONSOLE-CONTENT\n")
        rc, out, _ = _run_cli(["--log", "app"])
        self.assertEqual(rc, 0)
        self.assertIn("APP-CONTENT", out)
        self.assertNotIn("CONSOLE-CONTENT", out)

    def test_console_kind(self):
        self.write_log("2026-01-01-00-00-00.log", "APP-CONTENT\n")
        self.write_log("console-2026-01-01-00-00-01.log", "CONSOLE-CONTENT\n")
        rc, out, _ = _run_cli(["--log", "console"])
        self.assertEqual(rc, 0)
        self.assertIn("CONSOLE-CONTENT", out)
        self.assertNotIn("APP-CONTENT", out)

    def test_numeric_lines(self):
        self.write_log("2026-01-01-00-00-00.log", "".join("line%d\n" % i for i in range(1, 101)))
        rc, out, _ = _run_cli(["--log", "7"])
        self.assertEqual(rc, 0)
        self.assertIn("末尾 7 行", out)
        self.assertIn("line100", out)
        self.assertNotIn("line92", out)

    def test_kind_falls_back_to_any_when_missing(self):
        """只要 app 日志不存在，应回落到任意最新日志而不是报错。"""
        self.write_log("console-2026-01-01-00-00-01.log", "ONLY-CONSOLE\n")
        rc, out, _ = _run_cli(["--log", "app"])
        self.assertEqual(rc, 0)
        self.assertIn("ONLY-CONSOLE", out)

    def test_no_logs_at_all(self):
        rc, _out, err = _run_cli(["--log"])
        self.assertEqual(rc, 1)
        self.assertIn("未找到日志文件", err)

    def test_invalid_argument(self):
        rc, _out, err = _run_cli(["--log", "bogus"])
        self.assertEqual(rc, 2)
        self.assertIn("无法识别", err)

    def test_follow_without_log_is_rejected(self):
        rc, _out, err = _run_cli(["-f"])
        self.assertEqual(rc, 2)
        self.assertIn("--log", err)

    def test_log_flag_defaults_to_app(self):
        """`--log app` 只看应用日志（原 `logs` 子命令的语义已并入 --log）。"""
        self.write_log("2026-01-01-00-00-00.log", "APP-SUB\n")
        self.write_log("console-2026-01-01-00-00-01.log", "CONSOLE-SUB\n")
        rc, out, _ = _run_cli(["--log", "app"])
        self.assertEqual(rc, 0)
        self.assertIn("APP-SUB", out)
        self.assertNotIn("CONSOLE-SUB", out)

    def test_lines_flag_equivalent_to_numeric(self):
        """`-n N` 等价于 `--log N`（原 logs 子命令的 -n 已并入）。"""
        self.write_log("2026-01-01-00-00-00.log", "".join("line%d\n" % i for i in range(1, 101)))
        rc, out, _ = _run_cli(["--log", "-n", "7"])
        self.assertEqual(rc, 0)
        self.assertIn("末尾 7 行", out)
        self.assertIn("line100", out)

    def test_console_flag_equivalent_to_console_topic(self):
        """`--console` 等价于 `--log console`。"""
        self.write_log("2026-01-01-00-00-00.log", "APP-ONLY\n")
        self.write_log("console-2026-01-01-00-00-01.log", "CONSOLE-ONLY\n")
        rc, out, _ = _run_cli(["--log", "--console"])
        self.assertEqual(rc, 0)
        self.assertIn("CONSOLE-ONLY", out)
        self.assertNotIn("APP-ONLY", out)

    def test_file_flag_selects_explicit_path(self):
        """`--file PATH` 直接指定日志文件。"""
        path = self.write_log("2026-01-01-00-00-00.log", "BY-PATH\n")
        rc, out, _ = _run_cli(["--log", "--file", path])
        self.assertEqual(rc, 0)
        self.assertIn("BY-PATH", out)

    def test_log_numeric_beats_lines(self):
        """`--log 3` 的行数优先于 -n。"""
        self.write_log("2026-01-01-00-00-00.log", "".join("line%d\n" % i for i in range(1, 51)))
        rc, out, _ = _run_cli(["--log", "3", "-n", "20"])
        self.assertEqual(rc, 0)
        self.assertIn("末尾 3 行", out)


class TestLatestLogFileKinds(_TempLogs):
    def test_app_only(self):
        app = self.write_log("2026-01-01-00-00-00.log", "a")
        self.write_log("console-2026-01-01-00-00-01.log", "c")
        self.assertEqual(daemon.latest_log_file("app"), app)

    def test_console_only(self):
        self.write_log("2026-01-01-00-00-00.log", "a")
        con = self.write_log("console-2026-01-01-00-00-01.log", "c")
        self.assertEqual(daemon.latest_log_file("console"), con)

    def test_any_prefers_newest(self):
        self.write_log("2026-01-01-00-00-00.log", "a")
        con = self.write_log("console-2026-01-01-00-00-01.log", "c")
        self.assertEqual(daemon.latest_log_file("any"), con)

    def test_returns_none_when_empty(self):
        self.assertIsNone(daemon.latest_log_file("app"))
        self.assertIsNone(daemon.latest_log_file("console"))
        self.assertIsNone(daemon.latest_log_file("any"))


# ============================================================ 启动模式

class TestRunModeSelection(unittest.TestCase):
    """无参数启动 / --run 都 spawn 子进程；--run 额外跟随日志。"""

    def setUp(self):
        self._orig_main = Steamauto.main
        self._env_backup = os.environ.get("STEAMAUTO_BG_HANDOFF")
        self._orig_spawn = cli.daemon.spawn_background
        self._orig_follow = cli.daemon.follow
        self._orig_latest = cli.daemon.latest_log_file
        # main 开头 activate("default") 会 set_base_dir 污染真实目录；mock 掉它
        import utils.instance as instance_mod

        self._orig_activate = instance_mod.activate
        instance_mod.activate = lambda name, create=True: (name, None)

    def tearDown(self):
        Steamauto.main = self._orig_main
        cli.daemon.spawn_background = self._orig_spawn
        cli.daemon.follow = self._orig_follow
        cli.daemon.latest_log_file = self._orig_latest
        import utils.instance as instance_mod

        instance_mod.activate = self._orig_activate
        if self._env_backup is None:
            os.environ.pop("STEAMAUTO_BG_HANDOFF", None)
        else:
            os.environ["STEAMAUTO_BG_HANDOFF"] = self._env_backup

    def test_no_args_spawns_background(self):
        """无参数启动：spawn 子进程（主进程不 import Steamauto）。"""
        spawns = []
        cli.daemon.spawn_background = lambda **kw: (spawns.append(kw), (True, "ok"))[1]
        rc = _run_cli([])[0]
        self.assertEqual(rc, 0)
        self.assertEqual(len(spawns), 1, "无参数启动应 spawn 子进程")

    def test_run_flag_spawns_and_follows(self):
        """--run：spawn 子进程 + 跟随日志到前台。"""
        spawns = []
        follows = []
        logfile = os.path.join(tempfile.mkdtemp(), "console.log")
        with open(logfile, "w", encoding="utf-8") as f:
            f.write("log\n")
        cli.daemon.spawn_background = lambda **kw: (spawns.append(kw), (True, "ok"))[1]
        cli.daemon.follow = lambda path: follows.append(path)
        cli.daemon.latest_log_file = lambda kind: logfile
        rc = _run_cli(["--run"])[0]
        self.assertEqual(rc, 0)
        self.assertEqual(len(spawns), 1, "--run 应 spawn 子进程")
        self.assertEqual(len(follows), 1, "--run 应跟随日志")

    def test_run_daemon_goes_to_background_without_foreground_init(self):
        """`--run -d` 应直接后台启动（不做前台初始化，不调用 main）。"""
        called = []
        spawns = []
        Steamauto.main = lambda: (called.append(True), 0)[1]
        orig = cli.daemon.spawn_background
        cli.daemon.spawn_background = lambda **kw: (spawns.append(kw), (True, "ok"))[1]
        try:
            rc = _run_cli(["--run", "-d"])[0]
        finally:
            cli.daemon.spawn_background = orig
        self.assertEqual(rc, 0)
        self.assertEqual(len(spawns), 1, "run -d 应走 spawn_background")
        self.assertEqual(called, [], "run -d 不应做前台初始化")


# ============================================================ 登录不再自行暂停

class TestLoginNoLongerPauses(unittest.TestCase):
    """`login_to_steam` 不得自行 pause()。

    否则前台初始化遇到登录失败会停下等按键（实测日志里出现过
    「点击回车键继续...」卡住 3 秒），与「初始化后自动转后台」冲突。
    暂停与否应由调用方（main）决定。
    """

    def test_no_pause_calls_in_steam_client(self):
        path = os.path.join(SRC, "utils", "steam_client.py")
        with io.open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("pause()", src, "steam_client 不应再调用 pause()")
        self.assertNotIn(" pause", src, "pause 导入应已清理")

    def test_pause_still_importable_for_other_modules(self):
        """pause 本身仍存在于 utils.tools，其它模块照常可用。"""
        from utils.tools import pause

        self.assertTrue(callable(pause))


if __name__ == "__main__":
    unittest.main(verbosity=2)
