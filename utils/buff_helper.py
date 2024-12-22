import base64
from json import JSONDecodeError
import os
import time
from typing import Dict

import apprise
import json5
import qrcode
import qrcode_terminal
import requests
from apprise import AppriseAsset, AppriseAttachment
from bs4 import BeautifulSoup

from steampy.client import SteamClient
from utils.static import APPRISE_ASSET_FOLDER, BUFF_COOKIES_FILE_PATH, CONFIG_FILE_PATH
from utils.tools import get_encoding, logger

from requests_toolbelt.multipart.encoder import MultipartEncoder

def parse_openid_params(response: str) -> Dict[str, str]:
    bs = BeautifulSoup(response, "html.parser")
    params_to_find = ["action", "openid.mode", "openidparams", "nonce"]
    input_form = bs.find("form", {"id": "openidForm"})
    params = {}
    for param in params_to_find:
        params[param] = input_form.find("input", {"name": param}).attrs["value"]
    return params


def get_openid_params(steam_client: SteamClient) -> Dict[str, str]:
    session = requests.Session()
    response = requests.get("https://buff.163.com/account/login/steam?back_url=/", allow_redirects=False)
    location_url = response.headers["Location"]
    response = steam_client._session.get(location_url)
    return parse_openid_params(response.text), location_url, session


# Return the cookies of buff
def login_to_buff_by_steam(steam_client: SteamClient) -> str:
    params, location_url, session = get_openid_params(steam_client)
    multipart_data = MultipartEncoder(fields=params)
    headers = {
        'Content-Type': multipart_data.content_type,
        'Host': 'steamcommunity.com',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.70 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Referer': location_url
              },
    response = steam_client._session.post("https://steamcommunity.com/openid/login", data=multipart_data, headers=headers, allow_redirects=False)
    while response.status_code == 302:
        response = session.get(response.headers["Location"], allow_redirects=False)
    # 测试是否可用
    data = session.get(url='https://buff.163.com/account/api/login/status').json()['data']
    if data['state'] == 2:
        return session.cookies.get_dict(domain="buff.163.com")


def login_to_buff_by_qrcode() -> str:
    session = requests.session()
    response_json = session.get("https://buff.163.com/account/api/qr_code_login_open", params={"_": str(int(time.time() * 1000))}).json()
    if response_json["code"] != "OK":
        return ""
    qr_code_create_url = "https://buff.163.com/account/api/qr_code_create"
    response_json = session.post(qr_code_create_url, json={"code_type": 1, "extra_param": "{}"}).json()
    if response_json["code"] != "OK":
        logger.error("获取二维码失败")
        return ""
    code_id = response_json["data"]["code_id"]
    qr_code_url = response_json["data"]["url"]
    qrcode_terminal.draw(qr_code_url)
    img = qrcode.make(qr_code_url)
    img.save("qrcode.png")
    config = {}

    # 读取本地图片文件内容编码为Base64
    with open("qrcode.png", "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")

    html_content = f"<br/><img src='data:image/png;base64,{encoded_string}' />"

    try:
        with open(CONFIG_FILE_PATH, "r", encoding=get_encoding(CONFIG_FILE_PATH)) as f:
            config = json5.load(f)
    except:
        pass
    if "buff_login_notification" in config["buff_auto_accept_offer"]:
        asset = AppriseAsset(plugin_paths=[os.path.join(os.path.dirname(__file__), "..", APPRISE_ASSET_FOLDER)])
        apprise_obj = apprise.Apprise(asset=asset)
        for server in config["buff_auto_accept_offer"]["servers"]:
            apprise_obj.add(server)
        # 检查是否应包括HTML内容
        if (
            "buff_login_notification" in config["buff_auto_accept_offer"]
            and "include_qrcode_html_enable" in config["buff_auto_accept_offer"]["buff_login_notification"]
            and config["buff_auto_accept_offer"]["buff_login_notification"]["include_qrcode_html_enable"]
        ):
            body_content = "保存图片到手机，用Buff扫码登陆" + html_content
        else:
            body_content = "请使用手机扫描上方二维码登录BUFF或打开程序目录下的qrcode.png扫描"
        apprise_obj.notify(
            title=config["buff_auto_accept_offer"]["buff_login_notification"]["title"],
            body=body_content,
            attach=AppriseAttachment("qrcode.png"),
        )
    logger.info("请使用手机扫描上方二维码登录BUFF或打开程序目录下的qrcode.png扫描")
    status = 0
    scanned = False
    while status != 3:
        time.sleep(1)
        response_json = session.get(
            "https://buff.163.com/account/api/qr_code_poll", params={"_": str(int(time.time() * 1000)), "item_id": code_id}
        ).json()
        status = response_json["data"]["state"]
        if status == 4 or response_json["code"] != "OK":
            logger.error("二维码已失效")
            return ""
        if status == 2 and not scanned:
            scanned = True
            logger.info("扫描成功，请在手机上确认登录(建议勾选10天内免登录)")
    response = session.post(
        "https://buff.163.com/account/api/qr_code_login",
        json={"item_id": code_id},
    )
    logger.debug(json5.dumps(response.json()))
    cookies = response.cookies.get_dict(domain="buff.163.com")
    return cookies["session"]


