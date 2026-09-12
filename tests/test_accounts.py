"""账号层 / flag CLI / 唤醒机制 / 插件动态启停 的回归测试。

对应需求：
1. 首次启动引导登录 + 登录失败转后台 + `--login` 补登录
2. `--status account` 查看各平台登录与连接状态
3. `--logout` 手动登出
4. `--help` 列出可用操作

用法：python -m pytest tests/test_accounts.py -v
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from utils import accounts, cli, config_writer, control, daemon, runtime, static  # noqa: E402


def _run_cli(argv):
    """跑一次 CLI，返回 (rc, stdout, stderr)。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class _IsolatedPaths(unittest.TestCase):
    """把路径常量重定向到临时目录。

    目标模块不止 `utils.static`：`Steamauto.py`、`utils.daemon`、`utils.cli`、
    `utils.accounts` 都是用 `from utils.static import X` 在 **import 时绑定**的。
    只改 `utils.static` 对它们无效 —— 它们的常量会一直指向上一次测试留下的
    临时目录，导致跨测试互相污染（这正是之前两个测试失败的根因）。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sa-acct-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        cfg, run = os.path.join(self.tmp, "config"), os.path.join(self.tmp, "run")
        for sub in ("config", "session", "run", "logs"):
            os.makedirs(os.path.join(self.tmp, sub), exist_ok=True)

        import Steamauto

        self._paths = {
            "CONFIG_FOLDER": cfg,
            "CONFIG_FILE_PATH": os.path.join(cfg, "config.json5"),
            "BUFF_COOKIES_FILE_PATH": os.path.join(cfg, "buff_cookies_{steam_username}.txt"),
            "UU_TOKEN_FILE_PATH": os.path.join(cfg, "uu_token_{steam_username}.txt"),
            "STEAM_ACCOUNT_INFO_FILE_PATH": os.path.join(cfg, "steam_account_info.json5"),
            "ECOSTEAM_RSAKEY_FILE": os.path.join(cfg, "rsakey.txt"),
            "SESSION_FOLDER": os.path.join(self.tmp, "session"),
            "RUN_FOLDER": run,
            "PID_FILE": os.path.join(run, "steamauto.pid"),
            "STATE_FILE": os.path.join(run, "state.json"),
            "CONTROL_TOKEN_FILE": os.path.join(run, "control_token.txt"),
            "LOGS_FOLDER": os.path.join(self.tmp, "logs"),
        }
        self._mods = (static, daemon.static, cli.static, accounts.static, Steamauto)
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

    # ---- 辅助 ----
    def write_config(self, text=None):
        if text is None:
            text = '{\n  // 保留注释\n  "log_level": "info",\n'
            text += '  c5_auto_accept_offer: { enable: false, app_key: "KEY123" },\n'
            text += '  ecosteam: { enable: false, partnerId: "PID123", qps: 10 },\n'
            text += '  plugin_whitelist: [],\n  source_code_auto_update: false\n}\n'
        with io.open(static.CONFIG_FILE_PATH, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def write_steam_account(self, username="tester"):
        with io.open(static.STEAM_ACCOUNT_INFO_FILE_PATH, "w", encoding="utf-8", newline="") as f:
            f.write('{"steam_username": "%s", "steam_password": "x"}' % username)


# ============================================================ 平台名解析（D6）

class TestPlatformAliases(_IsolatedPaths):
    def test_canonical_names(self):
        for name in ("buff", "uu", "c5", "eco"):
            self.assertEqual(accounts.resolve(name), name)

    def test_case_insensitive(self):
        for raw, expected in (("BUFF", "buff"), ("Uu", "uu"), ("C5", "c5"), ("ECo", "eco")):
            self.assertEqual(accounts.resolve(raw), expected)

    def test_aliases(self):
        cases = {
            "buffapi": "buff",
            "uuyoupin": "uu",
            "c5game": "c5",
            "ecosteam": "eco",
            "ecos": "eco",
        }
        for raw, expected in cases.items():
            self.assertEqual(accounts.resolve(raw), expected, raw)

    def test_unknown(self):
        self.assertIsNone(accounts.resolve("steam"))
        self.assertIsNone(accounts.resolve(""))
        self.assertIsNone(accounts.resolve(None))

    def test_parse_platforms_list(self):
        names, bad = accounts.parse_platforms("uu,Buff, c5")
        self.assertEqual(names, ["uu", "buff", "c5"])
        self.assertEqual(bad, [])

    def test_parse_platforms_reports_bad(self):
        names, bad = accounts.parse_platforms("uu,nope")
        self.assertEqual(names, ["uu"])
        self.assertEqual(bad, ["nope"])

    def test_parse_platforms_dedupes(self):
        names, _ = accounts.parse_platforms("uu,UU,uuyoupin")
        self.assertEqual(names, ["uu"])


# ============================================================ 状态查询（需求 2）

class TestAccountState(_IsolatedPaths):
    def test_buff_without_steam_username(self):
        self.write_config()
        state = accounts.account_state("buff", live=False)
        self.assertFalse(state["configured"])
        self.assertFalse(state["logged_in"])
        self.assertIn("Steam 用户名", state["error"])

    def test_buff_without_credential(self):
        self.write_config()
        self.write_steam_account()
        state = accounts.account_state("buff", live=False)
        self.assertFalse(state["configured"])
        self.assertIn("尚无", state["error"])

    def test_buff_with_credential_no_live(self):
        self.write_config()
        self.write_steam_account()
        path = accounts.credential_path("buff")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write("session=abc123")
        state = accounts.account_state("buff", live=False)
        self.assertTrue(state["configured"])
        self.assertTrue(state["logged_in"])
        self.assertFalse(state["connected"])  # 未联网校验

    def test_uu_states(self):
        self.write_config()
        self.write_steam_account()
        state = accounts.account_state("uu", live=False)
        self.assertFalse(state["configured"])
        path = accounts.credential_path("uu")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write("token-xyz")
        state = accounts.account_state("uu", live=False)
        self.assertTrue(state["configured"])
        self.assertTrue(state["logged_in"])

    def test_c5_requires_app_key(self):
        self.write_config('{\n  c5_auto_accept_offer: { enable: false, app_key: "" }\n}\n')
        state = accounts.account_state("c5", live=False)
        self.assertFalse(state["configured"])
        self.assertIn("AppKey", state["error"])

    def test_c5_with_app_key_no_live(self):
        self.write_config()
        state = accounts.account_state("c5", live=False)
        self.assertTrue(state["configured"])
        self.assertTrue(state["logged_in"])

    def test_eco_requires_partner_and_key(self):
        self.write_config('{\n  ecosteam: { enable: false, partnerId: "" }\n}\n')
        state = accounts.account_state("eco", live=False)
        self.assertFalse(state["configured"])
        self.assertIn("partnerId", state["error"])

    def test_eco_rejects_public_key_file(self):
        self.write_config()
        with io.open(static.ECOSTEAM_RSAKEY_FILE, "w", encoding="utf-8") as f:
            f.write("-----BEGIN PUBLIC KEY-----\nabc\n")
        state = accounts.account_state("eco", live=False)
        self.assertFalse(state["configured"])
        self.assertIn("公钥", state["error"])

    def test_eco_with_private_key_no_live(self):
        self.write_config()
        with io.open(static.ECOSTEAM_RSAKEY_FILE, "w", encoding="utf-8") as f:
            f.write("-----BEGIN RSA PRIVATE KEY-----\nabc\n")
        state = accounts.account_state("eco", live=False)
        self.assertTrue(state["configured"])
        self.assertTrue(state["logged_in"])

    def test_all_account_states_covers_four_platforms(self):
        self.write_config()
        states = accounts.all_account_states(live=False)
        self.assertEqual(sorted(states), ["buff", "c5", "eco", "uu"])
        for name, info in states.items():
            self.assertIn("display", info)
            self.assertEqual(info["platform"], name)

    def test_steam_state_never_logs_in(self):
        """本地探测 Steam 时绝不能发起真实登录（会弹二维码）。"""
        self.write_account_and_probe()

    def write_account_and_probe(self):
        self.write_config()
        self.write_steam_account()
        state = accounts.steam_state(live=False)
        self.assertTrue(state["configured"])
        self.assertEqual(state["account"], "tester")
        self.assertFalse(state["connected"])
        self.assertIn("会话缓存", state["error"] or "")

    def test_steam_state_no_username(self):
        self.write_config()
        state = accounts.steam_state(live=False)
        self.assertFalse(state["configured"])
        self.assertIn("可选", state["error"])


# ============================================================ 登出（需求 3 / D5b）

class TestLogout(_IsolatedPaths):
    def test_logout_buff_clears_cookie_file(self):
        self.write_config()
        self.write_steam_account()
        path = accounts.credential_path("buff")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write("session=secret")
        ok, msg, cleared = accounts.logout("buff")
        self.assertTrue(ok, msg)
        self.assertIn(path, cleared)
        with io.open(path, encoding="utf-8") as f:
            self.assertNotIn("secret", f.read())

    def test_logout_uu_empties_token_file(self):
        self.write_config()
        self.write_steam_account()
        path = accounts.credential_path("uu")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write("token-secret")
        ok, _msg, cleared = accounts.logout("uu")
        self.assertTrue(ok)
        self.assertIn(path, cleared)
        with io.open(path, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "")

    def test_logout_c5_clears_app_key_and_keeps_comments(self):
        """D5b：一律清配置。同时必须保留配置文件里的注释与格式。"""
        self.write_config()
        ok, _msg, cleared = accounts.logout("c5")
        self.assertTrue(ok)
        self.assertIn("config:c5_auto_accept_offer.app_key", cleared)
        text = config_writer.read_text(static.CONFIG_FILE_PATH)
        self.assertIn("// 保留注释", text)
        self.assertIn('app_key: ""', text)
        self.assertNotIn("KEY123", text)

    def test_logout_eco_clears_partner_and_rsakey(self):
        self.write_config()
        with io.open(static.ECOSTEAM_RSAKEY_FILE, "w", encoding="utf-8") as f:
            f.write("PRIVATE-KEY-DATA")
        ok, _msg, cleared = accounts.logout("eco")
        self.assertTrue(ok)
        self.assertTrue(any("partnerId" in c for c in cleared))
        self.assertIn(static.ECOSTEAM_RSAKEY_FILE, cleared)
        text = config_writer.read_text(static.CONFIG_FILE_PATH)
        self.assertIn("// 保留注释", text)
        self.assertNotIn("PID123", text)
        with io.open(static.ECOSTEAM_RSAKEY_FILE, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "")

    def test_logout_then_status_shows_not_logged_in(self):
        self.write_config()
        self.write_steam_account()
        path = accounts.credential_path("buff")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write("session=abc")
        self.assertTrue(accounts.account_state("buff", live=False)["logged_in"])
        accounts.logout("buff")
        self.assertFalse(accounts.account_state("buff", live=False)["configured"])

    def test_logout_unknown_platform(self):
        ok, msg, _ = accounts.logout("nope")
        self.assertFalse(ok)
        self.assertIn("未知平台", msg)


# ============================================================ 登录的可交互性前置检查

class TestLoginPreconditions(_IsolatedPaths):
    def test_login_unknown_platform(self):
        ok, msg, _ = accounts.login("nope")
        self.assertFalse(ok)
        self.assertIn("未知平台", msg)

    def test_login_buff_without_steam_username(self):
        self.write_config()
        ok, msg, _ = accounts.login("buff")
        self.assertFalse(ok)
        self.assertIn("Steam 用户名", msg)

    def test_login_buff_requires_tty(self):
        self.write_config()
        self.write_steam_account()
        orig = accounts.stdin_is_interactive
        accounts.stdin_is_interactive = lambda: False
        try:
            ok, msg, _ = accounts.login("buff")
        finally:
            accounts.stdin_is_interactive = orig
        self.assertFalse(ok)
        self.assertIn("交互式终端", msg)

    def test_login_uu_requires_tty(self):
        self.write_config()
        self.write_steam_account()
        orig = accounts.stdin_is_interactive
        accounts.stdin_is_interactive = lambda: False
        try:
            ok, msg, _ = accounts.login("uu")
        finally:
            accounts.stdin_is_interactive = orig
        self.assertFalse(ok)
        self.assertIn("交互式终端", msg)

    def test_stdin_is_interactive_false_for_devnull(self):
        """回归：Windows 上 NUL 设备会被 os.isatty 误判为终端，必须排除。"""
        import subprocess

        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "from utils import accounts;"
            "print(accounts.stdin_is_interactive())" % SRC
        )
        p = subprocess.run([sys.executable, "-c", code], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.stdout.strip(), "False", "stdin=DEVNULL 时应判定为非交互")

    def test_stdin_is_interactive_false_when_daemon(self):
        orig = os.environ.get("STEAMAUTO_DAEMON")
        os.environ["STEAMAUTO_DAEMON"] = "1"
        try:
            self.assertFalse(accounts.stdin_is_interactive())
        finally:
            if orig is None:
                os.environ.pop("STEAMAUTO_DAEMON", None)
            else:
                os.environ["STEAMAUTO_DAEMON"] = orig

    def test_login_c5_reports_missing_app_key(self):
        self.write_config('{\n  c5_auto_accept_offer: { enable: false, app_key: "" }\n}\n')
        ok, msg, _ = accounts.login("c5")
        self.assertFalse(ok)
        self.assertIn("AppKey", msg)

    def test_login_eco_reports_missing_partner(self):
        self.write_config('{\n  ecosteam: { enable: false, partnerId: "" }\n}\n')
        ok, msg, _ = accounts.login("eco")
        self.assertFalse(ok)
        self.assertIn("partnerId", msg)

    def test_login_eco_reports_missing_key_file(self):
        self.write_config()
        ok, msg, _ = accounts.login("eco")
        self.assertFalse(ok)
        self.assertIn("私钥", msg)


# ============================================================ flag CLI（需求 2/3/4 / D4b）

class TestFlagCli(_IsolatedPaths):
    def test_help_lists_all_operations(self):
        rc, out, _ = _run_cli(["--help"])
        self.assertEqual(rc, 0)
        for expected in (
            "--login",
            "--logout",
            "--status account",
            "--run",
            "--start",
            "--stop",
            "--restart",
            "--log",
            "--config --set",
            "--ctl",
        ):
            self.assertIn(expected, out, "--help 未列出 %s" % expected)

    def test_status_account_table(self):
        self.write_config()
        self.write_steam_account()
        orig = daemon.is_running
        daemon.is_running = lambda: (True, {})  # 模拟程序运行中
        try:
            rc, out, _ = _run_cli(["--status", "account", "--no-live"])
        finally:
            daemon.is_running = orig
        self.assertEqual(rc, 0)
        self.assertIn("各平台账号状态", out)
        for label in ("BUFF", "UU", "C5", "ECO", "Steam"):
            self.assertIn(label, out, "状态表缺少 %s" % label)

    def test_status_account_json(self):
        self.write_config()
        self.write_steam_account()
        orig = daemon.is_running
        daemon.is_running = lambda: (True, {})
        try:
            rc, out, _ = _run_cli(["--status", "account", "--no-live", "--json"])
        finally:
            daemon.is_running = orig
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("accounts", data)
        self.assertEqual(sorted(data["accounts"]), ["buff", "c5", "eco", "uu"])
        self.assertIn("steam", data)
        self.assertIn("source", data)

    def test_status_account_requires_running(self):
        """程序未运行时，--status account 应被拒绝（runtime 数据无效）。"""
        orig = daemon.is_running
        daemon.is_running = lambda: (False, {})  # 模拟程序未运行
        try:
            rc, _out, err = _run_cli(["--status", "account"])
        finally:
            daemon.is_running = orig
        self.assertEqual(rc, 1)
        self.assertIn("程序未运行", err)

    def test_status_unknown_instance_not_running(self):
        """`--status <任意实例名>`：未运行的实例返回 3（不再有「不支持主题」）。"""
        rc, out, _ = _run_cli(["--status", "positions"])
        self.assertEqual(rc, 3)
        self.assertIn("positions", out)

    def test_logout_buff_via_flag(self):
        self.write_config()
        self.write_steam_account()
        path = accounts.credential_path("buff")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write("session=secret")
        rc, out, _ = _run_cli(["--logout", "buff"])
        self.assertEqual(rc, 0)
        self.assertIn("已清除", out)
        with io.open(path, encoding="utf-8") as f:
            self.assertNotIn("secret", f.read())

    def test_logout_multiple_platforms(self):
        self.write_config()
        self.write_steam_account()
        rc, out, _ = _run_cli(["--logout", "c5,eco"])
        self.assertEqual(rc, 0)
        text = config_writer.read_text(static.CONFIG_FILE_PATH)
        self.assertNotIn("KEY123", text)
        self.assertNotIn("PID123", text)

    def test_login_bad_platform_exit_code(self):
        rc, _out, err = _run_cli(["--login", "nope"])
        self.assertEqual(rc, 2)
        self.assertIn("无法识别", err)

    def test_login_without_platform_value(self):
        """空值必须报错，绝不能被静默忽略后 fall-through 成「前台运行」。"""
        rc, _out, err = _run_cli(["--login", ""])
        self.assertEqual(rc, 2)
        self.assertIn("请指定平台", err)

    def test_logout_without_platform_value(self):
        rc, _out, err = _run_cli(["--logout", ""])
        self.assertEqual(rc, 2)
        self.assertIn("请指定平台", err)

    def test_status_without_topic_value(self):
        """空值 = all，显示所有实例列表。"""
        rc, out, _ = _run_cli(["--status", ""])
        self.assertEqual(rc, 0)
        self.assertIn("实例列表", out)

    def test_status_process_topic(self):
        """process = 当前实例（default）的进程状态。"""
        from unittest import mock

        with mock.patch("utils.daemon.pid_alive", return_value=False):
            rc, out, _ = _run_cli(["--status", "process"])
        self.assertEqual(rc, 3)  # 当前实例未运行
        self.assertIn("状态", out)

    def test_legacy_subcommands_removed(self):
        """旧子命令写法已移除：应提示未指定操作并给出帮助。"""
        rc, out, err = _run_cli(["status"])
        self.assertEqual(rc, 0, "未指定操作时应打印帮助并返回 0")
        self.assertIn("未指定操作", err)
        self.assertIn("--status", out, "帮助应给出新的 --status 写法")


# ============================================================ 唤醒机制（D3b）

class TestWakeMechanism(unittest.TestCase):
    def setUp(self):
        runtime.clear_shutdown()
        runtime.clear_wake()

    def tearDown(self):
        runtime.clear_shutdown()
        runtime.clear_wake()

    def test_sleep_completes_when_undisturbed(self):
        self.assertTrue(runtime.interruptible_sleep(0.25, step=0.05))

    def test_sleep_interrupted_by_wake(self):
        threading.Timer(0.1, runtime.request_wake).start()
        start = time.monotonic()
        self.assertFalse(runtime.interruptible_sleep(10.0, step=0.05))
        self.assertLess(time.monotonic() - start, 3.0)
        # 关键：唤醒不是关停，调用方据此继续循环
        self.assertFalse(runtime.is_shutdown_requested())

    def test_wake_is_broadcast_to_all_waiters(self):
        """关键回归：唤醒必须送达**所有**等待者。

        早期用 threading.Event 实现时，多个等待者会争抢消费事件（谁先醒谁清掉），
        实测中后台 cloud_service 的轮询线程会把唤醒抢走，目标插件收不到。
        改用版本号后，每个等待者各自比较版本号，互不影响。
        """
        got = []

        def waiter(name):
            if not runtime.interruptible_sleep(10.0, step=0.05):
                got.append(name)

        threads = [threading.Thread(target=waiter, args=("w%d" % i,), daemon=True) for i in range(4)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        runtime.request_wake()
        for t in threads:
            t.join(timeout=3)
        self.assertEqual(sorted(got), ["w0", "w1", "w2", "w3"], "唤醒未广播到全部等待者")

    def test_wake_sequence_increments(self):
        before = runtime.wake_sequence()
        runtime.request_wake()
        self.assertEqual(runtime.wake_sequence(), before + 1)
        runtime.request_wake()
        self.assertEqual(runtime.wake_sequence(), before + 2)

    def test_wake_does_not_affect_already_completed_wait(self):
        """等待已结束后才到来的唤醒，不应回溯影响（版本号在进入等待时捕获）。"""
        self.assertTrue(runtime.interruptible_sleep(0.1, step=0.02))
        runtime.request_wake()
        self.assertTrue(runtime.interruptible_sleep(0.1, step=0.02))

    def test_shutdown_still_wins(self):
        runtime.request_shutdown()
        self.assertFalse(runtime.interruptible_sleep(10.0, step=0.05))
        self.assertTrue(runtime.is_shutdown_requested())

    def test_loop_pattern_continues_on_wake_and_breaks_on_shutdown(self):
        """插件侧的写法：只有关停才 break。"""
        iterations = []

        def worker():
            n = 0
            while not runtime.shutdown_event.is_set():
                n += 1
                iterations.append(n)
                if len(iterations) >= 2:
                    runtime.request_shutdown()
                    break
                if not runtime.interruptible_sleep(5, step=0.05) and runtime.is_shutdown_requested():
                    break

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.15)
        runtime.request_wake()  # 应当让它立刻进入第 2 轮，而不是等 5 秒
        t.join(timeout=3)
        self.assertFalse(t.is_alive(), "唤醒后循环未如期推进")
        self.assertGreaterEqual(len(iterations), 2)


# ============================================================ 插件动态启停（D1b）

class _StubPlugin:
    """可控的假插件：init() 返回 True 表示失败（Steamauto 的历史语义）。"""

    def __init__(self, fail_init=False, run_forever=True, crash=False):
        self.fail_init = fail_init
        self.run_forever = run_forever
        self.crash = crash
        self.init_calls = 0
        self.exec_calls = 0

    def init(self):
        self.init_calls += 1
        if self.crash:
            raise RuntimeError("init boom")
        return self.fail_init

    def exec(self):
        self.exec_calls += 1
        if self.run_forever:
            while not runtime.shutdown_event.is_set():
                if not runtime.interruptible_sleep(0.2, step=0.05) and runtime.is_shutdown_requested():
                    break


class TestPluginRuntime(unittest.TestCase):
    def setUp(self):
        runtime.clear_shutdown()
        runtime.clear_wake()
        import Steamauto

        self.Steamauto = Steamauto

    def tearDown(self):
        runtime.clear_shutdown()
        runtime.clear_wake()

    def test_start_success(self):
        plugin = _StubPlugin()
        rt = self.Steamauto.PluginRuntime({"uu_auto_accept_offer": plugin})
        ok, msg = rt.start("uu_auto_accept_offer")
        self.assertTrue(ok, msg)
        self.assertIn("uu_auto_accept_offer", rt.started_keys())
        self.assertEqual(rt.pending_keys(), [])

    def test_failed_plugin_is_retained_not_dropped(self):
        """核心：init 失败的插件必须留下来，供登录后重试。"""
        plugin = _StubPlugin(fail_init=True)
        rt = self.Steamauto.PluginRuntime({"buff_auto_accept_offer": plugin})
        ok, msg = rt.start("buff_auto_accept_offer")
        self.assertFalse(ok)
        self.assertIn("buff_auto_accept_offer", rt.failed_keys())
        self.assertIn("buff_auto_accept_offer", rt.pending_keys())
        self.assertEqual(rt.started_keys(), [])

    def test_retry_after_login_succeeds(self):
        plugin = _StubPlugin(fail_init=True)
        rt = self.Steamauto.PluginRuntime({"uu_auto_accept_offer": plugin})
        self.assertFalse(rt.start("uu_auto_accept_offer")[0])

        plugin.fail_init = False  # 模拟「用户补登录后」
        ok, msg = rt.start("uu_auto_accept_offer")
        self.assertTrue(ok, msg)
        self.assertEqual(rt.failed_keys(), [])
        self.assertIn("uu_auto_accept_offer", rt.started_keys())

    def test_start_is_idempotent(self):
        plugin = _StubPlugin()
        rt = self.Steamauto.PluginRuntime({"uu_auto_accept_offer": plugin})
        rt.start("uu_auto_accept_offer")
        first = plugin.exec_calls
        ok, msg = rt.start("uu_auto_accept_offer")
        self.assertTrue(ok)
        self.assertIn("已在运行", msg)
        self.assertEqual(plugin.exec_calls, first, "重复 start 不应再起线程")

    def test_unknown_key_reports_loaded_keys(self):
        rt = self.Steamauto.PluginRuntime({"uu_auto_accept_offer": _StubPlugin()})
        ok, msg = rt.start("c5_auto_accept_offer")
        self.assertFalse(ok)
        self.assertIn("uu_auto_accept_offer", msg)

    def test_init_crash_is_caught(self):
        rt = self.Steamauto.PluginRuntime({"ecosteam": _StubPlugin(crash=True)})
        ok, msg = rt.start("ecosteam")
        self.assertFalse(ok)
        self.assertIn("ecosteam", rt.failed_keys())

    def test_start_all_reports_started_and_skipped(self):
        ok_plugin = _StubPlugin()
        bad_plugin = _StubPlugin(fail_init=True)
        rt = self.Steamauto.PluginRuntime(
            {"uu_auto_accept_offer": ok_plugin, "c5_auto_accept_offer": bad_plugin}
        )
        started, skipped = rt.start_all(jitter=0)
        self.assertIn("uu_auto_accept_offer", started)
        self.assertIn("c5_auto_accept_offer", skipped)


class TestPluginInitSemantics(unittest.TestCase):
    """init() 的返回值语义是「True = 失败」，很容易被误改。"""

    def setUp(self):
        import Steamauto

        self.Steamauto = Steamauto

    def test_true_means_failure(self):
        ok, _reason = self.Steamauto.plugin_init_ok(_StubPlugin(fail_init=True))
        self.assertFalse(ok)

    def test_false_means_success(self):
        ok, reason = self.Steamauto.plugin_init_ok(_StubPlugin(fail_init=False))
        self.assertTrue(ok, reason)

    def test_exception_means_failure(self):
        ok, reason = self.Steamauto.plugin_init_ok(_StubPlugin(crash=True))
        self.assertFalse(ok)
        self.assertIn("异常", reason)

    def test_plugins_check_keeps_list_semantics(self):
        """保留旧签名：列表进、列表出（既有调用方依赖）。"""

        class Ok:
            def init(self):
                return False

        class Fail:
            def init(self):
                return True

        result = self.Steamauto.plugins_check([Ok(), Fail()])
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Ok)

    def test_plugins_check_empty(self):
        self.assertEqual(self.Steamauto.plugins_check([]), [])


# ============================================================ 首次引导（需求 1 / D2a）

class TestOnboarding(_IsolatedPaths):
    def setUp(self):
        super().setUp()
        import Steamauto

        self.Steamauto = Steamauto

    def test_non_interactive_skips_and_continues(self):
        """后台/无终端首次运行：跳过引导，但继续启动服务（不是退出）。"""
        orig_env = os.environ.get("STEAMAUTO_DAEMON")
        os.environ["STEAMAUTO_DAEMON"] = "1"
        orig_no_pause = os.environ.get("STEAMAUTO_NO_PAUSE")
        os.environ["STEAMAUTO_NO_PAUSE"] = "1"
        try:
            self.assertTrue(self.Steamauto._is_interactive() is False)
            proceed = self.Steamauto._onboard_first_run()
        finally:
            if orig_env is None:
                os.environ.pop("STEAMAUTO_DAEMON", None)
            else:
                os.environ["STEAMAUTO_DAEMON"] = orig_env
            if orig_no_pause is None:
                os.environ.pop("STEAMAUTO_NO_PAUSE", None)
            else:
                os.environ["STEAMAUTO_NO_PAUSE"] = orig_no_pause
        self.assertTrue(proceed, "无终端时应继续启动服务")

    def test_is_interactive_false_when_not_tty(self):
        orig = sys.stdin
        try:
            sys.stdin = io.StringIO()  # 无 isatty / 非 tty
            self.assertFalse(self.Steamauto._is_interactive())
        finally:
            sys.stdin = orig

    def test_both_login_fail_spawns_background(self):
        """BUFF 与 UU 都失败 → 调 daemon.spawn_background 并返回 False（不启动前台服务）。"""
        self.write_config()
        self.write_steam_account()

        calls = {}

        def fake_spawn(**kwargs):
            calls["spawned"] = True
            return True, "已后台启动"

        def fake_login(platform, interactive=True):
            return False, "%s 登录失败（测试）" % platform, {}

        orig_spawn = self.Steamauto.daemon.spawn_background
        orig_login = accounts.login
        orig_interactive = self.Steamauto._is_interactive
        self.Steamauto.daemon.spawn_background = fake_spawn
        accounts.login = fake_login
        self.Steamauto._is_interactive = lambda: True
        try:
            proceed = self.Steamauto._onboard_first_run()
        finally:
            self.Steamauto.daemon.spawn_background = orig_spawn
            accounts.login = orig_login
            self.Steamauto._is_interactive = orig_interactive

        self.assertFalse(proceed, "两个平台都失败时应转后台而不是前台继续")
        self.assertTrue(calls.get("spawned"), "未调用 spawn_background")

    def test_one_login_ok_continues_in_foreground(self):
        self.write_config()
        self.write_steam_account()

        orig_login = accounts.login
        orig_interactive = self.Steamauto._is_interactive
        accounts.login = lambda platform, interactive=True: (platform == "uu", "%s 结果" % platform, {})
        self.Steamauto._is_interactive = lambda: True
        try:
            proceed = self.Steamauto._onboard_first_run()
        finally:
            accounts.login = orig_login
            self.Steamauto._is_interactive = orig_interactive
        self.assertTrue(proceed, "至少一个平台成功时应继续前台启动")


class TestNotifyRuntime(_IsolatedPaths):
    """`--login` 成功后要让运行中的进程立刻重试对应插件（D1b 的调用侧）。

    验证发出去的是正确的命令与 plugin_key —— 映射写错的话，登录成功但插件不启动，
    而用户只会看到「凭据已保存」，很难排查。
    """

    def test_not_running(self):
        orig = daemon.is_running
        daemon.is_running = lambda: (False, {})
        try:
            delivered, msg = accounts.notify_runtime("uu")
        finally:
            daemon.is_running = orig
        self.assertFalse(delivered)
        self.assertIn("未在运行", msg)

    def test_control_unreachable(self):
        orig = daemon.is_running
        daemon.is_running = lambda: (True, {"pid": 1, "port": 1234, "control_ok": False})
        try:
            delivered, msg = accounts.notify_runtime("uu")
        finally:
            daemon.is_running = orig
        self.assertFalse(delivered)
        self.assertIn("控制通道", msg)

    def test_sends_plugin_retry_with_correct_key(self):
        expected = {
            "buff": "buff_auto_accept_offer",
            "uu": "uu_auto_accept_offer",
            "c5": "c5_auto_accept_offer",
            "eco": "ecosteam",
        }
        orig_is_running = daemon.is_running
        orig_request = control.request
        daemon.is_running = lambda: (True, {"pid": 1, "port": 4567, "control_ok": True})
        sent = []

        def fake_request(cmd, args=None, port=None, timeout=None, **kwargs):
            sent.append({"cmd": cmd, "args": args, "port": port})
            return True, {"ok": True, "message": "已启动"}

        control.request = fake_request
        try:
            for platform, key in expected.items():
                sent.clear()
                delivered, msg = accounts.notify_runtime(platform)
                self.assertTrue(delivered, msg)
                self.assertEqual(sent[0]["cmd"], "plugin.retry")
                self.assertEqual(sent[0]["args"]["plugin_key"], key, platform)
                self.assertEqual(sent[0]["args"]["platform"], platform)
                self.assertTrue(sent[0]["args"]["wake"], "应同时请求唤醒，避免等满 interval")
                self.assertEqual(sent[0]["port"], 4567)
        finally:
            daemon.is_running = orig_is_running
            control.request = orig_request

    def test_reports_remote_error(self):
        orig_is_running = daemon.is_running
        orig_request = control.request
        daemon.is_running = lambda: (True, {"pid": 1, "port": 4567, "control_ok": True})
        control.request = lambda *a, **k: (False, "未启用插件 xxx")
        try:
            delivered, msg = accounts.notify_runtime("uu")
        finally:
            daemon.is_running = orig_is_running
            control.request = orig_request
        self.assertFalse(delivered)
        self.assertIn("未启用插件", msg)


class TestFirstRunLoadsDefaultConfig(_IsolatedPaths):
    """首次运行必须加载刚生成的默认配置。

    上游 bug（本次改造才暴露）：`config` 只在「文件已存在」时才 json5.load，
    首次运行生成的默认配置**从未被读入**，于是 config 保持 {}，
    后续 get_plugins_enabled 会因「plugin_key 不在 config 里、又属于内置插件」
    判定无插件启用 → 「未启用任何插件」→ 直接退出。
    原代码首次运行走 pause()+return 0（提示去填配置），所以一直没暴露。
    """

    def setUp(self):
        super().setUp()
        import Steamauto

        self.Steamauto = Steamauto
        self._cfg_backup = Steamauto.config
        import utils.cloud_service as cloud_service

        self._cloud_backup = (cloud_service.checkVersion, cloud_service.getAds)
        cloud_service.checkVersion = lambda: None
        cloud_service.getAds = lambda: None

    def tearDown(self):
        import utils.cloud_service as cloud_service

        cloud_service.checkVersion, cloud_service.getAds = self._cloud_backup
        self.Steamauto.config = self._cfg_backup
        super().tearDown()

    def test_first_run_returns_1_and_loads_config(self):
        self.Steamauto.config = {}
        status = self.Steamauto.init_files_and_params()
        self.assertEqual(status, 1, "首次运行应返回 1")
        self.assertTrue(os.path.exists(static.CONFIG_FILE_PATH))
        cfg = self.Steamauto.config
        self.assertTrue(cfg, "首次运行也必须把默认配置读进 config，否则会被判定无插件启用")
        self.assertIn("buff_auto_accept_offer", cfg)
        self.assertTrue(cfg["buff_auto_accept_offer"]["enable"])

    def test_second_run_also_loads_config(self):
        self.Steamauto.init_files_and_params()  # 首次：生成
        self.Steamauto.config = {}
        status = self.Steamauto.init_files_and_params()  # 第二次
        self.assertEqual(status, 2)
        self.assertIn("buff_auto_accept_offer", self.Steamauto.config)

    def test_broken_config_returns_0(self):
        with io.open(static.CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json5 ::: ")
        status = self.Steamauto.init_files_and_params()
        self.assertEqual(status, 0, "配置损坏时应返回 0（调用方会提示并退出）")


class TestServeUntilShutdown(unittest.TestCase):
    """关键行为：插件全部失败时进程必须**保持存活**（待命），不能退出。

    否则用户之后 `--login` 根本没有进程可以通知，与「登录失败则挂起到后台运行」
    的需求直接冲突（实测中进程会在启动约 4 秒后自行退出）。
    """

    def setUp(self):
        runtime.clear_shutdown()
        runtime.clear_wake()
        import Steamauto

        self.Steamauto = Steamauto
        self._orig = Steamauto._PLUGIN_RUNTIME

    def tearDown(self):
        self.Steamauto._PLUGIN_RUNTIME = self._orig
        runtime.clear_shutdown()
        runtime.clear_wake()

    def test_returns_immediately_when_nothing_pending(self):
        rt = self.Steamauto.PluginRuntime({})
        self.Steamauto._PLUGIN_RUNTIME = rt
        start = time.monotonic()
        self.Steamauto._serve_until_shutdown(poll=0.05)
        self.assertLess(time.monotonic() - start, 1.0)

    def test_waits_while_plugins_pending(self):
        """有待登录插件时不能返回，直到收到关停请求。"""
        rt = self.Steamauto.PluginRuntime({"uu_auto_accept_offer": _StubPlugin(fail_init=True)})
        rt.start("uu_auto_accept_offer")  # 失败 → 进 pending
        self.assertEqual(rt.pending_keys(), ["uu_auto_accept_offer"])
        self.Steamauto._PLUGIN_RUNTIME = rt

        done = []

        def worker():
            self.Steamauto._serve_until_shutdown(poll=0.05)
            done.append(True)

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.4)
        self.assertEqual(done, [], "有待登录插件时不应返回（应保持待命）")
        runtime.request_shutdown()
        t.join(timeout=3)
        self.assertEqual(done, [True], "收到关停后应返回")

    def test_starts_pending_plugin_when_retried_during_standby(self):
        """待命期间通过 plugin.retry 启动插件，进程应继续服务而不是退出。"""
        plugin = _StubPlugin(fail_init=True)
        rt = self.Steamauto.PluginRuntime({"uu_auto_accept_offer": plugin})
        rt.start("uu_auto_accept_offer")
        self.Steamauto._PLUGIN_RUNTIME = rt

        done = []

        def worker():
            self.Steamauto._serve_until_shutdown(poll=0.05)
            done.append(True)

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.2)
        plugin.fail_init = False  # 等价于用户补登录
        ok, _msg = rt.start("uu_auto_accept_offer")
        self.assertTrue(ok)
        time.sleep(0.3)
        self.assertEqual(rt.pending_keys(), [])
        self.assertEqual(done, [], "插件已启动后应留在服务循环里等它跑")
        runtime.request_shutdown()
        t.join(timeout=3)
        self.assertEqual(done, [True])


if __name__ == "__main__":
    unittest.main(verbosity=2)
