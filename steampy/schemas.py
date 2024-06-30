from typing import List, TypedDict


class Params:
    def __init__(self, nonce: str, auth: str):
        self.nonce = nonce
        self.auth = auth


class TransferInfoItem:
    def __init__(self, url: str, params: Params):
        self.url = url
        self.params = params


class FinalizeLoginStatus:
    def __init__(self, steamID: str, redir: str, transfer_info: List[TransferInfoItem], primary_domain: str):
        self.steamID = steamID
        self.redir = redir
        self.transfer_info = transfer_info
        self.primary_domain = primary_domain


class AccountRecoveryParams(TypedDict):
    s: int
    account: int
    reset: int
    issueid: int
    lost: int


class RSAKey(TypedDict):
    mod: str
    exp: str
    timestamp: int
