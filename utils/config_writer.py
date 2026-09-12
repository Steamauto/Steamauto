"""配置文件定点编辑：改值/加键/删键，**保留原有注释、缩进与行尾**。

为什么不用 json5 回写：`json5` 库只能读不能写，而 `config.json5` 里全是注释与
注释掉的说明，任何「整体 dump 重写」都会把注释全部丢掉。

实现思路（D5.A）：不重新序列化整个文档，而是
1. 用一套轻量扫描器定位目标 `"key": value` 在原文中的**字符区间**；
2. 只替换区间内的值文本，其余字节原样保留。

支持的形态：嵌套对象、对象数组（路径里用数字下标，如 `a.list.0.name`）、
字符串/数字/布尔/null/数组/对象字面量。
已知边界（有意不支持）：无法跨作用域搬移键；`unset` 对「同一行内多个成员」的
紧凑写法可能留下多余空白（值仍可正常解析）。
"""

import json
import os
import re

import json5

# 裸值（非字符串/非括号）的字面量匹配
_BARE_VALUE_RE = re.compile(
    r"(?:"
    r"true|false|null|Infinity|NaN|[-+]?Infinity"
    r"|[-+]?(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)"
    r"|[A-Za-z_$][\w$]*"  # JSON5 允许裸标识符作值（少用，容错处理）
    r")"
)
_IDENT_RE = re.compile(r"[\w$]+")  # JSON5 允许无引号键，含 Unicode 字符


class ConfigEditError(Exception):
    """配置编辑失败。"""


# ------------------------------------------------------------------ 扫描原语

def _skip_ws_comments(text, i):
    """从 i 起跳过空白与 //、/* */ 注释。"""
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
        else:
            break
    return i


def _read_string(text, i):
    """读取一个字符串字面量，返回 (原文, 结束位置-不含)。"""
    quote = text[i]
    j = i + 1
    n = len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == quote:
            return text[i:j + 1], j + 1
        j += 1
    raise ConfigEditError("字符串未闭合")


def _unquote(raw):
    """把带引号的键转成纯文本。"""
    if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
        try:
            return json5.loads(raw)
        except Exception:
            return raw[1:-1]
    return raw


def _match_bracket(text, i, open_ch, close_ch):
    """返回与 text[i] 处 open_ch 匹配的 close_ch 之后的位置。"""
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            _, i = _read_string(text, i)
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ConfigEditError("括号未闭合")


def _read_value_span(text, i):
    """读取从 i（可含前导空白/注释）开始的值，返回 (start, end)。"""
    i = _skip_ws_comments(text, i)
    if i >= len(text):
        raise ConfigEditError("缺少值")
    c = text[i]
    if c in "\"'":
        _, end = _read_string(text, i)
        return i, end
    if c == "{":
        return i, _match_bracket(text, i, "{", "}")
    if c == "[":
        return i, _match_bracket(text, i, "[", "]")
    m = _BARE_VALUE_RE.match(text, i)
    if not m:
        raise ConfigEditError("无法解析的值，位置 %d 附近: %r" % (i, text[i:i + 20]))
    return i, m.end()


def _iter_object_members(text, obj_start, obj_end):
    """遍历对象的顶层成员，yield (key_start, key, value_start, value_end)。"""
    i = obj_start + 1
    limit = obj_end
    while True:
        i = _skip_ws_comments(text, i)
        while i < limit and text[i] == ",":
            i += 1
            i = _skip_ws_comments(text, i)
        if i >= limit or text[i] == "}":
            return
        key_start = i
        if text[i] in "\"'":
            raw, i = _read_string(text, i)
            key = _unquote(raw)
        else:
            m = _IDENT_RE.match(text, i)
            if not m:
                # 宁可显式报错，也不要静默错插导致配置被写坏
                raise ConfigEditError("无法解析键名，位置 %d 附近: %r" % (i, text[i:i + 20]))
            key = m.group(0)
            i = m.end()
        i = _skip_ws_comments(text, i)
        if i >= limit or text[i] != ":":
            raise ConfigEditError("键 %r 后缺少冒号，位置 %d 附近: %r" % (key, i, text[i:i + 20]))
        i += 1
        v_start, v_end = _read_value_span(text, i)
        yield key_start, key, v_start, v_end
        i = v_end


