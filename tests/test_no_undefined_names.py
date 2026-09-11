"""静态回归：防止「使用了名字却没导入」这类只在运行时才炸的 bug。

起因（2026-09-12 实际事故）：重构把 `logger.info(...)` 改成 `echo(...)` 时，
某些文件的导入行写法不同（如 `from utils.logger import LogFilter, PluginLogger,
handle_caught_exception`），条件式替换被静默跳过 → 运行时 `NameError: name 'echo'
is not defined`，插件初始化失败、程序直接退出。而当时 pytest 100 passed ——
因为测试从不执行插件初始化那条路径。

两层防护：
1. 本文件：AST 静态检查「裸名字未定义」（不依赖执行路径，代价极低）。
2. tests/test_architecture.py::test_all_plugins_importable：
   真实 import 每个插件，覆盖「导入的名字在源模块不存在」（ImportError）这类问题。
"""

import ast
import builtins
import io
import os
import sys
import unittest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# 被检查的目录（跳过 vendored 第三方代码与运行期产物）
SCAN_DIRS = ["utils", "plugins", "tests", ""]
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "api", "run", "logs", "session", "config"}

BUILTINS = set(dir(builtins))
ALLOW = {
    "__name__", "__file__", "__doc__", "__package__", "__builtins__",
    "__spec__", "__loader__", "__debug__",
    "self", "cls", "_", "__", "___",
}


def _iter_py_files():
    for rel in SCAN_DIRS:
        folder = os.path.join(SRC, rel)
        if not os.path.isdir(folder):
            continue
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def _defined_names(tree):
    """收集模块内**任意层级**的定义名与导入名（保守并集：宁可漏报，不要误报）。"""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    names.add("*")  # 星号导入无法静态确定，整文件跳过
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            args = getattr(node, "args", None)
            if args is not None:
                for arg in list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs):
                    names.add(arg.arg)
                if args.vararg:
                    names.add(args.vararg.arg)
                if args.kwarg:
                    names.add(args.kwarg.arg)
        elif isinstance(node, ast.Lambda):
            for arg in list(node.args.args) + list(node.args.posonlyargs) + list(node.args.kwonlyargs):
                names.add(arg.arg)
            if node.args.vararg:
                names.add(node.args.vararg.arg)
            if node.args.kwarg:
                names.add(node.args.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
    return names


def find_undefined_names(path):
    """返回 [(行号, 名字)]，为空表示该模块所有裸名都有定义。"""
    with io.open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    have = _defined_names(tree)
    if "*" in have:
        return []
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in have or node.id in BUILTINS or node.id in ALLOW:
                continue
            problems.append((node.lineno, node.id))
    return problems


class TestNoUndefinedNames(unittest.TestCase):
    def test_no_undefined_bare_names(self):
        """全仓（自有代码）不应存在「用了但没导入」的裸名字。"""
        offenders = []
        for path in sorted(_iter_py_files()):
            rel = os.path.relpath(path, SRC)
            for lineno, name in find_undefined_names(path):
                offenders.append("%s:%d 未定义的名字 %r" % (rel, lineno, name))
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_checker_detects_missing_import(self):
        """自检：检查器本身要能识别「调用 echo 但未导入」这种形态。"""
        import tempfile

        broken = "def f():\n    echo('hi')\n"
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(broken)
            path = f.name
        try:
            found = [n for _, n in find_undefined_names(path)]
            self.assertIn("echo", found)
        finally:
            os.remove(path)

    def test_checker_accepts_imported_echo(self):
        """自检：正常导入时不应误报。"""
        import tempfile

        ok = "from utils.logger import echo\n\n\ndef f():\n    echo('hi')\n"
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(ok)
            path = f.name
        try:
            self.assertEqual(find_undefined_names(path), [])
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
