import os
import time

import uuyoupinapi
from utils.logger import PluginLogger, handle_caught_exception
from utils.static import UU_TOKEN_FILE_PATH
from utils.tools import get_encoding

logger = PluginLogger("UULoginHelper")


def get_valid_token_for_uu():
    relogin = True
    logger.info("正在为悠悠有品获取有效的token...")
    if os.path.exists(UU_TOKEN_FILE_PATH):
        with open(UU_TOKEN_FILE_PATH, "r", encoding=get_encoding(UU_TOKEN_FILE_PATH)) as f:
            try:
                uuyoupin = uuyoupinapi.UUAccount(f.read())
                logger.info("悠悠有品成功登录, 用户名: " + uuyoupin.get_user_nickname())
                relogin = False
                return f.read()
            except Exception as e:
                handle_caught_exception(e, "[UULoginHelper]")
                logger.warning("悠悠有品token无效")
    else:
        logger.info("未检测到存储的悠悠token")
    logger.info("即将重新登录悠悠有品！")
    token = get_token_automatically()
    try:
        uuyoupin = uuyoupinapi.UUAccount(token)
        logger.info("悠悠有品成功登录, 用户名: " + uuyoupin.get_user_nickname())
        with open(UU_TOKEN_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(token)
        logger.info("悠悠有品token已保存到文件")
        return token
    except Exception as e:
        handle_caught_exception(e, "[UULoginHelper]")
        return False


def get_token_automatically():
    """
    引导用户输入手机号，发送验证码，输入验证码，自动登录，并且返回token
    :return: token
    """
    device_info = uuyoupinapi.generate_device_info()
    headers = uuyoupinapi.generate_headers(device_info["deviceId"], device_info["deviceId"])

    phone_number = input("输入手机号(+86)：")
    token_id = device_info["deviceId"]
    logger.debug("随机生成的token_id：" + token_id)
    result = uuyoupinapi.UUAccount.send_login_sms_code(phone_number, token_id, headers=headers)
    response = {}
    if result["Code"] != 5050:
        logger.info("发送验证码结果：" + result["Msg"])
        sms_code = input("输入验证码：")
        response = uuyoupinapi.UUAccount.sms_sign_in(phone_number, sms_code, token_id, headers=headers)
    else:
        logger.info("该手机号需要手动发送短信进行验证，正在获取相关信息...")
        result = uuyoupinapi.UUAccount.get_smsUpSignInConfig(headers).json()
        if result["Code"] == 0:
            logger.info("请求结果：" + result["Msg"])
            logger.info(
                f"请编辑发送短信 \033[1;33m{result['Data']['SmsUpContent']}\033[0m 到号码 \033[1;31m{result['Data']['SmsUpNumber']}\033[0m ！\n发送完成后请点击回车.",
            )
            input()
            logger.info("请稍候...")
            time.sleep(3)  # 防止短信发送延迟
            response = uuyoupinapi.UUAccount.sms_sign_in(phone_number, "", token_id, headers=headers)
    logger.info("登录结果：" + response["Msg"])
    got_token = response["Data"]["Token"]
    return got_token
