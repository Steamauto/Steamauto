from typing import Optional, Union

from pydantic import BaseModel

from utils.models import LeaseAsset


class UUOnLeaseShelfItem(BaseModel):
    AssetId: int
    IsCanLease: bool = True
    IsCanSold: bool = False
    LeaseDeposit: str
    LeaseMaxDays: int
    LeaseUnitPrice: float
    LongLeaseUnitPrice: Union[float, None] = None
    
    @classmethod
    def fromLeaseAsset(cls, leaseAsset: LeaseAsset):
        return cls(
            AssetId=int(leaseAsset.assetid),
            LeaseDeposit=str(leaseAsset.LeaseDeposit),
            LeaseMaxDays=leaseAsset.LeaseMaxDays,
            LeaseUnitPrice=leaseAsset.LeaseUnitPrice,
            LongLeaseUnitPrice=leaseAsset.LongLeaseUnitPrice
        )


class UUMarketLeaseItem(BaseModel):
    LeaseDeposit: Optional[str] = None
    LeaseUnitPrice: Optional[float] = None
    LongLeaseUnitPrice: Optional[float] = None
    CommodityName: Optional[str] = None