def _iter_array_items(text, arr_start, arr_end):
    """遍历数组元素，yield (index, value_start, value_end)。"""
    i = arr_start + 1
    idx = 0
    while True:
        i = _skip_ws_comments(text, i)
        while i < arr_end and text[i] == ",":
            i += 1
            i = _skip_ws_comments(text, i)
        if i >= arr_end or text[i] == "]":
            return
        v_start, v_end = _read_value_span(text, i)
        yield idx, v_start, v_end
        idx += 1
        i = v_end


def _line_indent(text, pos):
    """返回 pos 所在行的缩进前缀。"""
    line_start = text.rfind("\n", 0, pos) + 1
    m = re.match(r"[ \t]*", text[line_start:pos])
    return m.group(0) if m else ""


def split_path(key):
    """把 'a.b.0.c' 拆成 ['a','b','0','c']。"""
    if isinstance(key, (list, tuple)):
        return [str(p) for p in key]
    return [p for p in str(key).split(".") if p != ""]


# ------------------------------------------------------------------ 定位

def find_span(text, path):
    """定位路径对应的值区间。

    :return: (key_start, value_start, value_end)；未找到返回 None。
    """
    root = _skip_ws_comments(text, 0)
    if root >= len(text) or text[root] != "{":
        raise ConfigEditError("配置文件根节点不是对象")
    scope_start, scope_end = root, _match_bracket(text, root, "{", "}")
    for depth, part in enumerate(path):
        if text[scope_start] == "{":
            hit = None
            for key_start, key, v_start, v_end in _iter_object_members(text, scope_start, scope_end):
                if key == part:
                    hit = (key_start, v_start, v_end)
                    break
        elif text[scope_start] == "[":
            try:
                want = int(part)
            except ValueError:
                return None
            hit = None
            for idx, v_start, v_end in _iter_array_items(text, scope_start, scope_end):
                if idx == want:
                    hit = (v_start, v_start, v_end)
                    break
        else:
            return None
        if hit is None:
            return None
        if depth == len(path) - 1:
            return hit
        scope_start, scope_end = hit[1], hit[2]
    return None


# ------------------------------------------------------------------ 编解码

