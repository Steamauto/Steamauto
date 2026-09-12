"""CLI 树状输出的回归测试（`--help` 与 `--status account`）。

对齐问题背景：原实现用 `"%-22s" % name`，但中文/全角字符在终端占 **2 列**而
`len()` 只数 1，导致含中文的列整列错位。现统一按**显示宽度**补白
（`_display_width` / `_pad`），并用树状结构呈现。

对齐的验证方式不依赖制表符的绝对宽度（├ ─ └ │ 属 East_Asian_Width=Ambiguous，
不同终端渲染宽度不同），而是断言**同一字段在各行的显示宽度偏移一致**。
"""

import os
import sys
import unicodedata
import unittest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from utils import cli  # noqa: E402


def _width(text):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(text))


def _render(accounts_map, steam, source="运行中的进程（实时）", live=True):
    """渲染并返回输出行列表。"""
    lines = []
    orig = cli._p
    cli._p = lambda msg="": lines.append(str(msg))
    try:
        cli._render_status(accounts_map, steam, source, live)
    finally:
        cli._p = orig
    return lines


def _sample_accounts():
    return {
        "buff": {"platform": "buff", "display": "BUFF（网易BUFF）", "configured": True,
                 "logged_in": True, "connected": True, "account": "洛北辰", "error": None},
        "uu": {"platform": "uu", "display": "UU（悠悠有品）", "configured": True,
               "logged_in": True, "connected": True, "account": "YP0006457561", "error": None},
        "c5": {"platform": "c5", "display": "C5（C5Game）", "configured": False,
               "logged_in": False, "connected": False, "account": None,
               "error": "未配置 AppKey（config set c5_auto_accept_offer.app_key <key>）"},
        "eco": {"platform": "eco", "display": "ECO（ECOsteam）", "configured": False,
                "logged_in": False, "connected": False, "account": None,
                "error": "未配置 partnerId（config set ecosteam.partnerId <id>）"},
    }


def _sample_steam():
    return {"platform": "steam", "display": "Steam", "configured": True, "logged_in": False,
            "connected": False, "account": "529918871",
            "error": "当前为离线模式（未登录 Steam）；买卖/上架等功能不受影响"}


# ============================================================ --help 树状渲染

