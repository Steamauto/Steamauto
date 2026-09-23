from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class SteamDTQueryParams:
    page: int = 1
    page_size: int = 200
    type: str = "swap"
    want_to_get: str = "STEAM_BALANCE"
    purchase_plan: str = ""
    sale_plan: str = "STEAM_SELL_PRICE"
    min_sell_price: str = "2"
    max_sell_price: int = 5000
    min_transaction_count: str = "200"
    platform_list: List[str] = field(default_factory=lambda: ["BUFF"])
    currency: str = "CNY"
    language: str = "zh_CN"

    def to_payload(self) -> dict:
        return {
            "page": self.page,
            "pageSize": self.page_size,
            "type": self.type,
            "wantToGet": self.want_to_get,
            "purchasePlan": self.purchase_plan,
            "salePlan": self.sale_plan,
            "minSellPrice": self.min_sell_price,
            "maxSellPrice": self.max_sell_price,
            "minTransactionCount": self.min_transaction_count,
            "platformList": list(self.platform_list),
        }


@dataclass
class SteamDTRow:
    index: str = ""
    name: str = ""            
    volume: str = "0"         
    min_price: str = "0"     
    sell_ratio: str = "0"     
    buy_ratio: str = "0"      
    safe_buy_ratio: str = "0" 
    recent_ratio: str = "0"   
    platform: str = ""        
    steam_link: str = ""      
    update_time: str = ""     

    name_cn: str = ""         
    steam_price: float = 0.0  
    profit_amount: float = 0.0
