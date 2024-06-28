from typing import List

import pydantic
from pydantic import BaseModel, Field


class Params(BaseModel):
    nonce: str
    auth: str


class TransferInfoItem(BaseModel):
    url: str
    params: Params


class FinalizeLoginStatus(BaseModel):
    steamID: str
    redir: str
    transfer_info: List[TransferInfoItem]
    primary_domain: str


class AccountRecoveryParams(pydantic.BaseModel):
    s: int
    account: int
    reset: int
    issueid: int
    lost: int = 0


class RSAKey(pydantic.BaseModel):
    mod: str
    exp: str
    timestamp: int

    class Config:
        mod: Field = 'publickey_mod'
        exp: Field = 'publickey_exp'