class TestHelpTreeRendering(unittest.TestCase):
    """`--help` 必须用树状展示命令清单并按显示宽度对齐。"""

    @classmethod
    def setUpClass(cls):
        cls.text = cli.render_help()
        cls.lines = cls.text.split("\n")
        # 按渲染顺序取出所有「命令条目行」——不能靠文本搜索定位：
        # 例如「后台启动」是「直接后台启动（不做前台初始化）」的子串，
        # 且 "--login" 也出现在「说明」段里，搜索会命中错行（实测踩到过）。
        cls.item_lines = [
            l for l in cls.lines
            if l.startswith(("│  ", "   ")) and ("├─" in l or "└─" in l)
        ]
        cls.expected_items = sum(len(items) for _s, items in cli._HELP_SECTIONS)

    def test_title(self):
        self.assertEqual(self.lines[0], "Steamauto 可用操作")

    def test_all_sections_present(self):
        for section, _items in cli._HELP_SECTIONS:
            self.assertTrue(
                any(l.endswith(section) and l.startswith(("├─", "└─")) for l in self.lines),
                "缺少分组：%s" % section,
            )

    def test_tree_branch_characters(self):
        """组内子项带缩进与竖线延续；「说明」作为最后一条分支。"""
        self.assertEqual(len(self.item_lines), self.expected_items,
                         "命令条目行数应等于清单条目总数")
        self.assertTrue(any(l.startswith("│  ") for l in self.item_lines), "缺少竖线延续的缩进子项")
        self.assertTrue(any("└─ 说明" == l for l in self.lines), "缺少「说明」分支")

    def test_exactly_one_final_branch(self):
        """只有树状的最后一条分支（「说明」）用 └─；其余分组用 ├─。"""
        branches = [l for l in self.lines if l.startswith(("├─", "└─"))]
        self.assertEqual(branches[-1], "└─ 说明", branches[-1])
        corners = [l for l in branches if l.startswith("└─")]
        self.assertEqual(len(corners), 1, "应恰好一个 └─ 分支：%s" % corners)
        for section, _items in cli._HELP_SECTIONS:
            self.assertIn("├─ %s" % section, branches, "分组「%s」应用 ├─" % section)

    def test_no_legacy_separator(self):
        """旧排版用 ===== 分隔线，改造后不应再有。"""
        self.assertNotIn("=" * 10, self.text)

    def test_every_command_listed(self):
        for _section, items in cli._HELP_SECTIONS:
            for cmd, desc in items:
                self.assertIn(cmd, self.text, "缺少命令：%s" % cmd)
                self.assertIn(desc, self.text, "缺少说明：%s" % desc)

    def test_descriptions_align_within_each_section(self):
        """同一分组内，各命令的说明起始显示宽度必须一致（中文占 2 列）。"""
        cursor = 0
        for section, items in cli._HELP_SECTIONS:
            group = self.item_lines[cursor: cursor + len(items)]
            cursor += len(items)
            offsets = set()
            for (cmd, desc), row in zip(items, group):
                self.assertIn(cmd, row, "命令与行不匹配：%r" % row)
                offsets.add(_width(row[: row.index(desc)]))
            self.assertEqual(len(offsets), 1,
                             "分组「%s」说明列未对齐，偏移=%s" % (section, sorted(offsets)))

    def test_commands_padded_to_full_width(self):
        """命令列补齐到该组最长命令的显示宽度（<平台> 是全角，最易错）。"""
        cursor = 0
        for section, items in cli._HELP_SECTIONS:
            group = self.item_lines[cursor: cursor + len(items)]
            cursor += len(items)
            cmd_cols = max(_width(c) for c, _ in items)
            for (cmd, desc), row in zip(items, group):
                self.assertIn(cli._pad(cmd, cmd_cols) + "  " + desc, row,
                              "分组「%s」命令列未按显示宽度补齐：%r" % (section, row))

    def test_sub_item_branch_characters(self):
        """每组最后一个子项用 └─，其余用 ├─。"""
        cursor = 0
        for section, items in cli._HELP_SECTIONS:
            group = self.item_lines[cursor: cursor + len(items)]
            cursor += len(items)
            for j, row in enumerate(group):
                marker = "└─" if j == len(items) - 1 else "├─"
                self.assertIn(marker, row, "分组「%s」第 %d 条应使用 %s" % (section, j + 1, marker))

    def test_no_trailing_whitespace(self):
        for line in self.lines:
            self.assertEqual(line, line.rstrip(), "存在行尾空白：%r" % line)

    def test_notes_present(self):
        self.assertTrue(any("未登录 Steam 也能使用" in l for l in self.lines))
        self.assertTrue(any("--login/--logout" in l for l in self.lines))

    def test_notes_indent_matches_branch(self):
        """说明的正文缩进应与 `└─ ` 前缀同宽（3 列）。"""
        notes = self.lines[self.lines.index("└─ 说明") + 1:]
        self.assertTrue(notes)
        for note in notes:
            self.assertTrue(note.startswith("   "), "说明缩进不足：%r" % note)

    def test_help_commands_are_parseable(self):
        """守卫：帮助里出现的每条命令都必须能被真实解析器接受。

        否则帮助就成了「撒谎的文档」—— 用户照着敲会报错，这类偏差极难靠人眼发现。
        做法：把帮助里的命令去掉 `python Steamauto.py` 前缀后交给 parser 解析；
        需要值的占位符（<平台>/<KEY>/<VALUE>/<PATH>/<COMMAND>）替换成示例值，
        可选项（[--force] 之类）因本身已是合法 flag 直接保留。
        """
        import shlex

        placeholder = {
            "<平台>": "uu", "<KEY>": "log_level", "<VALUE>": "debug",
            "<PATH>": "some.log", "<COMMAND>": "ping",
        }
        checked = 0
        for _section, items in cli._HELP_SECTIONS:
            for cmd, _desc in items:
                rest = cmd.replace("python Steamauto.py", "").strip()
                if not rest:
                    continue  # 无参数启动，交由其它测试覆盖
                for k, v in placeholder.items():
                    rest = rest.replace(k, v)
                argv = shlex.split(rest)
                with self.subTest(cmd=cmd):
                    # 只要求「可解析」，不求成功执行（避免真的去启动/停止服务）
                    args = cli.build_parser().parse_args(argv)
                    self.assertIsNotNone(args)
                checked += 1
        self.assertGreaterEqual(checked, 15, "应校验到足够多的帮助条目，实际 %d" % checked)

    def test_no_legacy_subcommand_in_help(self):
        """帮助里不应再出现旧子命令写法（如 `python Steamauto.py run`）。

        按「前缀 + 空白/结尾」判断，而不是精确整行匹配 —— 帮助行后面还有说明文字，
        精确匹配会永远不命中（此前的写法就是因此漏过变异测试的）。
        """
        import re

        for legacy in ("run", "start", "stop", "restart", "status", "logs", "config", "ctl"):
            pattern = r"python Steamauto\.py %s(\s|$)" % legacy
            self.assertIsNone(
                re.search(pattern, self.text, re.M),
                "帮助仍含旧子命令写法：python Steamauto.py %s" % legacy,
            )

    def test_help_covers_every_implemented_flag(self):
        """守卫：每个**核心命令** flag 都必须在帮助里出现。

        与 test_every_command_listed（遍历清单自身）互补 —— 后者检查「清单渲染完整」，
        本条检查「文档覆盖实现」。缺一条就说明帮助漏了功能
        （变异测试实测能抓到「删掉 --restart 条目」这类情况）。

        只校验核心命令：`--port`/`--timeout`/`--lines`/`--file` 属从属细节，
        列进帮助会让清单过长；它们由 argparse 的 --help 兜底（见 build_parser 的 help=）。
        """
        core = {
            "--run", "--daemon", "--start", "--stop", "--restart", "--force",
            "--status", "--json", "--no-live", "--login", "--logout",
            "--log", "--follow", "--console",
            "--config", "--get", "--set", "--unset", "--list", "--reload",
            "--str", "--no-apply", "--ctl", "--help",
        }
        implemented = {
            opt
            for action in cli.build_parser()._actions
            for opt in action.option_strings
            if opt.startswith("--")
        }
        missing_impl = core - implemented
        self.assertEqual(missing_impl, set(), "核心清单里有解析器未实现的项：%s" % sorted(missing_impl))

        for opt in sorted(core):
            self.assertIn(opt, self.text, "帮助缺少核心选项：%s" % opt)


