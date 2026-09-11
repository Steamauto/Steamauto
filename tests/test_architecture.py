"""项目架构回归测试：平台 API 包位于 api/ 下的目录布局与导入完整性。

背景：交易平台 SDK（BuffApi / uuyoupinapi / PyC5Game / PyECOsteam / steampy /
protobufs）统一收纳到 api/ 下，Steam 相关的放进 api/Steam/。

这类重构的风险在于「静默失败」：现有测试并不 import 插件，也不 import 各平台包，
所以某个 import 路径写错时测试依然全绿，只有真正跑到那个插件才炸。本文件把
「包可导入 + 旧路径无残留 + 打包/lint 配置同步」固化成断言。

用法：python -m pytest tests/test_architecture.py -v
"""

import importlib
import io
import os
import sys
import unittest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# 期望的目录布局：路径 -> 是否为包（需 __init__.py）
EXPECTED_LAYOUT = {
    "api": True,
    "api/BuffApi": True,
    "api/uuyoupinapi": True,
    "api/PyC5Game": True,
    "api/PyECOsteam": True,
    "api/Steam": True,
    "api/Steam/steampy": True,
    "api/Steam/protobufs": True,
}

# 必须能从新位置导入的模块
IMPORTABLE_MODULES = [
    "api.BuffApi",
    "api.BuffApi.models",
    "api.BuffApi.BuffApiCrypt",
    "api.uuyoupinapi",
    "api.uuyoupinapi.models",
    "api.uuyoupinapi.UUApiCrypt",
    "api.PyC5Game",
    "api.PyECOsteam",
    "api.PyECOsteam.models",
    "api.PyECOsteam.sign",
    "api.Steam.steampy",
    "api.Steam.steampy.client",
    "api.Steam.steampy.login",
    "api.Steam.steampy.market",
    "api.Steam.steampy.confirmation",
    "api.Steam.steampy.chat",
    "api.Steam.steampy.utils",
    "api.Steam.steampy.exceptions",
    "api.Steam.steampy.models",
    "api.Steam.steampy.guard",
    "api.Steam.protobufs",
    "api.Steam.protobufs.enums_pb2",
    "api.Steam.protobufs.steammessages_auth.steamclient_pb2",
    "api.Steam.protobufs.steammessages_unified_base.steamclient_pb2",
]

# 被搬进 api/ 的包：旧根级目录不应残留，全仓也不应再有指向旧位置的绝对 import
MOVED_PACKAGES = ("BuffApi", "uuyoupinapi", "PyC5Game", "PyECOsteam", "steampy", "protobufs")


def _read(rel):
    with io.open(os.path.join(SRC, rel), "r", encoding="utf-8") as f:
        return f.read()


class TestApiLayout(unittest.TestCase):
    def test_directories_exist(self):
        for rel, needs_init in EXPECTED_LAYOUT.items():
            path = os.path.join(SRC, rel)
            self.assertTrue(os.path.isdir(path), "缺少目录：%s" % rel)
            if needs_init:
                self.assertTrue(
                    os.path.isfile(os.path.join(path, "__init__.py")),
                    "%s 缺少 __init__.py（不是普通包）" % rel,
                )

    def test_old_root_dirs_removed(self):
        """旧位置不应残留（残留会让 import 走错路径而不报错）。"""
        for name in MOVED_PACKAGES:
            self.assertFalse(
                os.path.exists(os.path.join(SRC, name)),
                "根目录仍存在 %s，应已移入 api/" % name,
            )

    def test_buffapicrypt_moved_into_buffapi(self):
        self.assertTrue(os.path.isfile(os.path.join(SRC, "api", "BuffApi", "BuffApiCrypt.py")))
        self.assertFalse(
            os.path.exists(os.path.join(SRC, "utils", "BuffApiCrypt.py")),
            "utils/BuffApiCrypt.py 应已移入 api/BuffApi/",
        )

    def test_steam_packages_nested_under_api_steam(self):
        self.assertTrue(os.path.isdir(os.path.join(SRC, "api", "Steam", "steampy")))
        self.assertTrue(os.path.isdir(os.path.join(SRC, "api", "Steam", "protobufs")))