def is_session_has_enough_permission(session: str) -> bool:
    if "session=" not in session:
        session = "session=" + session
    try:
        response_json = requests.get("https://buff.163.com/api/market/steam_trade", headers={"Cookie": session}).json()
        if "data" not in response_json:
            return False
        return True
    except:
        return False


def get_valid_session_for_buff(steam_client: SteamClient, logger) -> str:
    logger.info('[BuffLoginSolver] 正在获取与检查BUFF session...')
    global session
    session = ""
    if not os.path.exists(BUFF_COOKIES_FILE_PATH):
        with open(BUFF_COOKIES_FILE_PATH, "w", encoding="utf-8") as f:
            f.write("session=")
    else:
        with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
            session = f.read().replace("\n", "")
        if session and session != "session=":
            logger.info("[BuffLoginSolver] 使用缓存的session")
            logger.info("[BuffLoginSolver] 检测session是否有效...")
            if not is_session_has_enough_permission(session):
                logger.error("[BuffLoginSolver] 缓存的session无效")
                session = ""
            else:
                logger.info("[BuffLoginSolver] 缓存的session有效")
        else:
            session = ""
    if not session:  # 尝试通过Steam
        logger.info("[BuffLoginSolver] 尝试通过Steam登录至BUFF")
        got_cookies = {}
        try:
            got_cookies = login_to_buff_by_steam(steam_client)
        except Exception as e:
            logger.error(f"[BuffLoginSolver] 使用Steam登录至BUFF失败")
        if "session" not in got_cookies or not is_session_has_enough_permission(got_cookies["session"]):
            logger.error("[BuffLoginSolver] 使用Steam登录至BUFF失败")
        else:
            logger.info('[BuffLoginSolver] 使用Steam登录至BUFF成功')
            session = got_cookies["session"]
    if not session:  # 尝试通过二维码
        logger.info("[BuffLoginSolver] 尝试通过二维码登录至BUFF")
        try:
            session = login_to_buff_by_qrcode()
            if (not session) or (not is_session_has_enough_permission(session)):
                logger.error("[BuffLoginSolver] 使用Steam登录至BUFF失败")
            else:
                logger.info('[BuffLoginSolver] 使用二维码登录至BUFF成功')
        except JSONDecodeError:
            logger.error('[BuffLoginSolver] 你的服务器IP被BUFF封禁。请尝试更换服务器！')
            session = ""
    if not session:  # 无法登录至BUFF
        logger.error("[BuffLoginSolver] 无法登录至BUFF, 请手动更新BUFF cookies! ")
    else:
        with open(BUFF_COOKIES_FILE_PATH, "w", encoding="utf-8") as f:
            f.write("session=" + session.replace("session=", ""))
    if "session=" not in session:
        session = "session=" + session
    return session


def get_buff_username(session) -> str:
    if "session=" not in session:
        session = "session=" + session
    response_json = requests.get("https://buff.163.com/account/api/user/info", headers={"Cookie": session}).json()
    if response_json["code"] == "OK":
        if "data" in response_json:
            if "nickname" in response_json["data"]:
                return response_json["data"]["nickname"]
    return ""
