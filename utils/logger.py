import datetime
import logging
import os
import platform
import re

import colorlog
import requests
from requests.exceptions import ConnectionError, ReadTimeout

from steampy.exceptions import (ApiException, ConfirmationExpected,
                                EmptyResponse, InvalidCredentials,
                                InvalidResponse, SteamError)
from utils.static import (BUILD_INFO, CURRENT_VERSION, LOGS_FOLDER,
                          get_is_latest_version)

sensitive_data = []
sensitive_keys = ["ApiKey", "TradeLink", "JoinTime", "NickName", "access_token", "trade_url","TransactionUrl","RealName","IdCard"]


class LogFilter(logging.Filter):
    @staticmethod
    def add_sensitive_data(data):
        sensitive_data.append(data)

    def filter(self, record):
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

            record.msg = re.sub(pattern, replace_match, record.msg, flags=re.IGNORECASE) # type: ignore

        # 处理 URL 参数中的敏感信息
        for key in sensitive_keys:
            pattern = rf"({key}=)([^&\s]+)"
            def replace_url_match(match):
                return f"{match.group(1)}{mask_value(match.group(2))}"

            record.msg = re.sub(pattern, replace_url_match, record.msg, flags=re.IGNORECASE)

        return True


STEAM_ERROR_CODES = {
    1: "成功",
    2: "失败",
    3: "无连接",
    4: "无连接，重试",
    5: "无效密码",
    6: "已在其他地方登录",
    7: "无效协议版本",
    8: "无效参数",
    9: "文件未找到",
    10: "忙碌",
    11: "无效状态",
    12: "无效名称",
    13: "无效电子邮件",
    14: "重复名称",
    15: "访问被拒绝",
    16: "超时",
    17: "已封禁",
    18: "账户未找到",
    19: "无效Steam ID",
    20: "服务不可用",
    21: "未登录",
    22: "待定",
    23: "加密失败",
    24: "权限不足",
    25: "超出限制",
    26: "已吊销",
    27: "已过期",
    28: "已被兑换",
    29: "重复请求",
    30: "已拥有",
    31: "IP未找到",
    32: "持久化失败",
    33: "锁定失败",
    34: "登录会话被替换",
    35: "连接失败",
    36: "握手失败",
    37: "IO失败",
    38: "远程断开连接",
    39: "购物车未找到",
    40: "被阻止",
    41: "被忽略",
    42: "无匹配项",
    43: "账户已禁用",
    44: "服务只读",
    45: "账户未特色",
    46: "管理员操作成功",
    47: "内容版本错误",
    48: "尝试切换CM失败",
    49: "需要密码以踢出会话",
    50: "已在其他地方登录",
    51: "已暂停",
    52: "已取消",
    53: "数据损坏",
    54: "磁盘已满",
    55: "远程调用失败",
    56: "密码未设置",
    57: "外部帐户已取消链接",
    58: "PSN票证无效",
    59: "外部帐户已链接",
    60: "远程文件冲突",
    61: "密码不合法",
    62: "与上一个值相同",
    63: "账户登录被拒绝",
    64: "无法使用旧密码",
    65: "无效登录验证代码",
    66: "账户登录被拒绝，无邮件",
    67: "硬件不支持IPT",
    68: "IPT初始化错误",
    69: "受家长控制限制",
    70: "Facebook查询错误",
    71: "过期的登录验证代码",
    72: "IP登录限制失败",
    73: "账户被锁定",
    74: "需要验证电子邮件",
    75: "没有匹配的URL",
    76: "响应错误",
    77: "需要重新输入密码",
    78: "值超出范围",
    79: "意外错误",
    80: "已禁用",
    81: "无效CEG提交",
    82: "受限设备",
    83: "地区限制",
    84: "速率限制已超出",
    85: "需要双因素验证登录",
    86: "物品已删除",
    87: "账户登录被限速",
    88: "双因素验证码不匹配, 请检查shared_secret是否正确",
    89: "双因素激活码不匹配",
    90: "关联多个合作伙伴账户",
    91: "未修改",
    92: "无手机设备",
    93: "时间未同步",
    94: "短信验证码失败",
    95: "账户限制超出",
    96: "账户活动限制超出",
    97: "电话活动限制超出",
    98: "退款到钱包",
    99: "电子邮件发送失败",
    100: "未解决",
    101: "需要验证码",
    102: "GSLT拒绝",
    103: "GSLT所有者拒绝",
    104: "无效物品类型",
    105: "IP封禁",
    106: "GSLT已过期",
    107: "资金不足",
    108: "待处理事务过多",
    109: "未找到站点许可证",
    110: "WG网络发送超出限制",
    111: "账户未添加好友",
    112: "有限用户账户",
    113: "无法移除物品",
    114: "账户已删除",
    115: "现有用户取消许可证",
    116: "社区冷却中",
    117: "未指定启动器",
    118: "必须同意用户协议",
    119: "启动器已迁移",
    120: "Steam领域不匹配",
    121: "无效签名",
    122: "解析失败",
    123: "无验证手机",
}


logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
s_handler = logging.StreamHandler()
s_handler.setLevel(logging.INFO)
log_formatter = colorlog.ColoredFormatter(
    fmt="%(log_color)s[%(asctime)s] - %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    log_colors={"DEBUG": "cyan", "INFO": "green", "WARNING": "yellow", "ERROR": "red", "CRITICAL": "bold_red"},
)
s_handler.setFormatter(log_formatter)
logger.addHandler(s_handler)
if not os.path.exists(LOGS_FOLDER):
    os.mkdir(LOGS_FOLDER)
f_handler = logging.FileHandler(
    os.path.join(LOGS_FOLDER, datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S") + ".log"), encoding="utf-8"
)
f_handler.setLevel(logging.DEBUG)
f_handler.setFormatter(log_formatter)
logger.addHandler(f_handler)
logger.addFilter(LogFilter())
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
logger.debug(f"Steamauto {CURRENT_VERSION} started")
logger.debug(f"Running on {platform.system()} {platform.release()}({platform.version()})")
logger.debug(f"Python version: {os.sys.version}") # type: ignore
logger.debug(f"Build info: {BUILD_INFO}")


def handle_caught_exception(e: Exception, prefix: str = ""):
    plogger = logger
    if prefix and not prefix.endswith(" "):
        plogger = PluginLogger(prefix)
    if not get_is_latest_version():
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
    elif isinstance(
        e, (ConnectionError, ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError, ReadTimeout, InvalidResponse)
    ):
        plogger.error("网络异常, 请检查网络连接")
        plogger.error("这个错误可能是由于代理或VPN引起的, 本软件可不使用代理或任何VPN")
        plogger.error("如果你正在使用代理或VPN, 请尝试关闭后重启软件")
        plogger.error("如果你没有使用代理或VPN, 请检查网络连接")
    elif isinstance(e, InvalidCredentials):
        plogger.error("mafile有问题, 请检查mafile是否正确(尤其是identity_secret)")
        plogger.error(str(e))
    elif isinstance(e, ConfirmationExpected):
        plogger.error("Steam Session已经过期, 请删除session文件夹并重启Steamauto")
    elif isinstance(e, ValueError):
        plogger.error("Steam 宵禁限制, 请稍后再试!")
    elif isinstance(e, SystemError):
        plogger.error("无法连接至Steam，请检查Steam账户状态、网络连接、或重启Steamauto")
    elif isinstance(e, SteamError):
        plogger.error(
            "Steam 异常, 异常id:"
            + str(e.error_code)
            + ", 异常信息:"
            + STEAM_ERROR_CODES.get(e.error_code, "未知Steam错误")
        )
    elif isinstance(e, ApiException):
        plogger.error("Steam API 异常, 异常信息:" + str(e))
    else:
        plogger.error(
            f"当前Steamauto版本：{CURRENT_VERSION}\nPython版本：{os.sys.version}\n系统版本：{platform.system()} {platform.release()}({platform.version()})\n编译信息：{BUILD_INFO}\n"
        )
        plogger.error("发生未知异常, 异常信息:" + str(e) + ", 异常类型:" + str(type(e)) + ", 建议反馈至开发者！")
        if BUILD_INFO == '正在使用源码运行':
            plogger.error(e,exc_info=True)


class PluginLogger:
    def __init__(self, pluginName):
        self.pluginName = pluginName

    def debug(self, msg, *args, **kwargs):
        logger.debug(f"[{self.pluginName}] {msg}", *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        logger.info(f"[{self.pluginName}] {msg}", *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        logger.warning(f"[{self.pluginName}] {msg}", *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        logger.error(f"[{self.pluginName}] {msg}", *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        logger.critical(f"[{self.pluginName}] {msg}", *args, **kwargs)
