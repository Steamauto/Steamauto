from typing import Union

from pydantic import BaseModel


class RentAsset(BaseModel):
    StockId: str
    AssetId: str
    SteamGameId: str
    RentMaxDay: int
    RentPrice: float
    LongRentPrice: float
    RentDeposits: float
    RentDescription: Union[str, None] = None


class GoodsNum(BaseModel):
    # GoodsNum和AssetId二选一
    GoodsNum: Union[str, None] = None
    AssetId: Union[str, None] = None
    SteamGameId: str = '730'

class ECOPublishStockAsset(BaseModel):
    AssetId: str
    Price:float
    Description: Union[str, None] = None
    SteamGameId: str = '730'
    
    @classmethod
    def from_dict(cls,obj):
        return cls(AssetId=obj["assetid"],Price=obj["price"],Description=obj.get('Description',''),SteamGameId=obj['appid'])