# ============================================================ 显示宽度工具

class TestDisplayWidth(unittest.TestCase):
    def test_ascii_counts_one(self):
        self.assertEqual(cli._display_width("abc"), 3)
        self.assertEqual(cli._display_width(""), 0)

    def test_cjk_counts_two(self):
        self.assertEqual(cli._display_width("中"), 2)
        self.assertEqual(cli._display_width("中文字"), 6)

    def test_fullwidth_punctuation(self):
        # 全角括号在终端占 2 列
        self.assertEqual(cli._display_width("（"), 2)
        self.assertEqual(cli._display_width("BUFF（网易）"), 4 + 2 + 4 + 2)

    def test_mixed(self):
        self.assertEqual(cli._display_width("C5（C5Game）"), 2 + 2 + 6 + 2)

    def test_pad_uses_display_width(self):
        self.assertEqual(_width(cli._pad("中", 10)), 10)
        self.assertEqual(_width(cli._pad("abc", 10)), 10)
        self.assertEqual(_width(cli._pad("BUFF（网易BUFF）", 20)), 20)

    def test_pad_never_truncates(self):
        self.assertEqual(cli._pad("中文字", 2), "中文字")

    def test_clip_by_display_width(self):
        clipped = cli._clip("a" * 50, 10)
        self.assertTrue(clipped.endswith("…"))
        self.assertLessEqual(_width(clipped), 10)

    def test_clip_short_text_unchanged(self):
        self.assertEqual(cli._clip("abc", 10), "abc")

    def test_clip_never_splits_cjk(self):
        # 中文按 2 列计，7 列预算时应能放下 3 个汉字（6 列）+ 省略号
        clipped = cli._clip("中文中文中文", 7)
        self.assertLessEqual(_width(clipped), 7)
        self.assertTrue(clipped.startswith("中文"))
        self.assertTrue(clipped.endswith("…"))


