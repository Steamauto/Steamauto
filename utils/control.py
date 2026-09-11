"""本机回环控制通道（D1.A）。

运行中的 Steamauto 会在 127.0.0.1 上监听一个 TCP 端口，CLI（`config`/`status`/
`stop` 等子命令）通过它向进程发指令。设计要点：

- **只绑回环**：外部主机不可达，避免局域网内被改配置。
- **token 鉴权**：token 存在 `run/control_token.txt`（首次自动生成），
  防止本机其它程序误发指令。
- **行分隔 JSON**：请求/响应各为一行 JSON，简单可靠、便于调试。

协议：
    请求  {"token": "...", "cmd": "...", "args": {...}}\\n
    响应  {"ok": true, "data": {...}}\\n 或 {"ok": false, "error": "..."}\\n
"""

import json
import os
import secrets
import socket
import threading

from utils import static

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 45917
MAX_LINE = 1 << 16  # 64KB，单条消息上限
ACCEPT_TIMEOUT = 0.5  # accept 超时，便于 stop() 及时退出


def ensure_token() -> str:
    """读取控制通道 token；不存在则生成并落盘。"""
    path = static.CONTROL_TOKEN_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    token = secrets.token_urlsafe(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(token)
    try:
        os.chmod(path, 0o600)  # POSIX 生效；Windows 上是 no-op
    except OSError:
        pass
    return token


class ControlServer:
    """回环控制服务端。handlers: {命令名: 处理函数(args: dict) -> dict}。"""

    def __init__(self, handlers, host=DEFAULT_HOST, port=DEFAULT_PORT, token=None, logger=None):
        self.handlers = dict(handlers or {})
        self.host = host
        self.port = int(port)
        self.token = token or ensure_token()
        self.logger = logger
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self.bound_port = None

    # ---- 生命周期 ----
    def start(self, timeout=5.0):
        """启动监听线程，返回 (ok, error)。"""
        try:
            self._sock = self._bind()
        except OSError as e:
            return False, "控制通道启动失败（端口 %s 不可用）：%s" % (self.port, e)
        self.bound_port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, name="control-server", daemon=True)
        self._thread.start()
        return True, ""

    def _bind(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(8)
        sock.settimeout(ACCEPT_TIMEOUT)
        return sock

    def stop(self):
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # ---- 服务循环 ----
    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle_conn(conn)
            except Exception:  # noqa: BLE001 - 单连接异常不应终止服务
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _handle_conn(self, conn):
        conn.settimeout(5.0)
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
            if len(buf) > MAX_LINE:
                self._reply(conn, False, error="请求过大")
                return
        line = buf.split(b"\n", 1)[0]
        try:
            req = json.loads(line.decode("utf-8"))
        except Exception:
            self._reply(conn, False, error="请求不是合法 JSON")
            return
        if not isinstance(req, dict):
            self._reply(conn, False, error="请求必须是 JSON 对象")
            return
        if not secrets.compare_digest(str(req.get("token", "")), self.token):
            self._reply(conn, False, error="token 校验失败")
            return
        cmd = req.get("cmd")
        args = req.get("args") or {}
        handler = self.handlers.get(cmd)
        if handler is None:
            self._reply(conn, False, error="未知命令: %s，可用命令: %s" % (cmd, ", ".join(sorted(self.handlers))))
            return
        try:
            data = handler(args)
        except Exception as e:  # noqa: BLE001 - 把异常回给调用方
            self._reply(conn, False, error="执行 %s 失败: %s" % (cmd, e))
            return
        self._reply(conn, True, data=data if isinstance(data, dict) else {"result": data})

    @staticmethod
    def _reply(conn, ok, data=None, error=None):
        payload = {"ok": bool(ok)}
        if ok:
            payload["data"] = data or {}
        else:
            payload["error"] = error or "未知错误"
        try:
            conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass


def request(cmd, args=None, host=DEFAULT_HOST, port=DEFAULT_PORT, token=None, timeout=8.0):
    """向运行中的进程发一条控制指令。

    :return: (ok: bool, data_or_error) —— ok=True 时第二项为响应数据(dict)，
             ok=False 时第二项为错误字符串。
    """
    token = token if token is not None else ensure_token()
    payload = json.dumps({"token": token, "cmd": cmd, "args": args or {}}, ensure_ascii=False).encode("utf-8") + b"\n"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, int(port)))
        sock.sendall(payload)
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_LINE:
                return False, "响应过大"
    except ConnectionRefusedError:
        return False, "无法连接控制通道（端口 %s）：进程可能未运行或未启用 control" % port
    except socket.timeout:
        return False, "控制通道响应超时"
    except OSError as e:
        return False, "控制通道通信失败：%s" % (e,)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    if not buf:
        return False, "控制通道返回空响应"
    try:
        resp = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    except Exception:
        return False, "控制通道响应不是合法 JSON"
    if resp.get("ok"):
        return True, resp.get("data") or {}
    return False, resp.get("error") or "未知错误"


def ping(port, host=DEFAULT_HOST, token=None, timeout=3.0):
    """探测控制通道是否活着，返回 (ok, data_or_error)。"""
    return request("ping", port=port, host=host, token=token, timeout=timeout)
