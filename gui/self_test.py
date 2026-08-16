"""Steamauto GUI 自测入口（ad-hoc 验证，非正式测试套件）。

用法（在项目根目录）：
    python -m gui.self_test

覆盖：登录路由、状态 API、交互桥接(input/qrcode/ANSI)、monkeypatch 目标、
首页渲染、app.js 语法。不触发真实 Steam/BUFF/悠悠有品 网络请求。
"""
import builtins
import os
import shutil
import subprocess
import sys
import threading
import tempfile
import time

from . import config_editor, login, server

# 确保项目根在 sys.path，以便 import Steamauto 核心模块做 monkeypatch 目标验证
if config_editor.PROJECT_ROOT not in sys.path:
    sys.path.insert(0, config_editor.PROJECT_ROOT)


def _run():
    checks = []
    orig_cwd = os.getcwd()

    def check(name, cond):
        checks.append((name, bool(cond)))
        print(("PASS  " if cond else "FAIL  ") + name)

    # ---- 路由 + API ----
    routes = {str(r) for r in server.app.url_map.iter_rules()}
    for rt in ["/api/login/status", "/api/login/start", "/api/login/interact", "/api/login/respond", "/api/login/qrcode", "/api/config/table", "/api/config/table/save", "/api/account/reset", "/api/account/export", "/api/account/import"]:
        check("路由 " + rt, rt in routes)

    c = server.app.test_client()
    r = c.get("/api/login/status")
    d = r.get_json()
    check("status 200 且三平台", r.status_code == 200 and set(d) == {"steam", "buff", "uu"})
    check("初始 idle", all(d[p]["status"] == "idle" for p in d))
    check("未知平台拦截", c.post("/api/login/start", json={"platform": "hack"}).get_json()["ok"] is False)
    r = c.get("/")
    check("首页含平台登录tab", r.status_code == 200 and "平台登录" in r.get_data(as_text=True))

    # ---- bridge input ----
    r1 = []
    t1 = threading.Thread(target=lambda: r1.append(login.bridge.ask_input("请输入验证码")))
    t1.start()
    time.sleep(0.3)
    req = login.bridge.peek_request()
    check("input 请求", req is not None and req["type"] == "input" and req["prompt"] == "请输入验证码")
    login.bridge.respond("123456")
    t1.join(timeout=2)
    check("input 响应回传", r1 and r1[0] == "123456")

    # ---- bridge qrcode ----
    login.bridge.show_qrcode("https://buff.example.com/qr")
    req = login.bridge.peek_request()
    check("qrcode 请求", req is not None and req["type"] == "qrcode" and req["url"].startswith("https://"))
    login.bridge.clear()
    check("qrcode clear", login.bridge.peek_request() is None)

    # ---- ANSI 清理 ----
    r2 = []
    t2 = threading.Thread(target=lambda: r2.append(login.bridge.ask_input("\x1b[1;31m请输入手机号\x1b[0m")))
    t2.start()
    time.sleep(0.3)
    check("ANSI 清理", login.bridge.peek_request()["prompt"] == "请输入手机号")
    login.bridge.respond("13800138000")
    t2.join(timeout=2)

    # ---- 交互 API 全链路 ----
    r3 = []
    t3 = threading.Thread(target=lambda: r3.append(login.bridge.ask_input("API输入")))
    t3.start()
    time.sleep(0.3)
    check("interact 轮询", c.get("/api/login/interact").get_json()["request"]["prompt"] == "API输入")
    c.post("/api/login/respond", json={"value": "abc"})
    t3.join(timeout=2)
    check("respond 回传", r3 and r3[0] == "abc")

    # ---- monkeypatch 目标（temp cwd 隔离，避免污染项目） ----
    tmp = tempfile.mkdtemp(prefix="hermes-verify-")
    os.chdir(tmp)
    try:
        from utils import buff_helper, steam_client as sc  # noqa: E402
        from utils import uu_helper  # noqa: E402

        check("steam_client.pause 可调用", hasattr(sc, "pause") and callable(sc.pause))
        check("qrcode_terminal.draw 存在", hasattr(buff_helper.qrcode_terminal, "draw"))
        check("get_valid_session_for_buff 存在", hasattr(buff_helper, "get_valid_session_for_buff"))
        check("get_valid_token_for_uu 存在", hasattr(uu_helper, "get_valid_token_for_uu"))
        check("builtins.input 存在", hasattr(builtins, "input"))
        orig = sc.pause
        sc.pause = lambda *a, **k: None
        check("sc.pause 可 patch", sc.pause() is None)
        sc.pause = orig
        orig_d = buff_helper.qrcode_terminal.draw
        buff_helper.qrcode_terminal.draw = lambda u: u
        check("qrcode draw 可 patch", buff_helper.qrcode_terminal.draw("x") == "x")
        buff_helper.qrcode_terminal.draw = orig_d
    finally:
        os.chdir(orig_cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 配置表格（config_schema） ----
    from . import config_schema

    sample = {"buff_auto_accept_offer": {"enable": True, "interval": 600}}
    groups = config_schema.get_table_data(sample)
    check("表格数据生成", isinstance(groups, list) and len(groups) > 0)
    flat = {}
    for g in groups:
        for f in g["fields"]:
            flat[f["key"]] = f
    check("当前值读取", flat["buff_auto_accept_offer.enable"]["value"] is True)
    check("缺失字段用默认值", flat["buff_auto_accept_offer.dota2_support"]["value"] is False)

    check("bool 转换", config_schema.convert_value("bool", "true") is True)
    check("int 转换", config_schema.convert_value("int", "42") == 42)
    check("float 转换", config_schema.convert_value("float", "0.5") == 0.5)
    check("array 转换", config_schema.convert_value("array", '["a","b"]') == ["a", "b"])

    ok, result = config_schema.save_from_table({"buff_auto_accept_offer": {"enable": False}}, {"buff_auto_accept_offer.interval": "999"})
    check("save 转换并写回", ok and result["buff_auto_accept_offer"]["interval"] == 999)
    check("save 保留已有值", result["buff_auto_accept_offer"]["enable"] is False)
    check("save 补齐默认值", result["buff_auto_accept_offer"]["dota2_support"] is False)
    ok2, _ = config_schema.save_from_table({}, {"log_retention_days": "abc"})
    check("无效类型拦截", ok2 is False)

    # ---- 表格 API 端到端（临时文件，避免污染真实 config） ----
    import json5 as _j5

    _tmp_cfg = tempfile.mkdtemp(prefix="hermes-verify-")
    _orig_cfg_path = config_editor.CONFIG_FILE_PATH
    config_editor.CONFIG_FILE_PATH = os.path.join(_tmp_cfg, "config", "config.json5")
    try:
        r = c.get("/api/config/table")
        d = r.get_json()
        check("table API 返回 groups", r.status_code == 200 and isinstance(d.get("groups"), list) and len(d["groups"]) > 0)
        all_keys = set()
        for g in d["groups"]:
            for f in g["fields"]:
                all_keys.add(f["key"])
        check("schema 覆盖关键字段", {"buff_auto_accept_offer.enable", "log_level", "ecosteam.qps"}.issubset(all_keys))

        r = c.post("/api/config/table/save", json={"values": {"buff_auto_accept_offer.interval": "600", "log_level": "info"}})
        check("save API 成功", r.get_json().get("ok") is True)
        with open(config_editor.CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
            saved = _j5.load(f)
        check("文件写入 interval", saved["buff_auto_accept_offer"]["interval"] == 600)
        check("文件写入 log_level", saved["log_level"] == "info")
        check("文件补齐默认值", saved["buff_auto_accept_offer"]["enable"] is True)
        r = c.post("/api/config/table/save", json={"values": {"log_retention_days": "abc"}})
        check("save 无效拦截", r.get_json().get("ok") is False)
    finally:
        config_editor.CONFIG_FILE_PATH = _orig_cfg_path
        shutil.rmtree(_tmp_cfg, ignore_errors=True)

    # ---- 账号信息 API（reset/import/export） ----
    _tmp_acc = tempfile.mkdtemp(prefix="hermes-verify-")
    _orig_acc_path = config_editor.ACCOUNT_FILE_PATH
    config_editor.ACCOUNT_FILE_PATH = os.path.join(_tmp_acc, "config", "steam_account_info.json5")
    try:
        r = c.post("/api/account/reset")
        check("reset 成功", r.get_json().get("ok") is True)
        check("reset 后为空账号", r.get_json().get("account", {}).get("steam_username") == "")

        r = c.post("/api/account/import", json={"content": '{\n  // 备份注释\n  "steam_username": "test_user",\n  "steam_password": "pass",\n  "shared_secret": "SS",\n  "identity_secret": "IS",\n}'})
        check("import 成功", r.get_json().get("ok") is True)
        check("import 账号值", r.get_json().get("account", {}).get("steam_username") == "test_user")

        r = c.get("/api/account/export")
        d = r.get_json()
        check("export 返回内容", d.get("ok") is True and "test_user" in d.get("content", ""))

        r = c.post("/api/account/import", json={"content": "{ 无效"})
        check("import 无效拦截", r.get_json().get("ok") is False)
        r = c.post("/api/account/import", json={"content": "[1,2,3]"})
        check("import 非对象拦截", r.get_json().get("ok") is False)
    finally:
        config_editor.ACCOUNT_FILE_PATH = _orig_acc_path
        shutil.rmtree(_tmp_acc, ignore_errors=True)

    # ---- app.js 语法 ----
    try:
        rjs = subprocess.run(["node", "--check", os.path.join(config_editor.PROJECT_ROOT, "gui", "static", "app.js")], capture_output=True, text=True)
        check("app.js 语法", rjs.returncode == 0)
    except Exception:  # noqa: BLE001
        check("app.js 语法(node不可用跳过)", True)

    # ---- 汇总 ----
    failed = [n for n, ok in checks if not ok]
    print("\n===== %d/%d 通过 =====" % (len(checks) - len(failed), len(checks)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    _run()
