"""平台 API 命令行（utils.api_cli）的回归测试。

覆盖：命令清单 / 帮助渲染 / 未知操作与平台 / 凭据缺失 / 命令分发 / JSON 与表格输出 /
cli.main 的平台命令分流。

真实网络与 SDK 不参与：通过替换 ``_CLIENT_FACTORIES`` 注入 fake client，
保证测试离线、可重复、不依赖凭据。
"""

import contextlib
import io
import os
import sys
import unittest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from utils import api_cli, cli  # noqa: E402


def _run(platform, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = api_cli.main(platform, argv)
    return rc, out.getvalue(), err.getvalue()


class _FakeBuff:
    def get_user_brief_assest(self):
        return {
            "cash_amount": "156.18",
            "total_able_withdraw_amount": "156.18",
            "total_unable_withdraw_amount": "0",
            "frozen_amount": "0",
        }

    def get_user_nickname(self):
        return "洛北辰"

    def search_goods(self, key, game="csgo"):
        return [
            {"option": "AK-47 | 红线 (久经沙场)", "goods_ids": "33960"},
            {"option": "AK-47 | 墨岩 (久经沙场)", "goods_ids": "857550"},
        ]

    def get_sell_order_history(self, appid):
        return [{"assetid": "1", "price": "3.5"}]

    def get_inventory_all(self):
        return [{"assetid": "1", "goods_id": 773534, "name": "印花胶囊"}]

    def search_market(self, keyword, game="csgo", page_num=1, page_size=100):
        return {"code": "OK", "data": {"total_count": 714, "items": [{"goods_id": 33960}]}}

    def get_buy_order(self, goods_id, game="csgo", page_num=1, page_size=10):
        return {"code": "OK", "data": {"items": [{"price": "378"}]}}

    def get_buy_order_max(self, goods_id, game="csgo"):
        return "378"

    def get_sell_min(self, goods_id, game="csgo"):
        return "182"

    # ---- 写操作（mock）----
    def change_price(self, sell_orders):
        return {"success": len(sell_orders)}

    def on_sale(self, assets):
        return [a.assetid for a in assets], {}

    def cancel_sale(self, sell_orders, exclude_sell_orders=[]):
        return len(sell_orders), {}

    def buy_goods(self, **kwargs):
        return {"success": True}


class _FakeC5:
    def balance(self):
        return {"success": True, "data": {"balance": "100.0"}}

    def checkAppKey(self):
        return True


def _inject(platform, client):
    """临时替换某平台的客户端工厂，返回还原函数。"""
    orig = api_cli._CLIENT_FACTORIES[platform]
    api_cli._CLIENT_FACTORIES[platform] = lambda cfg: client
    return lambda: api_cli._CLIENT_FACTORIES.__setitem__(platform, orig)


class TestHelpAndDispatch(unittest.TestCase):
    def test_help_lists_ops(self):
        rc, out, _ = _run("buff", ["--help"])
        self.assertEqual(rc, 0)
        for op in ("balance", "nickname", "search", "search-market", "inventory",
                   "on-sale", "sell-history", "buy-order", "highest-buy", "lowest-sell",
                   "waiting-offer", "list", "sell-bidder", "off-shelf", "change-price", "buy"):
            self.assertIn(op, out, "--buff --help 缺 %s" % op)

    def test_no_args_shows_help(self):
        rc, out, _ = _run("buff", [])
        self.assertEqual(rc, 0)
        self.assertIn("balance", out)

    def test_unknown_operation(self):
        rc, out, err = _run("buff", ["nosuchop"])
        self.assertEqual(rc, 2)
        self.assertIn("未知操作", err)
        self.assertIn("balance", out, "报错后应顺带打印帮助")

    def test_unknown_platform(self):
        rc, _out, err = _run("xxx", [])
        self.assertEqual(rc, 2)
        self.assertIn("未知平台", err)

    def test_platform_alias_resolves(self):
        """平台名走 accounts.resolve 的别名（如 buffapi）。"""
        # resolve 只对已知别名生效；这里验证 api_cli 用它归一化
        self.assertEqual(api_cli.accounts.resolve("buffapi"), "buff")

    def test_every_platform_has_commands(self):
        for plat in ("buff", "uu", "c5", "eco"):
            ops = api_cli._COMMANDS[plat]()
            self.assertTrue(ops, "%s 无命令" % plat)
            for op, (fn, desc) in ops.items():
                self.assertTrue(callable(fn), "%s.%s 不是函数" % (plat, op))
                self.assertTrue(desc, "%s.%s 缺描述" % (plat, op))


class TestMissingCredential(unittest.TestCase):
    def setUp(self):
        self._orig_cred = api_cli.accounts.credential_path
        self._orig_load = api_cli.accounts.load_config

    def tearDown(self):
        api_cli.accounts.credential_path = self._orig_cred
        api_cli.accounts.load_config = self._orig_load

    def test_buff_requires_credential(self):
        """凭据文件缺失时应报可读错误（走真实 _buff_client，不 mock 工厂）。"""
        api_cli.accounts.credential_path = lambda platform: None
        rc, _out, err = _run("buff", ["balance"])
        self.assertEqual(rc, 1)
        self.assertIn("凭据", err)

    def test_uu_requires_credential(self):
        api_cli.accounts.credential_path = lambda platform: None
        rc, _out, err = _run("uu", ["inventory"])
        self.assertEqual(rc, 1)
        self.assertIn("凭据", err)

    def test_c5_requires_app_key(self):
        """C5 无会话，靠 config 里的 app_key；空配置应报错。"""
        api_cli.accounts.load_config = lambda: {}
        rc, _out, err = _run("c5", ["balance"])
        self.assertEqual(rc, 1)
        self.assertIn("AppKey", err)


class TestDispatchAndOutput(unittest.TestCase):
    def setUp(self):
        self._restore = _inject("buff", _FakeBuff())
        # 让 load_config 返回空配置（fake client 不依赖真实 config）
        self._orig_load = api_cli.accounts.load_config
        api_cli.accounts.load_config = lambda: {}

    def tearDown(self):
        self._restore()
        api_cli.accounts.load_config = self._orig_load

    def test_json_is_default(self):
        rc, out, _ = _run("buff", ["balance"])
        self.assertEqual(rc, 0)
        self.assertIn('"available"', out)
        self.assertIn('"trading_only"', out)
        self.assertIn('"frozen"', out)

    def test_positional_args_passed_to_handler(self):
        # search 接收 key + 可选 game
        rc, out, _ = _run("buff", ["search", "AK-47"])
        self.assertEqual(rc, 0)
        self.assertIn("红线", out)

    def test_table_output(self):
        rc, out, _ = _run("buff", ["search", "AK-47", "--table"])
        self.assertEqual(rc, 0)
        self.assertIn("option", out)
        self.assertIn("goods_ids", out)
        self.assertNotIn('"goods_ids"', out, "表格模式不应输出 JSON 键的引号")

    def test_table_flag_off_then_json(self):
        # 后出现的 --json 覆盖前面的 --table
        rc, out, _ = _run("buff", ["balance", "--table", "--json"])
        self.assertEqual(rc, 0)
        self.assertIn('"available"', out)

    def test_help_flag_wins_over_positional(self):
        rc, out, _ = _run("buff", ["balance", "--help"])
        self.assertEqual(rc, 0)
        self.assertIn("balance", out)
        self.assertIn("on-sale", out)

    def test_highest_buy_passes_goods_id(self):
        rc, out, _ = _run("buff", ["highest-buy", "33960"])
        self.assertEqual(rc, 0)
        self.assertIn("378", out)

    def test_lowest_sell_passes_goods_id(self):
        rc, out, _ = _run("buff", ["lowest-sell", "33960"])
        self.assertEqual(rc, 0)
        self.assertIn("182", out)

    def test_search_market_passes_keyword(self):
        rc, out, _ = _run("buff", ["search-market", "AK-47"])
        self.assertEqual(rc, 0)
        self.assertIn("714", out)

    def test_inventory_returns_list(self):
        rc, out, _ = _run("buff", ["inventory", "--table"])
        self.assertEqual(rc, 0)
        self.assertIn("印花胶囊", out)


class TestTableRenderer(unittest.TestCase):
    def test_list_of_dict(self):
        rows = [{"a": "中文", "b": "1"}, {"a": "x", "b": "2"}]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = api_cli._emit(rows, as_table=True)
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("a", text)
        self.assertIn("中文", text)

    def test_scalar_falls_back_to_json(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            api_cli._emit("洛北辰", as_table=True)
        self.assertIn('"洛北辰"', out.getvalue())

    def test_dict_falls_back_to_json(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            api_cli._emit({"k": "v"}, as_table=True)
        self.assertIn('"k"', out.getvalue())


class TestCliPlatformRouting(unittest.TestCase):
    def test_platform_flag_routes_to_api_cli(self):
        """`python Steamauto.py --buff --help` 走 api_cli 而非主 parser。"""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["--buff", "--help"])
        self.assertEqual(rc, 0)
        self.assertIn("balance", out.getvalue(), "--buff --help 应列出 BUFF 操作")

    def test_parser_accepts_platform_flags(self):
        """帮助文档里的 `--buff <OP>` 必须能被主 parser 解析（REMAINDER 兜底）。"""
        args = cli.build_parser().parse_args(["--buff", "balance"])
        self.assertEqual(args.buff, ["balance"])

    def test_help_lists_platform_section(self):
        import contextlib as _ctx

        out = io.StringIO()
        with _ctx.redirect_stdout(out):
            cli.cmd_help()
        self.assertIn("平台 API", out.getvalue())


class _FakeResponse:
    """模拟 requests.Response：带 .json() 方法。"""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class TestNormalize(unittest.TestCase):
    def test_response_gets_jsonified(self):
        """SDK 返回 Response（如 BUFF get_on_sale / UU get_template_purchase_order）应转 dict。"""
        self.assertEqual(api_cli._normalize(_FakeResponse({"code": "OK"})), {"code": "OK"})

    def test_dict_passes_through(self):
        self.assertEqual(api_cli._normalize({"a": 1}), {"a": 1})

    def test_list_passes_through(self):
        self.assertEqual(api_cli._normalize([1, 2]), [1, 2])

    def test_scalar_passes_through(self):
        self.assertEqual(api_cli._normalize("洛北辰"), "洛北辰")


class TestResponseReturningSdk(unittest.TestCase):
    """端到端：命令处理函数返回 Response 时，输出应为 JSON 而非报错。"""

    def test_on_sale_response_serializes(self):
        class _Client:
            def get_on_sale(self, page_num=1):
                return _FakeResponse({"code": "OK", "data": {"total_count": 64}})

        restore = _inject("buff", _Client())
        orig_load = api_cli.accounts.load_config
        api_cli.accounts.load_config = lambda: {}
        try:
            rc, out, _ = _run("buff", ["on-sale"])
        finally:
            restore()
            api_cli.accounts.load_config = orig_load
        self.assertEqual(rc, 0)
        self.assertIn('"total_count"', out)


class TestWriteOps(unittest.TestCase):
    """写操作命令的二次确认机制（--yes / --dry-run / 非交互式拦截）。"""

    def setUp(self):
        self._restore = _inject("buff", _FakeBuff())
        self._orig_load = api_cli.accounts.load_config
        api_cli.accounts.load_config = lambda: {}

    def tearDown(self):
        self._restore()
        api_cli.accounts.load_config = self._orig_load

    def test_dry_run_does_not_execute(self):
        """--dry-run 只预览，不调用 SDK。"""
        rc, out, _ = _run("buff", ["off-shelf", "12345", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("dry-run", out)
        self.assertIn("off-shelf", out)

    def test_non_interactive_requires_yes(self):
        """非交互式终端（如 Hermes terminal）无 --yes 应拦截，返回 2。"""
        rc, out, err = _run("buff", ["off-shelf", "12345"])
        self.assertEqual(rc, 2)
        self.assertIn("--yes", err)

    def test_yes_bypasses_confirmation(self):
        """--yes 跳过确认，直接执行（mock client 返回结果）。"""
        rc, out, _ = _run("buff", ["change-price", "S1", "5.5", "--yes"])
        self.assertEqual(rc, 0)

    def test_read_op_needs_no_confirmation(self):
        """只读命令不需要确认，直接执行。"""
        rc, out, _ = _run("buff", ["balance"])
        self.assertEqual(rc, 0)
        self.assertIn("available", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
