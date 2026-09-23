import base64
import hashlib
import hmac
import struct
import time
from typing import Dict, List, Tuple, Union
import requests
import urllib3
import urllib.parse
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
def _cookies_to_dict(cookie_str: str) -> dict:
    """将 Cookie 字符串转换为字典"""
    out = {}
    for part in (cookie_str or "").split(";"):
        s = part.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out
class SteamConfirmer:
    def __init__(self, identity_secret: str, device_id: str, steam_id: str, cookies: Union[str, dict]) -> None:
        raw_secret = (identity_secret or "").strip()
        raw_secret = raw_secret.replace("\\u002B", "+").replace("\\u002b", "+")
        raw_secret = raw_secret.replace("\u002B", "+").replace("\u002b", "+")
        raw_secret = raw_secret.replace("\\/", "/")
        self.identity_secret = raw_secret
        self.device_id = urllib.parse.unquote((device_id or "").strip())
        self.steam_id = str(steam_id or "").strip()
        self.session = requests.Session()
        self.session.verify = False
        if isinstance(cookies, str):
            ck_dict = _cookies_to_dict(cookies)
        else:
            ck_dict = cookies or {}
        self.session.cookies.update(ck_dict)
        self.session.headers.update({
            "User-Agent": "Steam Mobile/10372190 CFNetwork/3860.100.1 Darwin/25.0.0",
            "Accept": "application/json, text/plain, */*",
        })
    def _signature(self, tag: str, timestamp: int) -> str:
        secret_bytes = base64.b64decode(self.identity_secret)
        time_bytes = struct.pack(">Q", int(timestamp))
        if tag:
            time_bytes += tag.encode("ascii", errors="ignore")
        mac = hmac.new(secret_bytes, time_bytes, hashlib.sha1).digest()
        return base64.b64encode(mac).decode("utf-8")
    def get_confirmations(self) -> Tuple[bool, List[dict], str]:
        ts = int(time.time())
        try:
            sig = self._signature("conf", ts)
        except Exception as e:
            return False, [], f"签名失败: {e}"
        params = {
            "p": self.device_id,
            "a": self.steam_id,
            "k": sig,
            "t": ts,
            "m": "react",
            "tag": "conf",
        }
        url = "https://steamcommunity.com/mobileconf/getlist"
        try:
            r = self.session.get(url, params=params, timeout=20)
            try:
                data = r.json()
            except (ValueError, TypeError):
                return False, [], f"JSON 解析失败, status={r.status_code}, body={r.text[:200]}"
            if not data.get("success"):
                return False, [], str(data)
            return True, data.get("conf", []), ""
        except Exception as e:
            return False, [], str(e)
    def accept_all(self, conf_list: List[dict]) -> Tuple[bool, int, str]:
        if not conf_list:
            return True, 0, ""
        ts = int(time.time())
        try:
            sig = self._signature("accept", ts)
        except Exception as e:
            return False, 0, f"签名失败: {e}"
        params = {
            "p": self.device_id,
            "a": self.steam_id,
            "k": sig,
            "t": ts,
            "m": "react",
            "tag": "accept",
            "op": "allow",
        }
        multipart = []
        for c in conf_list:
            multipart.append(("cid[]", (None, str(c.get("id")))))
            multipart.append(("ck[]", (None, str(c.get("nonce")))))
        url = "https://steamcommunity.com/mobileconf/multiajaxop"
        try:
            r = self.session.post(url, params=params, files=multipart, timeout=25)
            try:
                data = r.json()
            except (ValueError, TypeError):
                return False, 0, f"JSON 解析失败, status={r.status_code}, body={r.text[:200]}"
            if not data.get("success"):
                return False, 0, str(data)
            return True, len(conf_list), ""
        except Exception as e:
            return False, 0, str(e)
def auto_confirm_once(identity_secret: str, device_id: str, steam_id: str, cookies: Union[str, dict]) -> Tuple[bool, int, str]:
    bot = SteamConfirmer(identity_secret, device_id, steam_id, cookies)
    ok, confs, err = bot.get_confirmations()
    if not ok:
        return False, 0, f"获取列表失败: {err}"
    ok2, n, err2 = bot.accept_all(confs)
    if not ok2:
        return False, 0, f"确认操作失败: {err2}"
    return True, n, ""