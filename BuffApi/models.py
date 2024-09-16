from typing import no_type_check

from pydantic import BaseModel

from utils.models import Asset


class BuffOnSaleAsset(BaseModel):
    assetid: str
    appid: int = 730
    classid: int
    instanceid: int
    contextid: int = 2
    market_hash_name: str
    price: float
    desc: str = ''
    
    @classmethod
    @no_type_check
    def from_Asset(cls,obj:Asset) -> 'BuffOnSaleAsset':
        return cls(
            assetid=obj.assetid,
            classid=obj.classid,
            instanceid=obj.instanceid,
            market_hash_name=obj.market_hash_name,
            price=obj.price,
            desc=''
        )