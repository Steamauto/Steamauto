import io
import os
import time
from typing import Dict

import qrcode
import requests
from bs4 import BeautifulSoup

from steampy.client import SteamClient
from utils.static import BUFF_COOKIES_FILE_PATH
from utils.tools import get_encoding


def parse_openid_params(response: str) -> Dict[str, str]:
    bs = BeautifulSoup(response, 'html.parser')
    params_to_find = ['action', 'openid.mode', 'openidparams', 'nonce']
    input_form = bs.find('form', {'id': 'openidForm'})
    params = {}
    for param in params_to_find:
        params[param] = input_form.find('input', {'name': param}).attrs['value']
    return params


def get_openid_params(steam_client: SteamClient) -> Dict[str, str]:
    response = requests.get('https://buff.163.com/account/login/steam?back_url=/', allow_redirects=False)
    response = steam_client._session.get(response.headers['Location'])
    return parse_openid_params(response.text)


# Return the cookies of buff
def login_to_buff(steam_client: SteamClient) -> str:
    params = get_openid_params(steam_client)
    response = steam_client._session.post('https://steamcommunity.com/openid/login', data=params, allow_redirects=False)
    while response.status_code == 302:
        response = steam_client._session.get(response.headers['Location'], allow_redirects=False)
    return steam_client._session.cookies.get_dict(domain='buff.163.com')


def login_to_buff_by_qrcode() -> str:
    get_qrcode_url = 'https://buff.163.com/account/api/qr_code_login_open?_=' + str(int(time.time() * 1000))
    response = requests.get(get_qrcode_url)
    response_json = response.json()
    if response_json["code"] != "OK":
        return ""
    cookies = response.cookies.get_dict(domain='buff.163.com')
    qr_code_create_url = 'https://buff.163.com/account/api/qr_code_create'
    response = requests.post(qr_code_create_url, json={'code_type': 1, 'extra_param': '{}'}, cookies=cookies).json()
    response_json = response.json()
    if response_json["code"] != "OK":
        return ""
    cookies = response.cookies.get_dict(domain='buff.163.com')
    code_id = response_json["data"]["code_id"]
    qr_code_url = response_json["data"]["url"]
    qrcode.make(qr_code_url).show()
    print("请使用手机扫描上方二维码登录BUFF")
    status = 0
    while status != 3:
        time.sleep(0.01)
        poll_url = ('https://buff.163.com/account/api/qr_code_poll?item_id=' + code_id + '&_=' +
                    str(int(time.time() * 1000)))
        response = requests.get(poll_url)
        response_json = response.json()
        if response_json["code"] != "OK":
            return ""
        status = response_json["data"]["state"]
    csrf_token = response.cookies.get_dict(domain='buff.163.com')["csrf_token"]
    response = requests.post('https://buff.163.com/account/api/qr_code_login', json={'item_id': code_id},
                             headers={'Referer': 'https://buff.163.com/', 'X-CSRFToken': csrf_token},
                             cookies=response.cookies)
    print(response.json()) # TODO: 永远都是二维码过期????
    cookies = response.cookies.get_dict(domain='buff.163.com')
    return cookies["session"]


def is_session_has_enough_permission(session: str) -> bool:
    if 'session=' not in session:
        session = 'session=' + session
    response_json = requests.get(
        "https://buff.163.com/api/market/steam_trade", headers={'Cookie': session}
    ).json()
    if 'data' not in response_json:
        return False
    return True


def get_valid_session_for_buff(steam_client: SteamClient, logger) -> str:
    if not os.path.exists(BUFF_COOKIES_FILE_PATH):
        buff_cookies = login_to_buff(steam_client)
        if "session" not in buff_cookies:
            logger.error("[buff_helper] 无法使用Steam登录至BUFF")
            return ""
        logger.info("[buff_helper] 已成功使用steam登录至BUFF")
        logger.info("[buff_helper] 检测session权限...")
        if is_session_has_enough_permission(buff_cookies["session"]):
            logger.info("[buff_helper] session权限足够, 已保存至" + BUFF_COOKIES_FILE_PATH)
            with open(BUFF_COOKIES_FILE_PATH, "w", encoding="utf-8") as f:
                f.write("session=" + buff_cookies["session"])
            return buff_cookies["session"]
        else:
            logger.error("[buff_helper] session权限不足, 请手动更新BUFF cookies! ")
            with open(BUFF_COOKIES_FILE_PATH, "w", encoding="utf-8") as f:
                f.write("session=")
            return ""
    logger.info("[buff_helper] 使用缓存的session")
    logger.info("[buff_helper] 检测session是否有效...")
    with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
        session = f.read().replace("\n", "").split(";")[0]
    user_name = get_buff_username(session)
    if not user_name:
        logger.error("[buff_helper] 缓存的session无效, 尝试使用steam登录")
        buff_cookies = login_to_buff(steam_client)
        if "session" not in buff_cookies:
            logger.error("[buff_helper] 无法使用Steam登录至BUFF, 请手动更新BUFF cookies! ")
            return ""
        session = buff_cookies["session"]
    logger.info("[buff_helper] 检测session权限...")
    if is_session_has_enough_permission(session):
        logger.info("[buff_helper] session权限足够, 已保存至" + BUFF_COOKIES_FILE_PATH)
        with open(BUFF_COOKIES_FILE_PATH, "w", encoding="utf-8") as f:
            f.write("session=" + session)
        return session
    else:
        logger.error("[buff_helper] session权限不足, 请手动更新BUFF cookies! ")
        with open(BUFF_COOKIES_FILE_PATH, "w", encoding="utf-8") as f:
            f.write("session=")
        return ""


def get_buff_username(session) -> str:
    if 'session=' not in session:
        session = 'session=' + session
    response_json = requests.get("https://buff.163.com/account/api/user/info", headers={'Cookie': session}).json()
    if response_json["code"] == "OK":
        if "data" in response_json:
            if "nickname" in response_json["data"]:
                return response_json["data"]["nickname"]
    return ""
