from time import time
from http import HTTPStatus
from base64 import b64encode

from rsa import encrypt, PublicKey
from requests import Session, Response

from steampy import guard
from steampy.models import SteamUrl
from steampy.exceptions import InvalidCredentials, CaptchaRequired, ApiException


class LoginExecutor:
    def __init__(self, username: str, password: str, shared_secret: str, session: Session) -> None:
        self.username = username
        self.password = password
        self.one_time_code = ""
        self.shared_secret = shared_secret
        self.session = session

    def _api_call(self, method: str, service: str, endpoint: str, version: str = "v1", params: dict = None) -> Response:
        url = "/".join([SteamUrl.API_URL, service, endpoint, version])
        # all requests from the login page use the same "Referer" and "Origin" values
        headers = {"Referer": SteamUrl.COMMUNITY_URL + "/", "Origin": SteamUrl.COMMUNITY_URL}
        if method.upper() == "GET":
            return self.session.get(url, params=params, headers=headers)
        else:
            return self.session.post(url, data=params, headers=headers)

    def login(self) -> Session:
        login_response = self._send_login_request()
        self._check_for_captcha(login_response)
        login_response = self._enter_steam_guard_if_necessary(login_response)
        self._assert_valid_credentials(login_response)
        self._perform_redirects(login_response.json())
        self.set_sessionid_cookies()
        return self.session

    def _send_login_request(self) -> Response:
        rsa_params = self._fetch_rsa_params()
        encrypted_password = self._encrypt_password(rsa_params)
        rsa_timestamp = rsa_params["rsa_timestamp"]
        request_data = self._prepare_login_request_data(encrypted_password, rsa_timestamp)
        return self.session.post(SteamUrl.COMMUNITY_URL + "/login/dologin", data=request_data)

    def set_sessionid_cookies(self):
        sessionid = self.session.cookies.get_dict()["sessionid"]
        community_domain = SteamUrl.COMMUNITY_URL[8:]
        store_domain = SteamUrl.STORE_URL[8:]
        community_cookie = self._create_session_id_cookie(sessionid, community_domain)
        store_cookie = self._create_session_id_cookie(sessionid, store_domain)
        self.session.cookies.set(**community_cookie)
        self.session.cookies.set(**store_cookie)

    @staticmethod
    def _create_session_id_cookie(sessionid: str, domain: str) -> dict:
        return {"name": "sessionid", "value": sessionid, "domain": domain}

    def _fetch_rsa_params(self, current_number_of_repetitions: int = 0) -> dict:
        request_data = {"account_name": self.username}
        response = self._api_call("GET", "IAuthenticationService", "GetPasswordRSAPublicKey", params=request_data)

        if response.status_code == HTTPStatus.OK and "response" in response.json():
            key_data = response.json()["response"]
            # Steam may return an empty "response" value even if the status is 200
            if "publickey_mod" in key_data and "publickey_exp" in key_data and "timestamp" in key_data:
                rsa_mod = int(key_data["publickey_mod"], 16)
                rsa_exp = int(key_data["publickey_exp"], 16)
                return {"rsa_key": PublicKey(rsa_mod, rsa_exp), "rsa_timestamp": key_data["timestamp"]}

        maximal_number_of_repetitions = 5
        if current_number_of_repetitions < maximal_number_of_repetitions:
            return self._fetch_rsa_params(current_number_of_repetitions + 1)

        raise ApiException("Could not obtain rsa-key. Status code: %s" % response.status_code)

    def _encrypt_password(self, rsa_params: dict) -> bytes:
        return b64encode(encrypt(self.password.encode("utf-8"), rsa_params["rsa_key"]))

    def _prepare_login_request_data(self, encrypted_password: bytes, rsa_timestamp: str) -> dict:
        return {
            "password": encrypted_password,
            "username": self.username,
            "twofactorcode": self.one_time_code,
            "emailauth": "",
            "loginfriendlyname": "",
            "captchagid": "-1",
            "captcha_text": "",
            "emailsteamid": "",
            "rsatimestamp": rsa_timestamp,
            "remember_login": "true",
            "donotcache": str(int(time() * 1000)),
        }

    @staticmethod
    def _check_for_captcha(login_response: Response) -> None:
        if login_response.json().get("captcha_needed", False):
            raise CaptchaRequired("Captcha required")

    def _enter_steam_guard_if_necessary(self, login_response: Response) -> Response:
        if login_response.json()["requires_twofactor"]:
            self.one_time_code = guard.generate_one_time_code(self.shared_secret)
            return self._send_login_request()
        return login_response

    @staticmethod
    def _assert_valid_credentials(login_response: Response) -> None:
        if not login_response.json()["success"]:
            raise InvalidCredentials(login_response.json()["message"])

    def _perform_redirects(self, response_dict: dict) -> None:
        parameters = response_dict.get("transfer_parameters")
        if parameters is None:
            raise Exception("Cannot perform redirects after login, no parameters fetched")
        for url in response_dict["transfer_urls"]:
            self.session.post(url, parameters)

    def _fetch_home_page(self, session: Session) -> Response:
        return session.post(SteamUrl.COMMUNITY_URL + "/my/home/")