# ============================================================ 树状结构

class TestStatusTreeRendering(unittest.TestCase):
    def setUp(self):
        self.lines = _render(_sample_accounts(), _sample_steam())
        self.text = "\n".join(self.lines)
        self.plat_lines = self._platform_rows(self.lines)

    def test_title(self):
        self.assertEqual(self.lines[0], "各平台账号状态")

    def test_meta_lines(self):
        self.assertTrue(any("来源：" in l for l in self.lines))
        self.assertTrue(any("联网校验：" in l for l in self.lines))

    def test_five_platform_rows(self):
        self.assertEqual(len(self.plat_lines), 5, self.plat_lines)

    def test_tree_branch_characters(self):
        self.assertTrue(all(l.startswith("├─") for l in self.plat_lines[:4]))
        self.assertTrue(self.plat_lines[-1].startswith("└─"), "末行应用 └─ 收尾")

    def test_detail_rows_are_indented(self):
        details = [l for l in self.lines if l.startswith("   └─")]
        self.assertEqual(len(details), 5, details)

    def test_columns_are_aligned(self):
        """核心：同一字段在所有平台行的显示宽度偏移必须一致。"""
        for label, variants in (
            ("已配置", ("已配置", "未配置")),
            ("已登录", ("已登录", "未登录")),
            ("连接", ("连接可用", "未校验")),
        ):
            offsets = set()
            for line in self.plat_lines:
                for v in variants:
                    if v in line:
                        offsets.add(_width(line[: line.index(v)]))
                        break
            self.assertEqual(len(offsets), 1, "%s 列未对齐，偏移=%s" % (label, sorted(offsets)))

    def test_no_trailing_whitespace(self):
        for line in self.lines:
            self.assertEqual(line, line.rstrip(), "存在行尾空白：%r" % line)

    def test_long_details_go_to_subrows(self):
        """长说明/账号放子行，避免把表格撑歪。"""
        for line in self.plat_lines:
            self.assertNotIn("AppKey", line)
            self.assertNotIn("离线模式", line)
        self.assertTrue(any(l.startswith("   └─") and "账号：" in l for l in self.lines))
        self.assertTrue(any(l.startswith("   └─") and "未配置 AppKey" in l for l in self.lines))

    def test_account_and_error_joined(self):
        self.assertTrue(any("｜" in l for l in self.lines))

    def test_three_state_labels(self):
        self.assertIn("已配置", self.text)
        self.assertIn("未配置", self.text)
        self.assertIn("已登录", self.text)
        self.assertIn("未登录", self.text)
        self.assertIn("连接可用", self.text)
        self.assertIn("未校验", self.text)

    def test_hint_present(self):
        self.assertTrue(any("--login" in l for l in self.lines))
        self.assertTrue(any("--status account --json" in l for l in self.lines))

    def test_balance_shown_when_present(self):
        """状态里有 balance 时，应显示「可用余额：¥xxx」。"""
        accts = _sample_accounts()
        accts["buff"]["balance"] = "156.18"
        accts["uu"]["balance"] = 6.53
        lines = _render(accts, _sample_steam())
        self.assertTrue(any("可用余额：¥156.18" in l for l in lines))
        self.assertTrue(any("可用余额：¥6.53" in l for l in lines))

    def test_balance_absent_when_missing(self):
        """无 balance 字段时（如旧进程），不显示余额。"""
        lines = _render(_sample_accounts(), _sample_steam())
        self.assertFalse(any("可用余额" in l for l in lines))

    def test_handles_missing_platform(self):
        """缺失的平台用空白状态补齐，不应崩溃。"""
        lines = _render({"buff": _sample_accounts()["buff"]}, None)
        self.assertEqual(len(self._platform_rows(lines)), 4)

    def test_no_steam_entry(self):
        lines = _render(_sample_accounts(), None)
        self.assertNotIn("Steam", "\n".join(self._platform_rows(lines)))
        self.assertEqual(len(self._platform_rows(lines)), 4)

    @staticmethod
    def _platform_rows(lines):
        return [
            l for l in lines
            if l.startswith(("├─", "└─")) and "来源：" not in l and "联网校验：" not in l
        ]


if __name__ == "__main__":
    unittest.main(verbosity=2)
