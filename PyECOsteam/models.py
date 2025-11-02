from typing import Union

from pydantic import BaseModel

from utils.models import Asset, LeaseAsset


class ECORentAsset(BaseModel):
    """
    StockId和AssetId二选一
    """

    SteamGameId: str = "730"
    StockId: Union[str, None] = None
    AssetId: Union[str, None] = None
    TradeTypes: list[int] = [2]
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
    SteamGameId: str = "730"


class ECOPublishStockAsset(BaseModel):
    AssetId: str
    SellPrice: float
    TradeTypes: list[int] = [1]
    Description: Union[str, None] = None
    SteamGameId: str = "730"

    @classmethod
    def fromAsset(cls, obj: Asset):
        return cls(AssetId=obj.assetid, SellPrice=float(obj.price), Description="", SteamGameId=str(obj.appid))  # type: ignore
