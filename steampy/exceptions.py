from typing import Optional

from steampy.steam_error_codes import STEAM_ERROR_CODES


class SevenDaysHoldException(Exception):
    pass


class TooManyRequests(Exception):
    pass


class ApiException(Exception):
    pass


class LoginRequired(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class CaptchaRequired(Exception):
    pass


class EmptyResponse(Exception):
    pass


class ConfirmationExpected(Exception):
    pass


class InvalidResponse(Exception):
    pass


class SteamLoginError(Exception):
    """携带登录失败阶段及 Steam 响应信息的内部异常。"""

    def __init__(
        self,
        stage: str,
        *,
        eresult: Optional[int] = None,
        http_status: Optional[int] = None,
        detail: Optional[str] = None,
        response=None,
    ):
        self.stage = stage
        self.eresult = eresult
        self.http_status = http_status
        self.detail = detail
        self.response = response
        parts = [f"stage={stage}"]
        if eresult is not None:
            parts.append(f"eresult={eresult}")
        if http_status is not None:
            parts.append(f"http_status={http_status}")
        if detail:
            parts.append(f"detail={detail}")
        super().__init__(", ".join(parts))


class SteamError(Exception):
    def __init__(self, error_code: int, error_msg: Optional[str] = None):
        self.error_code = error_code
        self.error_msg = error_msg

    def __str__(self) -> str:
        return str(
            {
                "error": STEAM_ERROR_CODES.get(self.error_code, self.error_code),
                "msg": self.error_msg,
                "code": self.error_code,
            }
        )


class ErrorSteamPasswordChange(Exception): ...


class ErrorSteamEmailChange(Exception): ...


class SendOfferError(Exception):
    """Error sending exchange."""


class SteamServerDownError(SendOfferError):
    """Steam servers may be down."""


class TradeOffersLimitError(SendOfferError):
    """Trade offers limit."""


class AccountOverflowError(SendOfferError):
    """Account overflow."""


class TradeBanError(SendOfferError):
    """Account have a trade ban."""


class ProfileSettingsError(SendOfferError):
    """Incorrect profile settings."""


class TradelinkError(SendOfferError):
    """Tradelink may be incorrect."""


class MobileConfirmationError(Exception):
    """Base mobile confirmation error."""


class NotFoundMobileConfirmationError(MobileConfirmationError):
    """No offer found pending mobile confirmation."""


class InvalidAuthenticatorError(MobileConfirmationError):
    """Invalid authenticator."""


class InvalidConfirmationPageError(MobileConfirmationError):
    """Invalid confirmation page."""
