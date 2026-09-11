import datetime
import logging
import os
import platform
import re
import sys

import colorlog
import json5
import requests
from requests.exceptions import ConnectionError, ReadTimeout

import utils.static as static
from api.Steam.steampy.exceptions import ApiException, ConfirmationExpected, EmptyResponse, InvalidCredentials, InvalidResponse, SteamError
from utils.static import BUILD_INFO, CONFIG_FILE_PATH, CURRENT_VERSION, LOGS_FOLDER, STEAM_ERROR_CODES

sensitive_data = []
sensitive_keys = ["ApiKey", "TradeLink", "JoinTime", "NickName", "access_token", "trade_url", "TransactionUrl", "RealName", "IdCard"]

if not os.path.exists(LOGS_FOLDER):
    os.mkdir(LOGS_FOLDER)


class LogFilter(logging.Filter):
    @staticmethod
    def add_sensitive_data(data):
        sensitive_data.append(data)

    def filter(self, record):
        if not isinstance(record.msg, str):
            return True
        for sensitive in sensitive_data:
            record.msg = record.msg.replace(sensitive, "*" * len(sensitive))

        def mask_value(value):
            return "*" * len(value)

        # 处理 JSON 数据中的敏感信息
        for key in sensitive_keys:
            pattern = rf'"{key}"\s*:\s*("(.*?)"|(\d+)|(true|false|null))'

            def replace_match(match):
                if match.group(2):  # 如果匹配到的是带引号的字符串
                    return f'"{key}": "{mask_value(match.group(2))}"'
                elif match.group(3):  # 如果匹配到的是数字
                    return f'"{key}": {mask_value(match.group(3))}'
                elif match.group(4):  # 如果匹配到的是true, false或null
                    return f'"{key}": {mask_value(match.group(4))}'

            record.msg = re.sub(pattern, replace_match, record.msg, flags=re.IGNORECASE)  # type: ignore

        # 处理 URL 参数中的敏感信息
        for key in sensitive_keys:
            pattern = rf"({key}=)([^&\s]+)"

            def replace_url_match(match):
                return f"{match.group(1)}{mask_value(match.group(2))}"

            record.msg = re.sub(pattern, replace_url_match, record.msg, flags=re.IGNORECASE)

        return True


