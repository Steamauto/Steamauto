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
        self.one_time_code = ''
        self.shared_secret = shared_secret
        self.session = session
        self.refresh_token = ''

    def _api_call(self, method: str, service: str, endpoint: str, version: str = 'v1', params: dict = None) -> Response:
        url = '/'.join([SteamUrl.API_URL, service, endpoint, version])
        # all requests from the login page use the same "Referer" and "Origin" values
        headers = {
            "Referer": SteamUrl.COMMUNITY_URL + '/',
            "Origin": SteamUrl.COMMUNITY_URL
        }
        if method.upper() == 'GET':
            return self.session.get(url, params = params, headers = headers)
        elif method.upper() == 'POST':
            return self.session.post(url, data = params, headers = headers)
        else:
            raise ValueError('Method must be either GET or POST')

    def login(self) -> Session:
        login_response = self._send_login_request()
        if len(login_response.json()['response']) == 0:
            raise ApiException('No response received from Steam API. Please try again later.')
        self._check_for_captcha(login_response)
        self._update_steam_guard(login_response)
        finallized_response = self._finallize_login()
        self._perform_redirects(finallized_response.json())
        self.set_sessionid_cookies()
        return self.session

    def _send_login_request(self) -> Response:
        rsa_params = self._fetch_rsa_params()
        encrypted_password = self._encrypt_password(rsa_params)
        rsa_timestamp = rsa_params['rsa_timestamp']
        request_data = self._prepare_login_request_data(encrypted_password, rsa_timestamp)
        return self._api_call('POST', 'IAuthenticationService', 'BeginAuthSessionViaCredentials', params = request_data)

    def set_sessionid_cookies(self):
        community_domain = SteamUrl.COMMUNITY_URL[8:]
        store_domain = SteamUrl.STORE_URL[8:]
        for name in ['steamLoginSecure', 'sessionid', 'steamRefresh_steam', 'steamCountry']:
            cookie = self.session.cookies.get_dict()[name]
            community_cookie = self._create_cookie(name, cookie, community_domain)
            store_cookie = self._create_cookie(name, cookie, store_domain)
            self.session.cookies.set(**community_cookie)
            self.session.cookies.set(**store_cookie)

    @staticmethod
    def _create_cookie(name: str, cookie: str, domain: str) -> dict:
        return {"name": name,
                "value": cookie,
                "domain": domain}

    def _fetch_rsa_params(self, current_number_of_repetitions: int = 0) -> dict:
        self.session.post(SteamUrl.COMMUNITY_URL)
        request_data = {'account_name': self.username}
        response = self._api_call('GET', 'IAuthenticationService', 'GetPasswordRSAPublicKey', params = request_data)

        if response.status_code == HTTPStatus.OK and 'response' in response.json():
            key_data = response.json()['response']
            # Steam may return an empty "response" value even if the status is 200
            if 'publickey_mod' in key_data and 'publickey_exp' in key_data and 'timestamp' in key_data:
                rsa_mod = int(key_data['publickey_mod'], 16)
                rsa_exp = int(key_data['publickey_exp'], 16)
                return {'rsa_key': PublicKey(rsa_mod, rsa_exp), 'rsa_timestamp': key_data['timestamp']}

        maximal_number_of_repetitions = 5
        if current_number_of_repetitions < maximal_number_of_repetitions:
            return self._fetch_rsa_params(current_number_of_repetitions + 1)

        raise ApiException('Could not obtain rsa-key. Status code: %s' % response.status_code)

    def _encrypt_password(self, rsa_params: dict) -> bytes:
        return b64encode(encrypt(self.password.encode('utf-8'), rsa_params['rsa_key']))

    def _prepare_login_request_data(self, encrypted_password: bytes, rsa_timestamp: str) -> dict:
        return {
            'persistence': "1",
            'encrypted_password': encrypted_password,
            'account_name': self.username,
            'encryption_timestamp': rsa_timestamp,
        }

    @staticmethod
    def _check_for_captcha(login_response: Response) -> None:
        if login_response.json().get('captcha_needed', False):
            raise CaptchaRequired('Captcha required')

    def _enter_steam_guard_if_necessary(self, login_response: Response) -> Response:
        if login_response.json()['requires_twofactor']:
            self.one_time_code = guard.generate_one_time_code(self.shared_secret)
            return self._send_login_request()
        return login_response

    @staticmethod
    def _assert_valid_credentials(login_response: Response) -> None:
        if not login_response.json()['success']:
            raise InvalidCredentials(login_response.json()['message'])

    def _perform_redirects(self, response_dict: dict) -> None:
        parameters = response_dict.get('transfer_info')
        if parameters is None:
            raise Exception('Cannot perform redirects after login, no parameters fetched')
        for pass_data in parameters:
            pass_data['params']['steamID'] = response_dict['steamID']
            self.session.post(pass_data['url'], pass_data['params'])

    def _fetch_home_page(self, session: Session) -> Response:
        return session.post(SteamUrl.COMMUNITY_URL + '/my/home/')

    def _update_steam_guard(self, login_response: Response) -> bool:
        client_id = login_response.json()["response"]["client_id"]
        steamid = login_response.json()["response"]["steamid"]
        request_id = login_response.json()["response"]["request_id"]
        code_type = 3
        code = guard.generate_one_time_code(self.shared_secret)

        update_data = {
            'client_id': client_id,
            'steamid': steamid,
            'code_type': code_type,
            'code': code
        }
        response = self._api_call('POST', 'IAuthenticationService', 'UpdateAuthSessionWithSteamGuardCode',
                                  params = update_data)
        if response.status_code == 200:
            self._pool_sessions_steam(client_id, request_id)
            return True
        else:
            raise Exception('Cannot update steam guard')

    def _pool_sessions_steam(self, client_id, request_id):
        pool_data = {
            'client_id': client_id,
            'request_id': request_id
        }
        response = self._api_call('POST', 'IAuthenticationService', 'PollAuthSessionStatus', params = pool_data)
        self.refresh_token = response.json()["response"]["refresh_token"]

    def _finallize_login(self):
        sessionid = self.session.cookies["sessionid"]
        redir = "https://steamcommunity.com/login/home/?goto="

        finallez_data = {
            'nonce': self.refresh_token,
            'sessionid': sessionid,
            'redir': redir
        }
        response = self.session.post("https://login.steampowered.com/jwt/finalizelogin", data = finallez_data)
        return response
