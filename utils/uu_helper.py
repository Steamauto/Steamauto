import os
import time

from colorama import Fore, Style

import uuyoupinapi
from utils.logger import PluginLogger, handle_caught_exception
from utils.static import UU_TOKEN_FILE_PATH
from utils.tools import get_encoding

logger = PluginLogger("UULoginSolver")


def get_valid_token_for_uu():
    logger.info("正在为悠悠有品获取有效的token...")
    if os.path.exists(UU_TOKEN_FILE_PATH):
        with open(UU_TOKEN_FILE_PATH, "r", encoding=get_encoding(UU_TOKEN_FILE_PATH)) as f:
            try:
                token = f.read()
                uuyoupin = uuyoupinapi.UUAccount(token)
                logger.info("悠悠有品成功登录, 用户名: " + uuyoupin.get_user_nickname())
                return token
            except Exception as e:
                logger.warning("缓存的悠悠有品Token无效")
    else:
        logger.info("未检测到存储的悠悠token")
    logger.info("即将重新登录悠悠有品！")
    token = str(get_token_automatically())
    try:
        uuyoupin = uuyoupinapi.UUAccount(token)
        logger.info("悠悠有品成功登录, 用户名: " + uuyoupin.get_user_nickname())
        with open(UU_TOKEN_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(token)
        logger.info("悠悠有品Token已自动缓存到本地")
        return token
    except TypeError:
        logger.error('获取Token失败！可能是验证码填写错误或者未发送验证短信！')
        return False
    except Exception as e:
        handle_caught_exception(e, "[UULoginSolver]")
        return False


def get_token_automatically():
    """
    引导用户输入手机号，发送验证码，输入验证码，自动登录，并且返回token
    :return: token
    """
    device_info = uuyoupinapi.generate_random_string(10)
    headers = uuyoupinapi.generate_headers(device_info, device_info)

    phone_number = input(f"{Style.BRIGHT+Fore.RED}请输入手机号(+86)(如果此时有其它插件输出请忽略！输入完按回车即可！)：{Style.RESET_ALL}")
    token_id = device_info
    logger.debug("随机生成的token_id：" + token_id)
    uk = ''
    try:
        from utils.cloud_service import get_uu_uk_from_cloud

        uk = get_uu_uk_from_cloud()
    except Exception:
        logger.warning("由于云服务已经关闭，无法获取UK，将使用默认配置")
        pass
    result = uuyoupinapi.UUAccount.send_login_sms_code(phone_number, token_id, headers=headers, uk=uk)
    response = {}
    if not ('成功' in result.get('Msg', '')):
        logger.info("发送验证码结果：" + result["Msg"])
        sms_code = input(f"{Style.BRIGHT+Fore.RED}请输入验证码(如果此时有其它插件输出请忽略！输入完按回车即可！)：{Style.RESET_ALL}")
        response = uuyoupinapi.UUAccount.sms_sign_in(phone_number, sms_code, token_id, headers=headers)
    else:
        logger.info("该手机号需要手动发送短信进行验证，正在获取相关信息...")
        result = uuyoupinapi.UUAccount.get_smsUpSignInConfig(headers).json()
        if result["Code"] == 0:
            logger.info("请求结果：" + result["Msg"])
            logger.info(
                f"{Style.BRIGHT+Fore.RED}请编辑发送短信 {Fore.YELLOW+result['Data']['SmsUpContent']} {Fore.RED}到号码 {Fore.YELLOW+result['Data']['SmsUpNumber']} {Fore.RED}！(如果此时有其它插件输出请忽略)发送完成后请按下回车{Style.RESET_ALL}",
            )
            input()
            logger.info("请稍候...")
            time.sleep(3)  # 防止短信发送延迟
            response = uuyoupinapi.UUAccount.sms_sign_in(phone_number, "", token_id, headers=headers)
    logger.info("登录结果：" + response["Msg"])
    try:
        got_token = response["Data"]["Token"]
    except (KeyError, TypeError, AttributeError):
        return False
    return got_token
