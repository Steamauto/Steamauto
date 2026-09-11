"""Steamauto 免鉴权改动的持久回归测试（标准库 unittest）。

用法：在项目根目录运行
    python -m unittest tests.test_steamauto -v

覆盖：OfflineSteamClient、发货人工确认、login_to_steam 解除 secret 强制、
manual_confirm_delivery 配置、日志默认等级、无终端（STEAMAUTO_NO_PAUSE）降级路径、
插件初始化失败跳过策略。

注意：本分支（plus）不包含 gui/。因此所有依赖 gui 包的用例已移除：
GUI 退出/日志等级接口、gui/runner.py 子进程启动、gui/config_schema.py 配置分组、
以及实现位于 gui/buff.py 与 gui/uu.py 中的自动买卖扫描（scan_and_trade）。
"""
import json5
import os
import shutil
import sys
import tempfile
import threading
import unittest


SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:
    sys.path.insert(0, SRC)


_ORIGINAL_CWD = os.getcwd()


def _isolated_cwd(cls=None):
    """临时目录隔离 config/logs/session，返回临时目录路径。

    os.chdir 是进程级副作用，清理时必须先切回原目录再删除，
    否则 Windows 上会因「目录正被使用」删不掉。传入 cls 时自动登记清理，
    避免每跑一次测试就在临时目录里遗留一堆空目录。
    """
    d = tempfile.mkdtemp(prefix="steamauto-test-")
    os.chdir(d)
    if cls is not None:
        def _cleanup():
            os.chdir(_ORIGINAL_CWD)
            shutil.rmtree(d, ignore_errors=True)

        cls.addClassCleanup(_cleanup)
    return d


class TestOfflineSteamClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = _isolated_cwd(cls)
        from utils.steam_client import OfflineSteamClient
        cls.OfflineSteamClient = OfflineSteamClient

    def test_identity(self):
        oc = self.OfflineSteamClient("linux_user")
        self.assertEqual(oc.username, "linux_user")
        self.assertIsNone(oc.get_steam64id_from_cookies())
        self.assertFalse(oc.is_session_alive())

    def test_unknown_method_raises(self):
        from api.Steam.steampy.exceptions import LoginRequired
        oc = self.OfflineSteamClient("u")
        with self.assertRaises(LoginRequired):
            oc.accept_trade_offer("x")


class TestManualConfirmDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = _isolated_cwd(cls)
        from utils import steam_client as sc
        from utils.steam_client import OfflineSteamClient
        cls.sc = sc
        cls.OfflineSteamClient = OfflineSteamClient

    def test_short_circuit(self):
        notifications = []
        orig = self.sc.send_notification
        self.sc.send_notification = lambda client, msg, title=None: notifications.append((msg, title))
        try:
            result = self.sc.accept_trade_offer(self.OfflineSteamClient("u"), threading.Lock(), "10001", desc="物品A")
            self.assertTrue(result)
            self.assertEqual(len(notifications), 1)
            self.assertIn("10001", notifications[0][0])
            self.assertEqual(notifications[0][1], "待人工确认发货")
        finally:
            self.sc.send_notification = orig


