import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from BuffApi import BuffAccount, BuffLoginRequired, get_authenticated_data
from plugins.BuffAutoAcceptOffer import BuffAutoAcceptOffer
from utils import buff_helper


def response(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    return result


class BuffAccountStateTest(unittest.TestCase):
    def test_explicit_login_failure(self):
        for result in (response({"code": "Login Required"}), response({"code": "Login Required"}, 403), response({}, 401)):
            with self.subTest(status=result.status_code):
                with self.assertRaises(BuffLoginRequired):
                    get_authenticated_data(result)

    def test_temporary_errors_are_not_login_failures(self):
        results = [
            response({}, 429),
            response({}, 503),
            response({}, 403),
            response({"code": "Login Required"}, 503),
            response({"code": "Server Error"}),
            response({"code": "OK"}),
            response([]),
        ]
        for result in results:
            with self.subTest(status=result.status_code, payload=result.text):
                with self.assertRaises((requests.RequestException, ValueError)):
                    get_authenticated_data(result)

    def test_both_account_checks_preserve_login_failure(self):
        account = object.__new__(BuffAccount)
        account.get = MagicMock(return_value=response({"code": "Login Required"}))
        for check in (account.get_user_nickname, account.get_steam_trade):
            with self.assertRaises(BuffLoginRequired):
                check()


class BuffSessionCacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cookie_file = Path(self.temp.name) / "cookies.txt"
        self.cookie_file.write_text("session=valid-cookie", encoding="utf-8")
        self.client = MagicMock(username="seller")
        self.logger = MagicMock()
        self.path_patch = patch.object(buff_helper, "BUFF_COOKIES_FILE_PATH", str(self.cookie_file))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    @patch.object(buff_helper, "login_to_buff_by_qrcode")
    @patch.object(buff_helper, "login_to_buff_by_steam")
    @patch.object(buff_helper.requests, "get")
    def test_timeout_keeps_cache_and_does_not_relogin(self, get, steam_login, qr_login):
        get.side_effect = requests.ReadTimeout("read timed out")
        with self.assertRaises(requests.ReadTimeout):
            buff_helper.get_valid_session_for_buff(self.client, self.logger)
        self.assertEqual(self.cookie_file.read_text(), "session=valid-cookie")
        steam_login.assert_not_called()
        qr_login.assert_not_called()
        self.assertEqual(get.call_args.kwargs["timeout"], buff_helper.BUFF_REQUEST_TIMEOUT)

    @patch.object(buff_helper, "login_to_buff_by_qrcode")
    @patch.object(buff_helper, "login_to_buff_by_steam")
    @patch.object(buff_helper.requests, "get")
    def test_empty_trade_list_is_valid_and_proxy_is_preserved(self, get, steam_login, qr_login):
        get.return_value = response({"code": "OK", "data": []})
        proxies = {"https": "http://proxy.invalid:8080"}
        session = buff_helper.get_valid_session_for_buff(self.client, self.logger, proxies)
        self.assertEqual(session, "session=valid-cookie")
        self.assertEqual(get.call_args.kwargs["proxies"], proxies)
        steam_login.assert_not_called()
        qr_login.assert_not_called()

    @patch.object(buff_helper, "send_notification")
    @patch.object(buff_helper, "login_to_buff_by_qrcode", return_value="")
    @patch.object(buff_helper, "login_to_buff_by_steam", return_value="")
    @patch.object(buff_helper.requests, "get")
    def test_failed_login_returns_empty_string_and_preserves_file(self, get, steam_login, qr_login, notify):
        get.return_value = response({"code": "Login Required"})
        self.assertEqual(buff_helper.get_valid_session_for_buff(self.client, self.logger), "")
        steam_login.assert_called_once()
        qr_login.assert_called_once()
        self.assertEqual(self.cookie_file.read_text(), "session=valid-cookie")

    @patch.object(buff_helper, "send_notification")
    @patch.object(buff_helper, "login_to_buff_by_qrcode", return_value="unverified-cookie")
    @patch.object(buff_helper, "login_to_buff_by_steam", return_value="")
    @patch.object(buff_helper.requests, "get")
    def test_invalid_qr_cookie_is_not_saved(self, get, steam_login, qr_login, notify):
        get.return_value = response({"code": "Login Required"})
        self.assertEqual(buff_helper.get_valid_session_for_buff(self.client, self.logger), "")
        self.assertEqual(self.cookie_file.read_text(), "session=valid-cookie")


class StopPolling(BaseException):
    pass


class BuffPollingRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.plugin = BuffAutoAcceptOffer(MagicMock(), MagicMock(), {"buff_auto_accept_offer": {"interval": 300}})
        self.plugin.logger = MagicMock()
        self.account = self.plugin.buff_account = MagicMock()
        self.account.get_user_info.return_value = {"nickname": "seller", "force_buyer_send_offer": True}
        self.account.get_notification.return_value = {"to_deliver_order": {"csgo": 1}, "to_confirm_sell": {}}
        self.account.get_sell_order_to_deliver.return_value = {}
        self.trade = {"tradeofferid": "123", "type": 3, "goods_infos": {}}

    def run_two_polls(self):
        waits = []

        def sleep(seconds):
            if seconds == 300:
                waits.append(seconds)
                if len(waits) == 2:
                    raise StopPolling()

        with patch("plugins.BuffAutoAcceptOffer.time.sleep", side_effect=sleep):
            with self.assertRaises(StopPolling):
                self.plugin.exec()
        self.assertEqual(waits, [300, 300])

    @patch("plugins.BuffAutoAcceptOffer.handle_caught_exception")
    @patch("plugins.BuffAutoAcceptOffer.accept_trade_offer", return_value=True)
    @patch("plugins.BuffAutoAcceptOffer.get_valid_session_for_buff")
    def test_network_recovers_and_delivery_resumes_without_login(self, login, accept, caught):
        for failure in (requests.ReadTimeout("read timed out"), requests.ConnectionError("disconnected")):
            with self.subTest(failure=type(failure).__name__):
                self.account.get_user_nickname.side_effect = [failure, "seller"]
                self.account.get_steam_trade.return_value = [self.trade]
                self.run_two_polls()
                login.assert_not_called()
                accept.assert_called_once()
                self.assertEqual(accept.call_args.args[2], "123")
                accept.reset_mock()

    @patch("plugins.BuffAutoAcceptOffer.handle_caught_exception")
    @patch("plugins.BuffAutoAcceptOffer.accept_trade_offer", return_value=True)
    @patch("plugins.BuffAutoAcceptOffer.get_valid_session_for_buff", return_value="")
    def test_failed_relogin_does_not_stop_polling(self, login, accept, caught):
        self.account.get_user_nickname.side_effect = [BuffLoginRequired(), "seller"]
        self.account.get_steam_trade.return_value = [self.trade]
        self.run_two_polls()
        login.assert_called_once()
        accept.assert_called_once()

    def test_trade_endpoint_timeout_is_not_login_failure(self):
        self.account.get_user_nickname.return_value = "seller"
        self.account.get_steam_trade.side_effect = requests.ReadTimeout()
        with self.assertRaises(requests.ReadTimeout):
            self.plugin.check_buff_account_state()

    def test_trade_endpoint_login_failure_triggers_relogin(self):
        self.account.get_user_nickname.return_value = "seller"
        self.account.get_steam_trade.side_effect = BuffLoginRequired()
        self.assertEqual(self.plugin.check_buff_account_state(), "")


class BuffLoginTimeoutTest(unittest.TestCase):
    @patch.object(buff_helper, "send_notification")
    @patch.object(buff_helper.qrcode, "make")
    @patch.object(buff_helper.qrcode_terminal, "draw")
    @patch.object(buff_helper.time, "sleep")
    @patch.object(buff_helper.time, "monotonic", side_effect=[0, 0, 181])
    @patch.object(buff_helper.requests, "session")
    def test_qr_wait_is_bounded(self, session_factory, monotonic, sleep, draw, make, notify):
        session = session_factory.return_value
        session.get.side_effect = [response({"code": "OK"}), response({"code": "OK", "data": {"state": 1}})]
        session.post.return_value = response({"code": "OK", "data": {"code_id": "id", "url": "https://example.invalid/qr"}})
        self.assertEqual(buff_helper.login_to_buff_by_qrcode(MagicMock()), "")
        for call in session.get.call_args_list + session.post.call_args_list:
            self.assertEqual(call.kwargs["timeout"], buff_helper.BUFF_REQUEST_TIMEOUT)

    @patch.object(buff_helper, "get_openid_params")
    def test_steam_redirect_loop_is_bounded(self, get_params):
        client = MagicMock()
        session = MagicMock()
        redirect = response({}, 302)
        redirect.headers["Location"] = "https://buff.163.com/redirect"
        client._session.post.return_value = redirect
        session.get.return_value = redirect
        get_params.return_value = ({"action": "login"}, "https://steamcommunity.com/openid/login", session)
        with self.assertRaises(requests.TooManyRedirects):
            buff_helper.login_to_buff_by_steam(client)
        self.assertEqual(session.get.call_count, 10)
        self.assertEqual(client._session.post.call_args.kwargs["timeout"], buff_helper.BUFF_REQUEST_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
