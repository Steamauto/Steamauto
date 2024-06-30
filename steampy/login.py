import base64
import json
from time import time
from http import HTTPStatus
from base64 import b64encode
from typing import List, Dict, Any

import rsa
from rsa import encrypt, PublicKey
from requests import Session, Response
from protobufs.enums_pb2 import ESessionPersistence
from protobufs.steammessages_auth.steamclient_pb2 import *

from steampy.schemas import FinalizeLoginStatus, TransferInfoItem, Params
from steampy.utils import check_error
from steampy import guard
from steampy.models import SteamUrl
from steampy.exceptions import InvalidCredentials, CaptchaRequired, ApiException, EmptyResponse, SteamError
from steampy.steam_error_codes import STEAM_ERROR_CODES


class LoginExecutor:

    def __init__(self, username: str, password: str, shared_secret: str, session: Session,
                 get_email_on_time_code_func: callable = None, func_2fa_input: callable = None) -> None:
        self.username = username
        self.password = password
        self.one_time_code = ''
        self.email_auth_code = ''
        self.shared_secret = shared_secret
        self.session = session
        self.get_email_on_time_code_func = get_email_on_time_code_func
        self.func_2fa_input = func_2fa_input

    def _api_call(self, method: str, service: str, endpoint: str, version: str = 'v1', params: dict = None,
                  ignore_error_num: List = None) -> Response:
        url = '/'.join([SteamUrl.API_URL, service, endpoint, version])
        # all requests from the login page use the same "Referer" and "Origin" values
        headers = {
            "Referer": SteamUrl.COMMUNITY_URL + '/',
            "Origin": SteamUrl.COMMUNITY_URL
        }
        if method.upper() == 'GET':
            resp = self.session.get(url, params=params, headers=headers, allow_redirects=False)
            check_error(resp, ignore_error_num)
            while resp.status_code == 302:
                resp = self.session.get(resp.headers['Location'], allow_redirects=False)
                check_error(resp, ignore_error_num)
            return resp
        else:
            resp = self.session.post(url, data=params, headers=headers, allow_redirects=False)
            check_error(resp, ignore_error_num)
            while resp.status_code == 302:
                resp = self.session.post(resp.headers['Location'], allow_redirects=False)
                check_error(resp, ignore_error_num)
            return resp

    def login(self) -> Session:
        self._send_login_request_protobuf()
        return self.session

    def _send_login_request(self) -> Response:
        rsa_params = self._fetch_rsa_params()
        encrypted_password = self._encrypt_password(rsa_params)
        rsa_timestamp = rsa_params['rsa_timestamp']
        request_data = self._prepare_login_request_data(encrypted_password, rsa_timestamp)
        return self.session.post(SteamUrl.COMMUNITY_URL + '/login/dologin', data=request_data)

    def _send_login_request_protobuf(self) -> None:
        rsa_params = self._fetch_rsa_params_protobuf()
        encrypted_password = self._encrypt_password_protobuf(rsa_params)
        rsa_timestamp = rsa_params.timestamp
        auth_session = self._begin_auth_session_protobuf(
            encrypted_password=encrypted_password,
            rsa_timestamp=rsa_timestamp,
        )
        if auth_session.allowed_confirmations:
            if self._is_twofactor_required_protobuf(auth_session.allowed_confirmations[0]):
                if self.shared_secret == '' and self.func_2fa_input is not None:
                    self.one_time_code = self.func_2fa_input()
                else:
                    self.one_time_code = guard.generate_one_time_code(self.shared_secret)
                if self.one_time_code == 'ok':
                    self._update_auth_session_protobuf(
                        client_id=auth_session.client_id,
                        steamid=auth_session.steamid,
                        code_type=EAuthSessionGuardType.k_EAuthSessionGuardType_DeviceConfirmation,
                    )
                else:
                    self._update_auth_session_protobuf(
                        client_id=auth_session.client_id,
                        steamid=auth_session.steamid,
                        code_type=EAuthSessionGuardType.k_EAuthSessionGuardType_DeviceCode,
                    )
            if self._is_email_auth_required_protobuf(auth_session.allowed_confirmations[0]):
                if self.get_email_on_time_code_func is not None:
                    self.email_auth_code = self.get_email_on_time_code_func()
                    self._update_auth_session_protobuf(
                        client_id=auth_session.client_id,
                        steamid=auth_session.steamid,
                        code_type=EAuthSessionGuardType.k_EAuthSessionGuardType_EmailCode,
                    )
                else:
                    raise SteamError(901, 'Email auth code is required, '
                                     'but no email auth code provided')
        session = self._poll_auth_session_status_protobuf(
            client_id=auth_session.client_id,
            request_id=auth_session.request_id,
        )
        tokens = self._finalize_login_protobuf(
            refresh_token=session.refresh_token,
            sessionid=self.session.cookies.get_dict()['sessionid'],
        )
        for token in tokens.transfer_info:
            self._set_token_protobuf(
                url=token.url,
                nonce=token.params.nonce,
                auth=token.params.auth,
                steamid=auth_session.steamid,
            )

    def _refresh_cookies_with_refresh_token(self, steamid: str, refresh_token: str):
        post_data = {
            'steamid': steamid,
            'refresh_token': refresh_token
        }
        resp = self._api_call('POST', 'IAuthenticationService', 'GenerateAccessTokenForApp', 'v1', post_data)
        resp_json = resp.json()
        if 'response' in resp_json and 'access_token' in resp_json['response']:
            access_token = resp_json['response']['access_token']
            steam_login_secure = (steamid + '%7C%7C' +
                                  access_token)
            for domain_name in ['steamcommunity.com']:
                self.session.cookies.set('sessionid',
                                         self.session.cookies.get_dict(domain_name)['sessionid'],
                                         domain=domain_name)
                self.session.cookies.set('steamLoginSecure', steam_login_secure, domain=domain_name)

    def _set_token_protobuf(self, url: str, nonce: str, auth: str, steamid: int) -> None:
        data = {
            'steamID': steamid,
            'auth': auth,
            'nonce': nonce
        }
        resp = self.session.post(url, data=data, allow_redirects=False)
        while resp.status_code == 302:
            resp = self.session.post(resp.headers['Location'], allow_redirects=False)

    def _finalize_login_protobuf(self, refresh_token: str, sessionid: str) -> FinalizeLoginStatus:
        response = self.session.post(
            url='https://login.steampowered.com/jwt/finalizelogin',
            data={
                'nonce': refresh_token,
                'sessionid': sessionid,
                'redir': 'https://steamcommunity.com/login/home/?goto='
            })

        response_data = json.loads(response.content)

        transfer_info_items = [
            TransferInfoItem(
                url=item['url'],
                params=Params(
                    nonce=item['params']['nonce'],
                    auth=item['params']['auth']
                )
            ) for item in response_data['transfer_info']
        ]

        finalize_login_status = FinalizeLoginStatus(
            steamID=response_data['steamID'],
            redir=response_data['redir'],
            transfer_info=transfer_info_items,
            primary_domain=response_data['primary_domain']
        )

        return finalize_login_status

    def _poll_auth_session_status_protobuf(
            self,
            client_id: int,
            request_id: bytes,
    ) -> CAuthentication_PollAuthSessionStatus_Response:
        message = CAuthentication_PollAuthSessionStatus_Request(
            client_id=client_id,
            request_id=request_id,
        )
        response = self._api_call('POST', 'IAuthenticationService', 'PollAuthSessionStatus', 'v1',
                                  {'input_protobuf_encoded': str(base64.b64encode(message.SerializeToString()),
                                                                 'utf8')})
        return CAuthentication_PollAuthSessionStatus_Response.FromString(response.content)

    def _update_auth_session_protobuf(
            self,
            client_id: int,
            steamid: int,
            code_type: int,
    ) -> Response:
        if code_type != k_EAuthSessionGuardType_EmailCode:
            message = CAuthentication_UpdateAuthSessionWithSteamGuardCode_Request(
                client_id=client_id,
                steamid=steamid,
                code=self.one_time_code,
                code_type=code_type,
            )
        else:
            message = CAuthentication_UpdateAuthSessionWithSteamGuardCode_Request(
                client_id=client_id,
                steamid=steamid,
                code=self.email_auth_code,
                code_type=code_type,
            )
        resp = self._api_call('POST', 'IAuthenticationService', 'UpdateAuthSessionWithSteamGuardCode', 'v1',
                              {'input_protobuf_encoded': str(base64.b64encode(message.SerializeToString()), 'utf8')},
                              ignore_error_num=[29])
        return resp

    def _is_twofactor_required_protobuf(self, confirmation: CAuthentication_AllowedConfirmation) -> bool:
        return confirmation.confirmation_type == EAuthSessionGuardType.k_EAuthSessionGuardType_DeviceCode

    def _is_email_auth_required_protobuf(self, confirmation: CAuthentication_AllowedConfirmation) -> bool:
        return confirmation.confirmation_type == EAuthSessionGuardType.k_EAuthSessionGuardType_EmailCode

    def _begin_auth_session_protobuf(
            self,
            encrypted_password: str,
            rsa_timestamp: int,
    ) -> CAuthentication_BeginAuthSessionViaCredentials_Response:
        message = CAuthentication_BeginAuthSessionViaCredentials_Request(
            account_name=self.username,
            encrypted_password=encrypted_password,
            encryption_timestamp=rsa_timestamp,
            remember_login=True,
            platform_type=EAuthTokenPlatformType.k_EAuthTokenPlatformType_MobileApp,
            website_id='Community',
            persistence=ESessionPersistence.k_ESessionPersistence_Persistent,
            device_friendly_name='Mozilla/5.0 (X11; Linux x86_64; rv:1.9.5.20) Gecko/2812-12-10 04:56:28 Firefox/3.8',
        )
        response = self._api_call('POST', 'IAuthenticationService', 'BeginAuthSessionViaCredentials', 'v1',
                                  {'input_protobuf_encoded': str(base64.b64encode(message.SerializeToString()),
                                                                 'utf8')})
        return CAuthentication_BeginAuthSessionViaCredentials_Response.FromString(response.content)

    def _fetch_rsa_params_protobuf(self) -> CAuthentication_GetPasswordRSAPublicKey_Response:
        self.session.get(SteamUrl.COMMUNITY_URL)
        rsa_params = self._fetch_rsa_params_protobuf_api_call()
        return rsa_params

    def _fetch_rsa_params_protobuf_api_call(self) -> CAuthentication_GetPasswordRSAPublicKey_Response:
        message = CAuthentication_GetPasswordRSAPublicKey_Request(
            account_name=self.username
        )
        response = self._api_call('GET', 'IAuthenticationService', 'GetPasswordRSAPublicKey', 'v1',
                                  {'input_protobuf_encoded': str(base64.b64encode(message.SerializeToString()),
                                                                 'utf8')})
        return CAuthentication_GetPasswordRSAPublicKey_Response.FromString(response.content)

    def _encrypt_password_protobuf(self, rsa_params: CAuthentication_GetPasswordRSAPublicKey_Response) -> str:
        publickey_exp = int(rsa_params.publickey_exp, 16)  # type:ignore
        publickey_mod = int(rsa_params.publickey_mod, 16)  # type:ignore
        public_key = rsa.PublicKey(
            n=publickey_mod,
            e=publickey_exp,
        )
        encrypted_password = rsa.encrypt(
            message=self.password.encode('ascii'),
            pub_key=public_key,
        )
        return str(base64.b64encode(encrypted_password), 'utf8')

    def set_sessionid_cookies(self):
        sessionid = self.session.cookies.get_dict()['sessionid']
        community_domain = SteamUrl.COMMUNITY_URL[8:]
        store_domain = SteamUrl.STORE_URL[8:]
        community_cookie = self._create_session_id_cookie(sessionid, community_domain)
        store_cookie = self._create_session_id_cookie(sessionid, store_domain)
        self.session.cookies.set(**community_cookie)
        self.session.cookies.set(**store_cookie)

    @staticmethod
    def _create_session_id_cookie(sessionid: str, domain: str) -> dict:
        return {"name": "sessionid",
                "value": sessionid,
                "domain": domain}

    def _fetch_rsa_params(self, current_number_of_repetitions: int = 0) -> dict:
        request_data = {'account_name': self.username}
        response = self._api_call('GET', 'IAuthenticationService', 'GetPasswordRSAPublicKey', params=request_data)

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
            'password': encrypted_password,
            'username': self.username,
            'twofactorcode': self.one_time_code,
            'emailauth': self.email_auth_code,
            'loginfriendlyname': '',
            'captchagid': '-1',
            'captcha_text': '',
            'emailsteamid': '',
            'rsatimestamp': rsa_timestamp,
            'remember_login': 'true',
            'donotcache': str(int(time() * 1000))
        }

    @staticmethod
    def _check_for_captcha(login_response: Response) -> None:
        if login_response.json() is None:
            raise EmptyResponse('Login response is empty')
        if login_response.json().get('captcha_needed', False):
            raise CaptchaRequired('Captcha required')

    def _enter_steam_guard_and_email_auth_if_necessary(self, login_response: Response) -> Response:
        if 'requires_twofactor' in login_response.json() and login_response.json()['requires_twofactor']:
            self.one_time_code = guard.generate_one_time_code(self.shared_secret)
            return self._send_login_request()
        elif 'emailauth_needed' in login_response.json() and login_response.json()['emailauth_needed']:
            self.email_auth_code = self.get_email_on_time_code_func()
            return self._send_login_request()
        return login_response

    @staticmethod
    def _assert_valid_credentials(login_response: Response) -> None:
        if not login_response.json()['success']:
            raise InvalidCredentials(login_response.json()['message'])

    def _perform_redirects(self, response_dict: dict) -> None:
        parameters = response_dict.get('transfer_parameters')
        if parameters is None:
            raise Exception('Cannot perform redirects after login, no parameters fetched')
        for url in response_dict['transfer_urls']:
            self.session.post(url, parameters)

    def _fetch_home_page(self, session: Session) -> Response:
        return session.post(SteamUrl.COMMUNITY_URL + '/my/home/')