class TestSecretOptional(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = _isolated_cwd(cls)
        from utils import steam_client as sc
        cls.sc = sc
        # 构造「只有账号密码、secret 空」的账号文件
        cls._orig_account = sc.STEAM_ACCOUNT_INFO_FILE_PATH
        cls.account_path = os.path.join(cls._tmp, "steam_account_info.json5")
        sc.STEAM_ACCOUNT_INFO_FILE_PATH = cls.account_path
        with open(cls.account_path, "w", encoding="utf-8") as f:
            f.write('{"steam_username": "user", "steam_password": "pass", "shared_secret": "", "identity_secret": ""}')

    def test_empty_secret_not_rejected(self):
        sc = self.sc
        sc._check_proxy_availability = lambda config: False
        sc.pause = lambda: None
        errors = []
        orig_error = sc.logger.error
        sc.logger.error = lambda msg, *a, **k: errors.append(str(msg))
        try:
            result = sc.login_to_steam({})
        finally:
            sc.logger.error = orig_error
        # 返回 None 是因为代理检查失败（而非 secret 字段为空）
        self.assertIsNone(result)
        self.assertFalse(any("为空" in e for e in errors))


class TestLoggingChanges(unittest.TestCase):

    def test_default_log_level_info(self):
        from utils import static
        self.assertEqual(json5.loads(static.DEFAULT_CONFIG_JSON).get("log_level"), "info")


class TestPluginCheckSkipsFailed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = _isolated_cwd(cls)
        import Steamauto
        cls.Steamauto = Steamauto

    def test_skips_failed_plugins(self):
        class OkPlugin:
            def init(self):
                return False

        class FailPlugin:
            def init(self):
                return True

        class CrashPlugin:
            def init(self):
                raise RuntimeError("boom")

        plugins = self.Steamauto.plugins_check([OkPlugin(), FailPlugin(), CrashPlugin()])
        self.assertEqual(len(plugins), 1)
        self.assertIsInstance(plugins[0], OkPlugin)

    def test_empty_returns_empty_list(self):
        self.assertEqual(self.Steamauto.plugins_check([]), [])


class TestHeadlessModeAndManualConfirm(unittest.TestCase):
    def test_buff_skip_qrcode_gui_mode(self):
        with open(os.path.join(SRC, "utils", "buff_helper.py"), encoding="utf-8") as f:
            self.assertIn("STEAMAUTO_NO_PAUSE", f.read())

    def test_uu_skip_input_gui_mode(self):
        with open(os.path.join(SRC, "utils", "uu_helper.py"), encoding="utf-8") as f:
            self.assertIn("STEAMAUTO_NO_PAUSE", f.read())


    def test_buff_skip_binding_check_in_manual_confirm(self):
        """人工确认模式下，BUFF 插件跳过 Steam 账号与 BUFF 的绑定校验。"""
        with open(os.path.join(SRC, "plugins", "BuffAutoAcceptOffer.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("static.manual_confirm_delivery", src)
        mc_idx = src.index("static.manual_confirm_delivery")
        bind_idx = src.index('steam_info["max_bind_count"]')
        self.assertLess(mc_idx, bind_idx)

    def test_uu_sms_prompt_bridged(self):
        """UU 短信发送提示改为 input prompt，便于无终端/外部桥接场景回传。"""
        with open(os.path.join(SRC, "utils", "uu_helper.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('input("请编辑发送短信 "', src)


class TestBuffLoginWithoutSteam(unittest.TestCase):
    """未登录 Steam 时的 BUFF 登录降级（免鉴权路径）。

    事故背景：未登录 Steam 时仍去走 Steam OpenID 登录 BUFF，拿到的是 Steam 登录页
    而非授权表单 → `input_form` 为 None → 抛 `'NoneType' object has no attribute
    'find'`，日志里只有一句无信息量的未知异常。现已改为提前跳过 + 可读报错。
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = _isolated_cwd(cls)
        from utils import buff_helper

        cls.bh = buff_helper

    def test_parse_openid_params_raises_readable_error(self):
        """页面里没有 OpenID 表单时，应给出可读原因而不是 NoneType.find。"""
        with self.assertRaises(ValueError) as ctx:
            self.bh.parse_openid_params("<html><body>Steam 登录页</body></html>")
        msg = str(ctx.exception)
        self.assertIn("OpenID", msg)
        self.assertNotIn("NoneType", msg)

    def test_parse_openid_params_extracts_fields(self):
        html = (
            '<html><body><form id="openidForm">'
            '<input name="action" value="https://steamcommunity.com/openid/login"/>'
            '<input name="openid.mode" value="checkid_setup"/>'
            '<input name="openidparams" value="abc"/>'
            '<input name="nonce" value="xyz"/>'
            "</form></body></html>"
        )
        params = self.bh.parse_openid_params(html)
        self.assertEqual(params["openid.mode"], "checkid_setup")
        self.assertEqual(params["nonce"], "xyz")

    def test_steam_session_usable_false_when_offline(self):
        from utils.steam_client import OfflineSteamClient

        self.assertFalse(self.bh._steam_session_usable(OfflineSteamClient("u")))
        self.assertFalse(self.bh._steam_session_usable(None))

    def test_steam_session_usable_tolerates_probe_error(self):
        class Boom:
            def is_session_alive(self):
                raise RuntimeError("boom")

        self.assertFalse(self.bh._steam_session_usable(Boom()))

    def test_steam_session_usable_true_when_alive(self):
        class Alive:
            def is_session_alive(self):
                return True

        self.assertTrue(self.bh._steam_session_usable(Alive()))

    def test_skips_steam_path_when_not_logged_in(self):
        """源码级校验：Steam 会话不可用时应跳过 OpenID 分支。"""
        src = self.bh.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("_steam_session_usable(steam_client)", text)

    def test_qrcode_login_flow_reaches_echo(self):
        """直接跑二维码登录流程 —— 原始崩溃点（该函数内的 echo 未导入）。

        原 traceback：
            utils/buff_helper.py line 82, in login_to_buff_by_qrcode
                echo("请使用手机扫描上方二维码登录BUFF或...")
            NameError: name 'echo' is not defined
        这里把网络与二维码渲染全部 mock 掉，确定性执行到该 echo。
        """
        from unittest import mock

        state = {"poll": 0}

        class FakeCookies:
            def get_dict(self, domain=None):
                return {"session": "qrsession123"}

        class FakeResp:
            def __init__(self, payload=None):
                self._payload = payload or {}
                self.cookies = FakeCookies()
                self.status_code = 200

            def json(self):
                return self._payload

        class FakeSession:
            proxies = None

            def get(self, url, **kwargs):
                if "qr_code_login_open" in url:
                    return FakeResp({"code": "OK"})
                if "qr_code_poll" in url:
                    state["poll"] += 1
                    # 第一次：已扫码未确认（触发「扫描成功…」echo）；第二次：已确认（退出循环）
                    return FakeResp({"code": "OK", "data": {"state": 2 if state["poll"] == 1 else 3}})
                raise AssertionError("未预期的 GET: %s" % url)

            def post(self, url, **kwargs):
                if "qr_code_create" in url:
                    return FakeResp({"code": "OK", "data": {"code_id": "cid", "url": "https://x/qr"}})
                if "qr_code_login" in url:
                    return FakeResp({"code": "OK"})
                raise AssertionError("未预期的 POST: %s" % url)

        fake_img = mock.MagicMock()
        with mock.patch.object(self.bh.requests, "session", return_value=FakeSession()), \
                mock.patch.object(self.bh.qrcode, "make", return_value=fake_img), \
                mock.patch.object(self.bh.qrcode_terminal, "draw", lambda *a, **k: None), \
                mock.patch.object(self.bh, "send_notification", lambda *a, **k: None), \
                mock.patch.object(self.bh.time, "sleep", lambda *a, **k: None):
            session = self.bh.login_to_buff_by_qrcode(None)

        self.assertEqual(session, "qrsession123")
        self.assertGreaterEqual(state["poll"], 2, "轮询未走到「已扫码」与「已确认」两个状态")


if __name__ == "__main__":
    unittest.main(verbosity=2)
