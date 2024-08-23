import json
from typing import Union

from pydantic import BaseModel


class Asset(BaseModel):
    assetid: str
    appid: Union[str, int, None] = '730'
    classid: Union[str, int, None] = None
    instanceid: Union[str, int, None] = None
    contextid: Union[int, str, None] = 2
    market_hash_name: Union[str, int, None] = None
    orderNo: Union[str, int, None] = None
    price: Union[float, int, None] = None

class LeaseAsset(Asset):
    price: Union[float, None] = None
    IsCanLease: bool = True
    IsCanSold: bool = False
    LeaseDeposit: float
    LeaseMaxDays: int
    LeaseUnitPrice: float
    LongLeaseUnitPrice: Union[float, None] = None
    orderNo: Union[str, int, None] = None


class ModelEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (Asset, LeaseAsset)):
            return obj.model_dump(exclude_none=True)
        return json.JSONEncoder.default(self, obj)
