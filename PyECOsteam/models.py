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

class goodsNum(BaseModel):
    # GoodsNum和AssetId二选一
    GoodsNum: Union[str, None] = None
    AssetId: Union[str, None] = None
    SteamGameId: str = '730'