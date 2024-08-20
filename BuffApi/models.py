from typing import Union

from pydantic import BaseModel

class BuffOnSaleAsset(BaseModel):
    assetid: str
    appid: int = 730
    classid: int
    instanceid: int
    contextid: int = 2
    market_hash_name: str
    orderNo: str
    price: float
    income: float
    desc: str = ''