class TestApiImports(unittest.TestCase):
    def test_all_api_modules_importable(self):
        for name in IMPORTABLE_MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_steampy_internal_relative_imports(self):
        """steampy 内部一律用相对导入（才能被搬到任意父包下）。"""
        folder = os.path.join(SRC, "api", "Steam", "steampy")
        offenders = []
        for fname in os.listdir(folder):
            if not fname.endswith(".py"):
                continue
            for lineno, line in enumerate(_read("api/Steam/steampy/%s" % fname).splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("from steampy", "import steampy")):
                    offenders.append("api/Steam/steampy/%s:%d %s" % (fname, lineno, stripped))
        self.assertEqual(offenders, [], "steampy 内仍有绝对自引用：%s" % offenders)

    def test_protobufs_relative_import(self):
        """生成的 protobuf 代码里的跨文件引用也必须是相对导入。"""
        src = _read("api/Steam/protobufs/steammessages_auth/steamclient_pb2.py")
        self.assertNotIn("from protobufs.", src)
        self.assertIn("from ..steammessages_unified_base import steamclient_pb2", src)

    def test_steampy_exceptions_attribute_available(self):
        """utils.steam_client 依赖 `steampy.exceptions.X` 这种属性访问，必须可解析。

        `from api.Steam import steampy` 只绑定包本身（__init__.py 为空），不会自动
        加载子模块；若漏掉显式的子模块导入，这行会在运行到异常处理分支时才失败。
        """
        import utils.steam_client as sc

        for attr in ("InvalidCredentials", "ConfirmationExpected", "LoginRequired"):
            self.assertTrue(hasattr(sc.steampy.exceptions, attr), "缺少 steampy.exceptions.%s" % attr)

    def test_offline_client_raises_api_steampy_loginrequired(self):
        from api.Steam.steampy.exceptions import LoginRequired
        from utils.steam_client import OfflineSteamClient

        with self.assertRaises(LoginRequired):
            OfflineSteamClient("u").accept_trade_offer("x")

    def test_all_plugins_importable(self):
        """插件通过 api.* 引用平台包，导入失败说明路径写错。"""
        folder = os.path.join(SRC, "plugins")
        for fname in sorted(os.listdir(folder)):
            if not fname.endswith(".py") or fname.startswith("__"):
                continue
            with self.subTest(plugin=fname):
                importlib.import_module("plugins.%s" % fname[:-3])


class TestNoStaleReferences(unittest.TestCase):
    """全仓不应再有指向旧位置的绝对 import。"""

    SCAN_DIRS = ["", "utils", "plugins", "tests"]

    def _iter_py_files(self):
        for rel in self.SCAN_DIRS:
            folder = os.path.join(SRC, rel)
            for fname in os.listdir(folder):
                if fname.endswith(".py"):
                    yield os.path.join(folder, fname)

    def test_no_absolute_import_of_moved_packages(self):
        offenders = []
        for path in self._iter_py_files():
            with io.open(path, "r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    stripped = line.strip()
                    for mod in MOVED_PACKAGES:
                        if stripped.startswith(("from %s " % mod, "from %s." % mod, "import %s" % mod)):
                            offenders.append("%s:%d %s" % (os.path.relpath(path, SRC), lineno, stripped))
        self.assertEqual(offenders, [], "仍引用旧位置的模块：%s" % offenders)

    def test_imports_use_api_prefix(self):
        """被搬运包的引用应统一带 api. 前缀（或相对导入）。"""
        self.assertIn("from api.Steam.steampy.client import SteamClient", _read("Steamauto.py"))
        self.assertIn("from api.BuffApi import BuffAccount", _read("plugins/BuffAutoAcceptOffer.py"))
        self.assertIn("import api.uuyoupinapi as uuyoupinapi", _read("utils/uu_helper.py"))
        self.assertIn("from api.PyC5Game import C5Account", _read("plugins/C5AutoAcceptOffer.py"))
        self.assertIn("from api.PyECOsteam import ECOsteamClient, models", _read("plugins/ECOsteam.py"))


class TestBuildAndLintConfig(unittest.TestCase):
    """打包与 lint 配置必须跟着目录一起改，否则打包产物缺模块 / 生成代码被 lint。"""

    def test_build_spec_hidden_imports(self):
        spec = _read("build.spec")
        for mod in ("api.BuffApi", "api.PyC5Game", "api.PyECOsteam", "api.uuyoupinapi",
                    "api.Steam.steampy", "api.Steam.protobufs"):
            self.assertIn("'%s'" % mod, spec, "build.spec 缺少 hidden import：%s" % mod)
        for stale in ("'BuffApi'", "'PyC5Game'", "'PyECOsteam'", "'uuyoupinapi'", "'utils.ApiCrypt'"):
            self.assertNotIn(stale, spec, "build.spec 仍有过时条目：%s" % stale)

    def test_ruff_excludes_moved_protobufs(self):
        ruff = _read("ruff.toml")
        self.assertIn("api/Steam/protobufs", ruff)
        self.assertNotIn('exclude = ["protobufs"]', ruff)


if __name__ == "__main__":
    unittest.main(verbosity=2)
