from typing import Union

from pydantic import BaseModel

from utils.models import Asset, LeaseAsset


class RentAsset(BaseModel):
    '''
    StockId和AssetId二选一
    '''
    SteamGameId: str = '730'
    StockId: Union[str, None] = None
    AssetId: Union[str, None] = None
    RentMaxDay: int
    RentPrice: float
    LongRentPrice: Union[float, None] = None
    RentDeposits: float
    RentDescription: Union[str, None] = None

    @classmethod
    def fromLeaseAsset(cls, obj: LeaseAsset):
        return cls(
            SteamGameId=str(obj.appid),
            AssetId=obj.assetid,
            RentMaxDay=obj.LeaseMaxDays,
            RentPrice=obj.LeaseUnitPrice,
            LongRentPrice=obj.LongLeaseUnitPrice,
            RentDeposits=obj.LeaseDeposit,
        )


class GoodsNum(BaseModel):
    # GoodsNum和AssetId二选一
    GoodsNum: Union[str, None] = None
    AssetId: Union[str, None] = None
    SteamGameId: str = '730'


class ECOPublishStockAsset(BaseModel):
    AssetId: str
    Price: float
    Description: Union[str, None] = None
    SteamGameId: str = '730'

    @classmethod
    def from_Asset(cls, obj: Asset):
        return cls(AssetId=obj.assetid, Price=float(obj.price), Description='', SteamGameId=str(obj.appid))  # type: ignore