def encode_value(value) -> str:
    """把 Python 值编码成 JSON5 字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return repr(value)
        return repr(value)
    return json.dumps(value, ensure_ascii=False)


def coerce_value(raw):
    """把 CLI 传入的字符串按 JSON5 字面量解析；解析失败则按普通字符串处理。

    这样 `true`/`14`/`[1,2]` 会得到布尔/整数/数组，`info` 这类裸词得到字符串。
    """
    if not isinstance(raw, str):
        return raw
    try:
        return json5.loads(raw)
    except Exception:
        return raw


# ------------------------------------------------------------------ 编辑操作

def set_value_in_text(text, path, literal):
    """在文本里设置值。返回 (new_text, created)。created=True 表示新增了键。

    父级对象缺失时会自动逐级创建（例如旧配置文件没有 `console_echo`，
    执行 `console_echo.enable=false` 会自动补出该对象）。
    """
    span = find_span(text, path)
    if span is not None:
        _, v_start, v_end = span
        if text[v_start:v_end] == literal:
            return text, False
        return text[:v_start] + literal + text[v_end:], False
    for depth in range(1, len(path)):
        sub = path[:depth]
        if find_span(text, sub) is None:
            text = _insert_key(text, sub, "{}")
    return _insert_key(text, path, literal), True

def _resolve_object_scope(text, path):
    """定位某个对象/数组路径的区间；path 为空表示根对象。"""
    if not path:
        root = _skip_ws_comments(text, 0)
        return root, _match_bracket(text, root, "{", "}")
    span = find_span(text, path)
    if span is None:
        raise ConfigEditError("父级配置不存在：%s" % ".".join(path))
    _, v_start, v_end = span
    if text[v_start] not in "{[":
        raise ConfigEditError("父级不是对象/数组，无法在其下新增：%s" % ".".join(path))
    return v_start, v_end


def _insert_key(text, path, literal):
    """在父对象末尾插入新键（保留原缩进与换行风格）。返回新文本。"""
    if not path:
        raise ConfigEditError("路径为空")
    parent = path[:-1]
    key = path[-1]
    scope_start, scope_end = _resolve_object_scope(text, parent)
    if text[scope_start] != "{":
        raise ConfigEditError("父级不是对象，无法新增键：%s" % ".".join(parent))
    close_idx = scope_end - 1
    base_indent = _line_indent(text, scope_start)
    child_indent = base_indent + "  "
    nl = "\r\n" if "\r\n" in text else "\n"
    members = list(_iter_object_members(text, scope_start, scope_end))
    entry = '"%s": %s' % (key, literal)

    if not members:
        insert_at = scope_start + 1
        insert_text = nl + child_indent + entry + nl + base_indent
        return text[:insert_at] + insert_text + text[insert_at:]

    last_value_end = members[-1][3]
    probe = _skip_ws_comments(text, last_value_end)
    if probe < close_idx and text[probe] == ",":
        insert_at = probe + 1
        insert_text = nl + child_indent + entry
    else:
        insert_at = last_value_end
        insert_text = "," + nl + child_indent + entry
    return text[:insert_at] + insert_text + text[insert_at:]


def remove_value_in_text(text, path):
    """删除某个键（及其所在行），返回 (new_text, removed)。"""
    span = find_span(text, path)
    if span is None:
        return text, False
    key_start, _, v_end = span
    line_start = text.rfind("\n", 0, key_start) + 1
    probe = _skip_ws_comments(text, v_end)
    if probe < len(text) and text[probe] == ",":
        end = probe + 1
        while end < len(text) and text[end] not in "\r\n":
            end += 1  # 连带删掉行尾注释
    else:
        # 最后一个成员：连带删除前一个成员的逗号
        prev_comma = text.rfind(",", line_start, key_start)
        if prev_comma != -1:
            line_start = prev_comma
        end = v_end
    return text[:line_start] + text[end:], True


# ------------------------------------------------------------------ 文件级接口

def read_text(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def set_value(path, key, value, literal=None):
    """设置配置文件里的某个键，保留注释与格式。

    写盘前会校验编辑结果仍是合法 JSON5；不合法则抛 ConfigEditError 且**不改动原文件**。

    :return: (changed: bool, created: bool, literal_used: str)
    """
    text = read_text(path)
    lit = literal if literal is not None else encode_value(value)
    new_text, created = set_value_in_text(text, split_path(key), lit)
    if new_text != text:
        validate_text(new_text)
        write_text(path, new_text)
        return True, created, lit
    return False, False, lit


def remove_value(path, key):
    """删除配置文件里的某个键。返回 (changed, removed)。"""
    text = read_text(path)
    new_text, removed = remove_value_in_text(text, split_path(key))
    if new_text != text:
        validate_text(new_text)
        write_text(path, new_text)
        return True, True
    return False, False


def load_config(path):
    """读取配置文件为字典（读用 json5，容忍注释/尾逗号）。"""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json5.load(f)
    return data if isinstance(data, dict) else {}


def get_value(cfg, key):
    """从字典按点分路径取值，返回 (found, value)。"""
    cur = cfg
    for part in split_path(key):
        if isinstance(cur, dict):
            if part not in cur:
                return False, None
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, cur


def flatten(cfg, prefix="", expand_arrays=False):
    """把配置字典摊平成 [(点分键, 值)]。

    :param expand_arrays: True 时把数组元素也展开成 `key.0` / `key.1`；
                          False（默认）时数组**整体作为一个值**返回。
    默认不展开是为了展示友好：`filter_name` / `blacklist_words` 这类数组应显示成
    `["A", "B"]`，而不是拆成一堆 `key.0 = A`、`key.1 = B` 的条目。
    需要枚举「所有可寻址键」（含数组下标）时传 True。
    """
    out = []
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            out.extend(flatten(v, "%s.%s" % (prefix, k) if prefix else str(k), expand_arrays))
    elif isinstance(cfg, list) and expand_arrays:
        for i, v in enumerate(cfg):
            out.extend(flatten(v, "%s.%s" % (prefix, i) if prefix else str(i), expand_arrays))
    else:
        out.append((prefix, cfg))
    return out


def validate_text(text):
    """校验编辑后的文本仍是合法 JSON5；不合法则抛异常。"""
    try:
        json5.loads(text)
    except Exception as e:
        raise ConfigEditError("编辑后配置不是合法 JSON5：%s" % (e,))
    return True
