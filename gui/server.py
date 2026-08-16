"""Steamauto GUI 的 Flask 服务（本地 Web 界面）。"""
import os

from flask import Flask, jsonify, render_template, request

from . import config_editor, config_schema, login, runner

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    log_file = runner.latest_log_file()
    return jsonify(
        {
            "running": runner.is_running(),
            "pid": runner.get_pid(),
            "log_file": os.path.basename(log_file) if log_file else None,
        }
    )


@app.route("/api/start", methods=["POST"])
def api_start():
    ok, msg = runner.start()
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    ok, msg = runner.stop()
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/logs")
def api_logs():
    tail = request.args.get("tail", type=int)
    flush = request.args.get("flush") == "1"
    lines, name = runner.read_logs(tail=tail, flush=flush)
    return jsonify({"lines": lines, "file": name})


@app.route("/api/config")
def api_config():
    config_text = config_editor.read_text(config_editor.CONFIG_FILE_PATH)
    account = config_editor.load_json5(config_editor.ACCOUNT_FILE_PATH)
    account_text = config_editor.read_text(config_editor.ACCOUNT_FILE_PATH)
    return jsonify(
        {
            "config_text": config_text or "",
            "account": account if isinstance(account, dict) else {},
            "account_text": account_text or "",
        }
    )


@app.route("/api/config/save", methods=["POST"])
def api_config_save():
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    ok, msg = config_editor.save_text(config_editor.CONFIG_FILE_PATH, content)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/account/save", methods=["POST"])
def api_account_save():
    data = request.get_json(silent=True) or {}
    account = {
        "shared_secret": data.get("shared_secret", ""),
        "identity_secret": data.get("identity_secret", ""),
        "steam_username": data.get("steam_username", ""),
        "steam_password": data.get("steam_password", ""),
    }
    config_editor.save_json5(config_editor.ACCOUNT_FILE_PATH, account)
    return jsonify({"ok": True, "msg": "保存成功"})


@app.route("/api/account/reset", methods=["POST"])
def api_account_reset():
    config_editor.save_json5(config_editor.ACCOUNT_FILE_PATH, config_editor.ACCOUNT_DEFAULT)
    return jsonify({"ok": True, "msg": "已恢复默认值", "account": dict(config_editor.ACCOUNT_DEFAULT)})


@app.route("/api/account/export")
def api_account_export():
    content = config_editor.read_text(config_editor.ACCOUNT_FILE_PATH) or ""
    return jsonify({"ok": True, "content": content, "filename": "steam_account_info.json5"})


@app.route("/api/account/import", methods=["POST"])
def api_account_import():
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    ok, value = config_editor.validate_json5(content)
    if not ok:
        return jsonify({"ok": False, "msg": "JSON5 语法错误：" + str(value)})
    if not isinstance(value, dict):
        return jsonify({"ok": False, "msg": "内容应为 JSON 对象"})
    config_editor.save_text(config_editor.ACCOUNT_FILE_PATH, content)
    return jsonify({"ok": True, "msg": "导入成功", "account": value})


@app.route("/api/login/status")
def api_login_status():
    return jsonify(login.get_state())


@app.route("/api/login/start", methods=["POST"])
def api_login_start():
    data = request.get_json(silent=True) or {}
    platform = data.get("platform", "")
    if platform not in ("steam", "buff", "uu"):
        return jsonify({"ok": False, "msg": "未知平台"})
    try:
        login.start_login(platform)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "msg": str(e)})
    return jsonify({"ok": True, "msg": "已开始登录"})


@app.route("/api/login/interact")
def api_login_interact():
    req = login.bridge.peek_request()
    return jsonify({"request": req})


@app.route("/api/login/respond", methods=["POST"])
def api_login_respond():
    data = request.get_json(silent=True) or {}
    value = data.get("value", "")
    login.bridge.respond(value)
    return jsonify({"ok": True})


@app.route("/api/login/qrcode")
def api_login_qrcode():
    url = request.args.get("url", "")
    if not url:
        return jsonify({"ok": False})
    import base64
    import io

    import qrcode

    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return jsonify({"ok": True, "image": "data:image/png;base64," + b64})


@app.route("/api/config/table")
def api_config_table():
    config = config_editor.load_json5(config_editor.CONFIG_FILE_PATH) or {}
    return jsonify({"groups": config_schema.get_table_data(config)})


@app.route("/api/config/table/save", methods=["POST"])
def api_config_table_save():
    data = request.get_json(silent=True) or {}
    values = data.get("values", {})
    config = config_editor.load_json5(config_editor.CONFIG_FILE_PATH) or {}
    ok, result = config_schema.save_from_table(config, values)
    if not ok:
        return jsonify({"ok": False, "msg": result})
    config_editor.save_json5(config_editor.CONFIG_FILE_PATH, result)
    return jsonify({"ok": True, "msg": "保存成功"})
