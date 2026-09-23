from typing import Optional
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
MARKET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.97 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}
def parse_cookies(cookie_str: str) -> dict:
    out = {}
    for item in cookie_str.split(";"):
        s = item.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out
def create_market_session(
    cookies_raw: str,
    steam_id: str,
    *,
    headers: Optional[dict] = None,
    verify: bool = False,
) -> requests.Session:
    session = requests.Session()
    session.verify = verify
    session.cookies.update(parse_cookies(cookies_raw))
    h = {**MARKET_HEADERS, "Referer": f"https://steamcommunity.com/profiles/{steam_id}/inventory/"}
    if headers:
        h.update(headers)
    session.headers.update(h)
    return session
