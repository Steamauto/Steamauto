"""Steamauto GUI 启动入口（可选入口，不侵入核心代码）。

用法：
    python gui.py [--host 127.0.0.1] [--port 8080] [--no-browser]

启动后浏览器访问 http://127.0.0.1:8080
"""
import argparse
import os
import sys
import threading
import webbrowser

# 保证以 `python gui.py` 方式运行时能导入同目录下的 gui 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.server import app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Steamauto 图形控制台")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    url = "http://%s:%d" % (args.host, args.port)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print("Steamauto GUI 已启动，请访问: " + url)
    print("按 Ctrl+C 退出。")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
