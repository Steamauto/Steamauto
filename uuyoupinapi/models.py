from typing import Optional, Union

from pydantic import BaseModel


from utils.models import LeaseAsset,Asset


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

class UUOnSellShelfItem(BaseModel):
    AssetId: int
    IsCanLease: bool = False
    IsCanSold: bool = True
    Price: float
    
    @classmethod
    def fromAsset(cls, asset: Asset):
        return cls(
            AssetId=int(asset.assetid),
            Price=asset.price
        )

class UUChangePriceItem(BaseModel):
    CommodityId: int
    IsCanLease: bool = False
    IsCanSold: bool = False
    LeaseDeposit: Union[str, None] = None
    LeaseMaxDays: Union[int, None] = None
    LeaseUnitPrice: Union[float, None] = None
    LongLeaseUnitPrice: Union[float, None] = None
    Price: Union[float, None] = None
    
    @classmethod 
    def fromAsset(cls, asset: Asset):
        return cls(
            CommodityId=int(asset.orderNo), # type: ignore
            Price=asset.price,
            IsCanLease=False,
            IsCanSold=True,
        )
    
    @classmethod
    def fromLeaseAsset(cls, leaseAsset: LeaseAsset):
        return cls(
            CommodityId=int(leaseAsset.orderNo), # type: ignore
            LeaseDeposit=str(leaseAsset.LeaseDeposit),
            LeaseMaxDays=leaseAsset.LeaseMaxDays,
            LeaseUnitPrice=leaseAsset.LeaseUnitPrice,
            LongLeaseUnitPrice=leaseAsset.LongLeaseUnitPrice,
            IsCanLease=True,
            IsCanSold=False
        )