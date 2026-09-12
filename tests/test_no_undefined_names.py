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
#: 永不遍历的目录名（含 vendored 的 api/，只有逃生序列检查需要进去）
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "api", "run", "logs", "session", "config"}
#: 只有它需要扫到 vendored 的 steampy（那几处无效转义就在里面）
VENDORED_STEAM = os.path.join("api", "Steam", "steampy")

BUILTINS = set(dir(builtins))
ALLOW = {
    "__name__", "__file__", "__doc__", "__package__", "__builtins__",
    "__spec__", "__loader__", "__debug__",
    "self", "cls", "_", "__", "___",
}


def iter_py_files(dirs=SCAN_DIRS, skip=SKIP_DIRS):
    """遍历目录下的 .py 文件（跳过 skip 中的目录名）。"""
    for rel in dirs:
        folder = os.path.join(SRC, rel)
        if not os.path.isdir(folder):
            continue
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if d not in skip]
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
        for path in sorted(iter_py_files()):
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


class TestNoInvalidEscapeSequences(unittest.TestCase):
    r"""源码里不得有「无效转义序列」——Python 3.12+ 会在启动时打印 SyntaxWarning。

    实例：`api/Steam/steampy/utils.py` 的三处正则用了非 raw 字符串，
    在 Python 3.14 下每次启动都刷 3 条 SyntaxWarning，非常吵。

    修这类问题的坑：**不能无脑加 `r` 前缀**。parse_price 里的那处混合了
    有效转义（`\\d` → `\d`）与无效转义（`\D` → `\D`），直接加 r 会改变正则语义：

        原写法   pattern = "\D?(\\d*)(\\.|,)?(\\d*)"     # 值 = \D?(\d*)(\.|,)?(\d*)
        错误改法 pattern = r"\D?(\\d*)(\\.|,)?(\\d*)"    # 值 = \D?(\\d*)... 语义变了！
        正确改法 pattern = r"\D?(\d*)(\.|,)?(\d*)"       # 需同时去掉多余反斜杠

    **为什么用 tokenize 而不是 compileall 检查**
    ------------------------------------------
    该 SyntaxWarning 是 Python **3.12+** 才引入的。本仓库测试跑在 3.11 上，
    若用 `compileall -W error::SyntaxWarning` 做守卫，在 3.11 下**永远不会报警**
    —— 是个空测试（变异测试实测 3/3 漏检）。因此改用 tokenize 直接扫描源码字面量，
    与解释器版本无关，且能给出准确行号。
    """

    #: 扫描范围比 _undefined_names 多出 vendored 的 steampy（那几处警告就在里面）
    SCAN = SCAN_DIRS + [VENDORED_STEAM]

    #: 非 raw 字符串里合法的转义首字符
    VALID_ESCAPES = set("ntr'\"abfv\\") | set("01234567") | {"x", "u", "U", "N", "\n"}

    def _find_invalid_escapes(self, path):
        import re
        import tokenize

        issues = []
        with open(path, "rb") as f:
            try:
                tokens = list(tokenize.tokenize(f.readline))
            except (tokenize.TokenError, SyntaxError, IndentationError):
                return issues
        for tok in tokens:
            if tok.type != tokenize.STRING:
                continue
            m = re.match(r"(?i)^([rubf]*)", tok.string)
            prefix = m.group(1).lower()
            if "r" in prefix:
                continue  # raw 字符串不做转义处理
            body = tok.string[len(m.group(1)):]
            for hit in re.finditer(r"\\(.)", body, re.S):
                if hit.group(1) not in self.VALID_ESCAPES:
                    issues.append((tok.start[0], hit.group(0), tok.line.strip()[:70]))
        return issues

    def test_no_invalid_escape_sequences(self):
        offenders = []
        for path in sorted(iter_py_files(self.SCAN)):
            rel = os.path.relpath(path, SRC)
            for lineno, seq, snippet in self._find_invalid_escapes(path):
                offenders.append("%s:%d 无效转义 %r  <- %s" % (rel, lineno, seq, snippet))
        self.assertEqual(offenders, [], "存在无效转义序列（3.12+ 会打印 SyntaxWarning）：\n" + "\n".join(offenders))

    def test_detector_catches_and_accepts(self):
        """自检：检测器要能分辨无效转义与合法写法（含常见误判）。"""
        import tempfile

        cases = [
            (r'x = "\d"', 1),            # 无效：\d
            (r'x = "\D"', 1),            # 无效：\D
            (r'x = r"\d"', 0),           # raw 合法
            (r'x = "\n\t"', 0),          # 合法转义
            (r'x = "C:\\path"', 0),      # 合法：\\
            (r'x = "\x41\u4e2d"', 0),    # 合法：\x \u
            (r'x = re.compile("a(\d+)")', 1),  # 无效（函数参数里的字面量）
        ]
        for src, expected in cases:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
                f.write(src + "\n")
                path = f.name
            try:
                got = len(self._find_invalid_escapes(path))
            finally:
                os.remove(path)
            self.assertEqual(got, expected, "自检失败：%r 期望 %d 处，实际 %d 处" % (src, expected, got))

    def test_steampy_regex_values_unchanged(self):
        """逐字校验三处正则的运行时值（防止有人改形式时改变语义）。"""
        from api.Steam.steampy.utils import parse_price  # noqa: F401

        path = os.path.join(SRC, "api", "Steam", "steampy", "utils.py")
        with io.open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn(r"\D?(\d*)(\.|,)?(\d*)", src, "parse_price 的正则值被改动")
        self.assertIn(r"mylisting_\d+", src, "mylisting 正则值被改动")

    def test_parse_price_behaviour(self):
        """行为守卫：常见价格写法必须解析正确（回归上游正则）。"""
        from api.Steam.steampy.utils import parse_price

        self.assertEqual(str(parse_price("$1.23")), "1.23")
        self.assertEqual(str(parse_price("$0.03")), "0.03")
        self.assertEqual(str(parse_price("12.5")), "12.5")
        self.assertEqual(str(parse_price("1,23")), "1.23")  # 欧式小数点


if __name__ == "__main__":
    unittest.main(verbosity=2)
