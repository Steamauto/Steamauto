"""跨平台统一操作门面（platforms）。

把 BUFF / 悠悠有品 / ECOsteam 的同类「平台操作」统一成一套接口，供 plugins 复用，
消除各插件里重复的「if platform 分支调 SDK」逻辑。

统一返回结构（dict 列表，字段语义跨平台一致）：
- 在售挂单 get_on_sale:    ``[{'order_no', 'assetid', 'name', 'price'}]``
- 售出订单 get_sold_orders: ``[{'order_id', 'assetid', 'name'}]``（assetid 可能为 None）
- 下架 off_shelf:           ``(success_count, failure_count)``

只依赖 api/ 下各平台 SDK，不依赖 utils/（避免循环依赖）。
"""

import datetime
import time

PLATFORMS = ("buff", "uu", "eco")


def _require(client, platform):
    if client is None:
        raise ValueError(f"{platform} 客户端未初始化")
    return client


def get_on_sale(buff, uu, eco, platform, steam_id=None):
    """获取平台在售挂单，返回统一结构 ``[{'order_no', 'assetid', 'name', 'price'}]``。"""
    if platform == "buff":
        client = _require(buff, platform)
        data = client.get_on_sale().json()["data"]
        items = data["items"]
        if data["total_count"] > 500:
            items += client.get_on_sale(page_num=2).json()["data"]["items"]
        goods_infos = data.get("goods_infos", {})
        return [
            {
                "order_no": item["id"],
                "assetid": str(item["asset_info"]["assetid"]),
                "name": (goods_infos.get(str(item.get("goods_id", ""))) or {}).get("market_hash_name", ""),
                "price": float(item.get("price", 0)),
            }
            for item in items
        ]
    if platform == "uu":
        client = _require(uu, platform)
        return [
            {
                "order_no": item["id"],
                "assetid": str(item["steamAssetId"]),
                "name": item.get("name", ""),
                "price": float(item.get("sellAmount", 0)),
            }
            for item in client.get_sell_list()
        ]
    if platform == "eco":
        client = _require(eco, platform)
        return [
            {
                "order_no": item["GoodsNum"],
                "assetid": str(item["AssetId"]),
                "name": item.get("GoodsName", ""),
                "price": float(item.get("Price", 0)),
            }
            for item in client.getFullSellGoodsList(steam_id)
        ]
    raise ValueError(f"不支持的平台：{platform}")


def off_shelf(buff, uu, eco, platform, order_nos):
    """在指定平台下架一批挂单，返回 ``(success_count, failure_count)``。"""
    if not order_nos:
        return 0, 0
    if platform == "buff":
        client = _require(buff, platform)
        success, problems = client.cancel_sale(list(order_nos))
        return success, len(problems)
    if platform == "uu":
        client = _require(uu, platform)
        rsp = client.off_shelf([str(o) for o in order_nos]).json()
        if int(rsp.get("Code", -1)) == 0:
            return len(order_nos), 0
        return 0, len(order_nos)
    if platform == "eco":
        from api.PyECOsteam import models as eco_models

        client = _require(eco, platform)
        success_count, failure_count = client.OffshelfGoods(
            [eco_models.GoodsNum(GoodsNum=o, SteamGameId="730") for o in order_nos]
        )
        return success_count, failure_count
    raise ValueError(f"不支持的平台：{platform}")


def get_sold_orders(buff, uu, eco, platform, steam_id=None):
    """获取平台已售出（待发货）订单，返回统一结构 ``[{'order_id', 'assetid', 'name'}]``。

    - buff: 直接含 assetid（items_to_trade），按 assetid 精确匹配
    - uu: 只含饰品名（assetid 为 None），分页拉取
    - eco: 只含饰品名（assetid 为 None），近 30 天订单
    """
    if platform == "buff":
        client = _require(buff, platform)
        data = client.get_sell_order_to_deliver("csgo", 730)
        goods_infos = (data or {}).get("goods_infos", {})
        result = []
        for trade in (data or {}).get("items", []):
            order_id = str(trade.get("id"))
            goods_id = str(trade.get("goods_id", ""))
            name = (goods_infos.get(goods_id) or {}).get("market_hash_name", "") if goods_id in goods_infos else ""
            assetids = []
            for x in trade.get("items_to_trade") or []:
                aid = x.get("assetid") or x.get("id") if isinstance(x, dict) else x
                if aid:
                    assetids.append(str(aid))
            if assetids:
                for aid in assetids:
                    result.append({"order_id": order_id, "assetid": aid, "name": name})
            else:
                # 拿不到 assetid 时退化为按名称匹配
                result.append({"order_id": order_id, "assetid": None, "name": name})
        return result
    if platform == "uu":
        client = _require(uu, platform)
        result = []
        page_index, page_size = 1, 20
        while True:
            order_list = client.get_sold_order_list(orderStatus="140", pageIndex=page_index, pageSize=page_size)
            for order in order_list:
                name = (order.get("productDetail") or {}).get("commodityName", "")
                result.append({"order_id": str(order.get("orderNo")), "assetid": None, "name": name})
            if len(order_list) == page_size:
                page_index += 1
                time.sleep(0.5)
                continue
            break
        return result
    if platform == "eco":
        client = _require(eco, platform)
        today = datetime.datetime.today()
        last_month = (today - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        tomorrow = (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        orders = client.getFullSellerOrderList(last_month, tomorrow, DetailsState=8, SteamId=steam_id)
        return [
            {"order_id": str(order.get("OrderNum")), "assetid": None, "name": order.get("GoodsName", "")}
            for order in (orders or [])
        ]
    raise ValueError(f"不支持的平台：{platform}")
