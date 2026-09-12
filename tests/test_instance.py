"""多开实例（utils.instance + cli --instance/--instances）的回归测试。

覆盖：实例名规范化、数据目录映射、端口分配（排除已配置）、实例初始化、
实例列表、CLI 分流。全部用临时目录隔离，不碰真实项目数据。
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

from utils import cli, instance, static  # noqa: E402


class _TmpInstances(unittest.TestCase):
    """把 static.INSTANCES_DIR / _BASE_DIR 重定向到临时目录。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sa-inst-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._orig = {
            "INSTANCES_DIR": static.INSTANCES_DIR,
            "_BASE_DIR": getattr(static, "_BASE_DIR", None),
        }
        static.INSTANCES_DIR = os.path.join(self.tmp, "instances")
        static._BASE_DIR = os.path.join(self.tmp, "default")
        os.makedirs(static._BASE_DIR, exist_ok=True)

    def tearDown(self):
        static.INSTANCES_DIR = self._orig["INSTANCES_DIR"]
        if self._orig["_BASE_DIR"] is not None:
            static._BASE_DIR = self._orig["_BASE_DIR"]

    def write_config(self, base, port):
        cfg_dir = os.path.join(base, "config")
        os.makedirs(cfg_dir, exist_ok=True)
        with open(os.path.join(cfg_dir, "config.json5"), "w", encoding="utf-8") as f:
            f.write('{ control: { enable: true, port: %d } }\n' % port)


class TestNormalize(unittest.TestCase):
    def test_empty_is_default(self):
        self.assertEqual(instance.normalize(""), "default")

    def test_default_case_insensitive(self):
        self.assertEqual(instance.normalize("DEFAULT"), "default")

    def test_named(self):
        self.assertEqual(instance.normalize("alice"), "alice")

    def test_strips_whitespace(self):
        self.assertEqual(instance.normalize("  bob  "), "bob")

    def test_illegal_name(self):
        with self.assertRaises(ValueError):
            instance.normalize("a/b")
        with self.assertRaises(ValueError):
            instance.normalize("..")


class TestBaseDir(_TmpInstances):
    def test_default_under_instances_dir(self):
        self.assertEqual(instance.base_dir("default"), os.path.join(static.INSTANCES_DIR, "default"))

    def test_named_under_instances_dir(self):
        self.assertEqual(
            instance.base_dir("alice"),
            os.path.join(static.INSTANCES_DIR, "alice"),
        )


class TestConfiguredPorts(_TmpInstances):
    def test_extracts_ports_from_all_instances(self):
        self.write_config(static._BASE_DIR, 45917)  # default
        self.write_config(os.path.join(static.INSTANCES_DIR, "a"), 45918)
        self.write_config(os.path.join(static.INSTANCES_DIR, "b"), 45920)
        ports = instance._configured_ports()
        self.assertEqual(ports, {45917, 45918, 45920})

    def test_ignores_missing_config(self):
        self.assertEqual(instance._configured_ports(), set())


class TestAllocatePort(_TmpInstances):
    def test_skips_configured_ports(self):
        """已配置但未运行的端口必须被跳过（否则两个实例分到同一端口）。"""
        self.write_config(static._BASE_DIR, 45917)
        self.write_config(os.path.join(static.INSTANCES_DIR, "a"), 45918)
        # mock socket bind 永远成功 → 返回第一个「不在 used 且能 bind」的端口
        import utils.instance as inst_mod

        orig_socket = inst_mod.socket
        inst_mod.socket = _FakeSocketModule()
        try:
            port = instance.allocate_port(start=45917)
        finally:
            inst_mod.socket = orig_socket
        self.assertEqual(port, 45919, "应跳过 45917/45918，分到 45919")


class _FakeSocketModule:
    """socket 模块的替身：socket() 返回的上下文 bind 永远成功（不真正占端口）。"""

    AF_INET = 2
    SOCK_STREAM = 1

    class _Ctx:
        def bind(self, _addr):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def socket(self, *_a, **_k):
        return self._Ctx()


