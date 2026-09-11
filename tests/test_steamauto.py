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
import sys
import tempfile
import threading
import unittest


SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:
    sys.path.insert(0, SRC)


def _isolated_cwd():
    """临时目录隔离 config/logs/session，返回临时目录路径。"""
    d = tempfile.mkdtemp(prefix="steamauto-test-")
    os.chdir(d)
    return d


class TestOfflineSteamClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = _isolated_cwd()
        from utils.steam_client import OfflineSteamClient
        cls.OfflineSteamClient = OfflineSteamClient

    def test_identity(self):
        oc = self.OfflineSteamClient("linux_user")
        self.assertEqual(oc.username, "linux_user")
        self.assertIsNone(oc.get_steam64id_from_cookies())
        self.assertFalse(oc.is_session_alive())

    def test_unknown_method_raises(self):
        import steampy.exceptions
        oc = self.OfflineSteamClient("u")
        with self.assertRaises(steampy.exceptions.LoginRequired):
            oc.accept_trade_offer("x")


class TestManualConfirmDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = _isolated_cwd()
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
        cls._tmp = _isolated_cwd()
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
        cls._tmp = _isolated_cwd()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