def parse_log_level(value, default=logging.INFO) -> int:
    """把配置里的日志等级（字符串/数字）解析为 logging 级别整数。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if value is None:
        return default
    text = str(value).strip().upper()
    if text.isdigit():
        return int(text)
    return getattr(logging, text, default)


def read_log_settings(cfg) -> dict:
    """从配置字典解析日志双通道设置。

    配置结构：
        "log_level": "info",              // 日志文件级别
        "log_retention_days": 7,
        "console_echo": {
            "enable": true,               // 是否启用控制台回显通道
            "min_level": "warning",       // 非回显记录至少该级别才上控制台
            "dual_write_events": true     // 关键业务事件是否同时写入日志文件
        }
    """
    if not isinstance(cfg, dict):
        cfg = {}
    echo = cfg.get("console_echo")
    if not isinstance(echo, dict):
        echo = {}
    return {
        "level": parse_log_level(cfg.get("log_level", "INFO"), logging.INFO),
        "retention": int(cfg.get("log_retention_days", 7) or 0),
        "echo_enable": bool(echo.get("enable", True)),
        "echo_min_level": parse_log_level(echo.get("min_level", "WARNING"), logging.WARNING),
        "dual_events": bool(echo.get("dual_write_events", True)),
    }


log_retention_days = None
log_level = None
_settings = read_log_settings({})
try:
    with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
        _settings = read_log_settings(json5.loads(f.read()))
except Exception as e:
    pass
log_level = _settings["level"]
log_retention_days = _settings["retention"]
console_echo_enable = _settings["echo_enable"]
console_echo_min_level = _settings["echo_min_level"]
console_dual_write_events = _settings["dual_events"]

def prune_old_logs(retention_days=None):
    """按保留天数删除过期日志文件，返回删除数量。"""
    days = log_retention_days if retention_days is None else retention_days
    if not days:
        return 0
    removed = 0
    for name in os.listdir(LOGS_FOLDER):
        path = os.path.join(LOGS_FOLDER, name)
        if not name.endswith(".log"):
            continue
        age = datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(path))
        if age > datetime.timedelta(days=days):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


prune_old_logs()

logger = logging.getLogger()
logger.setLevel(0)

# 回显通道标记：echo() 输出的记录带 ECHO_FLAG；DUAL_FLAG 表示同时写入日志文件。
ECHO_FLAG = "steamauto_echo"
DUAL_FLAG = "steamauto_echo_dual"
# 终端颜色转义（colorama/colorlog 注入），日志文件不应包含
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_LOG_FMT = "[%(asctime)s] - %(levelname)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def strip_ansi(text):
    """剥掉字符串里的 ANSI 颜色转义，非字符串原样返回。"""
    if isinstance(text, str):
        return _ANSI_RE.sub("", text)
    return text


def _stdout_is_tty():
    """stdout 是否连着终端。取不到（被替换/关闭）时按非终端处理。"""
    try:
        return bool(sys.stdout and sys.stdout.isatty())
    except Exception:
        return False


class ConsoleEchoFilter(logging.Filter):
    """控制台（回显）通道过滤。

    只放行两类记录：
      1. 显式回显：由 echo() 写出、带 ECHO_FLAG 的用户可见信息；
      2. 告警/错误：级别 >= console_echo_min_level（默认 WARNING）。
    技术性 INFO/DEBUG 日志只进文件，不上控制台。
    """

    def filter(self, record):
        if not console_echo_enable:
            return False
        if getattr(record, ECHO_FLAG, False):
            return True
        return record.levelno >= console_echo_min_level


class FileChannelFilter(logging.Filter):
    """日志文件（技术）通道过滤。

    - 回显专用内容（ECHO_FLAG 且非 DUAL_FLAG）不写入日志文件；
    - 关键业务事件用 echo(dual=True) 写出，**不受日志等级限制**地写入文件。
      否则把 log_level 调成 warning 就会连启动/登录/发货/退出都一并丢掉，
      违背 dual=True 的语义（显式标记为「必须留档」）。
    - 普通记录按 log_level 门限过滤；
    - 剥掉 ANSI 颜色转义，避免日志文件出现乱码控制符。

    等级门限之所以放在这里而不是 f_handler.setLevel()，是因为 handler 的等级
    检查先于 filter 执行，放在 handler 上就无法为 dual 事件开例外。

    实现细节：本 filter 会就地修改 record.msg 来剥离 ANSI，因此回显 handler
    必须**先于**本 handler 注册（handler 按注册顺序依次 emit），否则控制台也会
    丢失颜色。
    """

    def filter(self, record):
        is_echo = getattr(record, ECHO_FLAG, False)
        if is_echo and not getattr(record, DUAL_FLAG, False):
            return False
        if not is_echo and record.levelno < (log_level or logging.INFO):
            return False
        if isinstance(record.msg, str):
            record.msg = strip_ansi(record.msg)
        return True


_plain_formatter = logging.Formatter(_LOG_FMT, _LOG_DATEFMT)

s_handler = logging.StreamHandler()
# 级别门限交给 ConsoleEchoFilter，保证 echo() 不受级别限制
s_handler.setLevel(1)
# 只有连着终端才上色：输出被重定向到文件/管道时（后台运行的控制台日志）
# colorlog 仍会注入 ANSI 转义，在日志里留下乱码残渣，所以退回纯文本格式。
if _stdout_is_tty():
    s_handler.setFormatter(
        colorlog.ColoredFormatter(
            fmt="%(log_color)s" + _LOG_FMT,
            datefmt=_LOG_DATEFMT,
            log_colors={"DEBUG": "cyan", "INFO": "green", "WARNING": "yellow", "ERROR": "red", "CRITICAL": "bold_red"},
        )
    )
else:
    s_handler.setFormatter(_plain_formatter)
s_handler.addFilter(ConsoleEchoFilter())
logger.addHandler(s_handler)

f_handler = logging.FileHandler(os.path.join(LOGS_FOLDER, datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S") + ".log"), encoding="utf-8")
# 等级门限由 FileChannelFilter 判定（这样 echo(dual=True) 能绕过等级限制）
f_handler.setLevel(1)
f_handler.setFormatter(_plain_formatter)
f_handler.addFilter(FileChannelFilter())
logger.addHandler(f_handler)
logger.addFilter(LogFilter())


def echo(msg, dual=False, level=logging.INFO):
    """用户可见的「必要回显」——只上控制台，默认不写入日志文件。

    输出到非终端（重定向到文件/管道，如后台运行的控制台日志）时，先剥掉
    ANSI 颜色转义，避免日志里出现 `[0m` 这类乱码残渣。

    :param dual: True 表示关键业务事件，同时写入日志文件（受
                 console_echo.dual_write_events 配置控制）。
    :param level: 记录级别，回显本身不受级别门限限制。
    """
    if not _stdout_is_tty():
        msg = strip_ansi(msg)
    effective_dual = bool(dual) and console_dual_write_events
    logger.log(level, msg, extra={ECHO_FLAG: True, DUAL_FLAG: effective_dual})


def set_log_level(level):
    """运行时调整日志文件级别（热生效）。返回生效后的级别整数。

    只更新模块级 log_level；门限由 FileChannelFilter 在 emit 时读取，
    因此无需改动 handler 状态即可生效。
    """
    global log_level
    log_level = parse_log_level(level, logging.INFO)
    logger.debug("日志文件级别已调整为 %s", logging.getLevelName(log_level))
    return log_level


def set_console_echo_settings(enable=None, min_level=None, dual_events=None):
    """运行时调整回显通道设置（热生效）。"""
    global console_echo_enable, console_echo_min_level, console_dual_write_events
    if enable is not None:
        console_echo_enable = bool(enable)
    if min_level is not None:
        console_echo_min_level = parse_log_level(min_level, logging.WARNING)
    if dual_events is not None:
        console_dual_write_events = bool(dual_events)


def apply_log_settings(cfg) -> dict:
    """从完整配置字典重新应用日志设置（供 config.reload / 热改调用）。"""
    settings = read_log_settings(cfg)
    set_log_level(settings["level"])
    set_console_echo_settings(settings["echo_enable"], settings["echo_min_level"], settings["dual_events"])
    global log_retention_days
    log_retention_days = settings["retention"]
    return settings


def cleanup_old_logs():
    """按 log_retention_days 清理过期日志文件，返回删除数量。"""
    return prune_old_logs()


logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
logging.getLogger("apprise").setLevel(logging.WARNING)
logger.debug(f"Steamauto {CURRENT_VERSION} started")
logger.debug(f"Running on {platform.system()} {platform.release()}({platform.version()})")
logger.debug(f"Python version: {os.sys.version}")  # type: ignore
logger.debug(f"Build info: {BUILD_INFO}")
logger.debug(f"Attributes check: _MEIPASS: {hasattr(sys, '_MEIPASS')}, frozen: {hasattr(sys, 'frozen')}")


def handle_caught_exception(e: Exception, prefix: str = "", known: bool = False):
    plogger = logger
    if prefix and not prefix.endswith(" "):
        plogger = PluginLogger(prefix)
    if (not static.is_latest_version) and not known:
        plogger.warning("当前Steamauto版本可能不是最新版本！请在更新到新版本后再次尝试！")
    logger.debug(e, exc_info=True)

    if isinstance(e, KeyboardInterrupt):
        plogger.info("检测到键盘中断,程序即将退出...")
        exit(0)
    elif isinstance(e, SystemExit):
        plogger.info("检测到系统退出请求,程序即将退出...")
        exit(0)
    elif isinstance(e, requests.exceptions.SSLError):
        plogger.error("梯子问题, 请更换梯子")
    elif isinstance(e, EmptyResponse):
        plogger.error("Steam返回空响应, 可能是IP受到Steam风控, 请更换IP或稍后再试")
    elif isinstance(e, requests.exceptions.ProxyError):
        plogger.error("代理异常。建议关闭代理。如果你连接Steam有困难，可单独打开配置文件内的Steam代理功能。")
    elif isinstance(e, (ConnectionError, ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError, ReadTimeout, InvalidResponse)):
        plogger.error("网络异常, 请检查网络连接")
        plogger.error("这个错误可能是由于代理或VPN引起的, 本软件可不使用代理或任何VPN")
        plogger.error("如果你正在使用代理或VPN, 请尝试关闭后重启软件")
        plogger.error("如果你没有使用代理或VPN, 请检查网络连接")
    elif isinstance(e, InvalidCredentials):
        if "Invalid API key" in str(e):
            plogger.error("Steam access_token/API 会话已失效，正在或需要重新登录")
            plogger.error(str(e))
        else:
            plogger.error("Steam 登录凭据无效，请检查账号密码或mafile是否正确")
            plogger.error(str(e))
    elif isinstance(e, ConfirmationExpected):
        plogger.error("Steam Session已经过期, 请删除session文件夹并重启Steamauto")
    elif isinstance(e, SystemError):
        plogger.error("无法连接至Steam，请检查Steam账户状态、网络连接、或重启Steamauto")
    elif isinstance(e, SteamError):
        plogger.error("Steam 异常, 异常id:" + str(e.error_code) + ", 异常信息:" + STEAM_ERROR_CODES.get(e.error_code, "未知Steam错误"))
    elif isinstance(e, ApiException):
        if "Invalid trade offer state" in str(e):
            if "Canceled" in str(e):
                plogger.error("交易已取消，无法接受报价")
            elif "Accepted" in str(e):
                plogger.error("交易已接受，无法重复操作")
            else:
                plogger.error("交易状态异常，无法接受报价，异常信息：" + str(e))
        else:
            plogger.error("Steam API 异常, 异常信息: " + str(e))
    else:
        if not known:
            plogger.error(
                f"当前Steamauto版本：{CURRENT_VERSION}\nPython版本：{os.sys.version}\n系统版本：{platform.system()} {platform.release()}({platform.version()})\n编译信息：{BUILD_INFO}\n"  # type: ignore
            )
            plogger.error("发生未知异常, 异常信息:" + str(e) + ", 异常类型:" + str(type(e)) + " 已记录至日志文件")

        if BUILD_INFO == "正在使用源码运行":
            plogger.error(e, exc_info=True)


class PluginLogger:
    def __init__(self, pluginName):
        if "[" and "]" not in pluginName:
            self.pluginName = f"[{pluginName}]"
        else:
            self.pluginName = pluginName

    def debug(self, msg, *args, **kwargs):
        logger.debug(f"{self.pluginName} {msg}", *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        logger.info(f"{self.pluginName} {msg}", *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        logger.warning(f"{self.pluginName} {msg}", *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        logger.error(f"{self.pluginName} {msg}", *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        logger.critical(f"{self.pluginName} {msg}", *args, **kwargs)

    def log(self, level, msg, *args, **kwargs):
        logger.log(level, f"{self.pluginName} {msg}", *args, **kwargs)