class TestEnsureInstance(_TmpInstances):
    def test_creates_config_and_account(self):
        from unittest import mock

        with mock.patch.object(instance, "allocate_port", return_value=45999):
            bd, created = instance.ensure_instance("alice")
        self.assertTrue(created)
        self.assertTrue(os.path.exists(os.path.join(bd, "config", "config.json5")))
        self.assertTrue(os.path.exists(os.path.join(bd, "config", "steam_account_info.json5")))
        with open(os.path.join(bd, "config", "config.json5"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn('"port": 45999', text, "分配的端口应写入实例 config")

    def test_default_creates_instance(self):
        """default 实例也创建目录（instances/default）+ 生成配置。"""
        from unittest import mock

        with mock.patch.object(instance, "_migrate_legacy_default"), mock.patch.object(instance, "allocate_port", return_value=45917):
            bd, created = instance.ensure_instance("default")
        self.assertTrue(created)
        self.assertEqual(bd, os.path.join(static.INSTANCES_DIR, "default"))
        self.assertTrue(os.path.exists(os.path.join(bd, "config", "config.json5")))


class TestListInstances(_TmpInstances):
    def test_lists_default_and_named(self):
        from unittest import mock

        os.makedirs(os.path.join(static.INSTANCES_DIR, "alice"), exist_ok=True)
        os.makedirs(os.path.join(static.INSTANCES_DIR, "bob"), exist_ok=True)
        os.makedirs(os.path.join(static.INSTANCES_DIR, "default"), exist_ok=True)
        with mock.patch("utils.daemon.pid_alive", return_value=True):
            entries = instance.list_instances()
        names = [e["name"] for e in entries]
        self.assertEqual(names, sorted(names), "实例应按名称排序")
        self.assertIn("default", names)
        self.assertIn("alice", names)
        self.assertIn("bob", names)


class TestCliInstances(_TmpInstances):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_instances_command(self):
        from unittest import mock

        with mock.patch("utils.daemon.pid_alive", return_value=False):
            rc, out, _ = self._run(["--instances"])
        self.assertEqual(rc, 0)
        self.assertIn("default", out)
        self.assertIn("实例列表", out)

    def test_instance_flag_creates_and_routes(self):
        """`--instance alice --status alice`：--instance 激活 alice（建目录），--status alice 读其 state。"""
        from unittest import mock

        with mock.patch.object(instance, "allocate_port", return_value=46001):
            rc, out, err = self._run(["--instance", "alice", "--status", "alice"])
        self.assertEqual(rc, 3, "alice 未运行，--status alice 应返回 3")
        self.assertTrue(os.path.exists(os.path.join(static.INSTANCES_DIR, "alice", "config", "config.json5")))

    def test_instance_eq_form(self):
        """`--instance=bob --status bob` 等号形式同样生效。"""
        from unittest import mock

        with mock.patch.object(instance, "allocate_port", return_value=46002):
            rc, _out, _err = self._run(["--instance=bob", "--status", "bob"])
        self.assertEqual(rc, 3)
        self.assertTrue(os.path.exists(os.path.join(static.INSTANCES_DIR, "bob", "config", "config.json5")))


class TestRemoveRename(_TmpInstances):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_remove_nonexistent(self):
        rc, _out, err = self._run(["--instance", "ghost", "--remove"])
        self.assertEqual(rc, 1)
        self.assertIn("不存在", err)

    def test_remove_existing(self):
        from unittest import mock

        with mock.patch.object(instance, "allocate_port", return_value=46010):
            instance.ensure_instance("alice")
        rc, out, _ = self._run(["--instance", "alice", "--remove"])
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(static.INSTANCES_DIR, "alice")))

    def test_remove_running_refused(self):
        from unittest import mock

        with mock.patch.object(instance, "allocate_port", return_value=46011):
            instance.ensure_instance("alice")
        with mock.patch("utils.daemon.pid_alive", return_value=True):
            # 写一个 pid 到 alice 的 state，模拟运行中
            state_file = os.path.join(static.INSTANCES_DIR, "alice", "run", "steamauto.state.json")
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            with open(state_file, "w", encoding="utf-8") as f:
                f.write('{"pid": 12345}')
            rc, _out, err = self._run(["--instance", "alice", "--remove"])
        self.assertEqual(rc, 1)
        self.assertIn("正在运行", err)

    def test_rename_success(self):
        from unittest import mock

        with mock.patch.object(instance, "allocate_port", return_value=46012):
            instance.ensure_instance("alice")
        rc, out, _ = self._run(["--instance", "alice", "--rename", "bob"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(static.INSTANCES_DIR, "bob")))
        self.assertFalse(os.path.exists(os.path.join(static.INSTANCES_DIR, "alice")))

    def test_rename_target_exists(self):
        from unittest import mock

        with mock.patch.object(instance, "allocate_port", return_value=46013):
            instance.ensure_instance("alice")
            instance.ensure_instance("bob")
        rc, _out, err = self._run(["--instance", "alice", "--rename", "bob"])
        self.assertEqual(rc, 1)
        self.assertIn("已存在", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
