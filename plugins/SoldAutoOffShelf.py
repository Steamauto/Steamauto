"""跨平台售出自动下架插件（SoldAutoOffShelf）。

场景：同一饰品同时挂在 BUFF / 悠悠有品 / ECOsteam 出售，
当它在任一平台售出后，自动在其余平台下架同一饰品，避免一单多卖。

检测方式（B 为主 + A 兜底）：
- B：轮询各平台的「已售出/待发货订单」，发现新售出立即在其他平台下架。
  - BUFF：sell_order/to_deliver（直接含 assetid，按 assetid 精确匹配）
  - 悠悠有品：出售订单列表 orderStatus=140（只含饰品名，按 market_hash_name 匹配，一单下架一件）
  - ECOsteam：卖家订单 DetailsState=8（只含饰品名，按名称匹配，一单下架一件）
- A（兜底，可配置 inventory_check）：上架中的饰品若已不在 Steam 库存中
  （例如已发货完成），直接在该平台下架。

下架按数量精确处理：售出 1 件只在其他平台各下架 1 件同名/同 assetid 的挂单，
其余同名挂单继续出售。
"""

import datetime
import os
import time

from api.BuffApi import BuffAccount
from api.PyECOsteam import ECOsteamClient, models as eco_models
import api.uuyoupinapi as uuyoupinapi
from utils.buff_helper import get_valid_session_for_buff
from utils.logger import PluginLogger, handle_caught_exception
from utils.notifier import send_notification
from utils.static import ECOSTEAM_RSAKEY_FILE
from utils.steam_client import get_cs2_inventory
from utils.tools import get_encoding
from utils.uu_helper import get_valid_token_for_uu

logger = PluginLogger("SoldAutoOffShelf")

SUPPORTED_PLATFORMS = ("buff", "uu", "eco")
PLATFORM_NAMES = {"buff": "BUFF", "uu": "悠悠有品", "eco": "ECOsteam"}
MAX_RETRY = 5  # 单个售出订单下架失败的最大重试轮数


class SoldAutoOffShelf:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        self.buff_client = None
        self.uu_client = None
        self.eco_client = None
        self.steam_id = None
        # 已成功处理的售出订单 key（platform:order_id），避免重复处理
        self.handled_orders = set()
        # 处理中的订单重试计数
        self.order_retries = {}

    def init(self) -> bool:
        cfg = self.config.get("sold_auto_off_shelf", {})
        self.interval = int(cfg.get("interval", 60))
        self.inventory_check = bool(cfg.get("inventory_check", True))
        platforms = cfg.get("platforms", list(SUPPORTED_PLATFORMS))
        self.platforms = [p for p in platforms if p in SUPPORTED_PLATFORMS]
        if len(self.platforms) < 2:
            logger.error("跨平台售出自动下架至少需要启用 2 个平台（buff/uu/eco），请检查配置！插件退出。")
            return True

        self.steam_id = self.steam_client.get_steam64id_from_cookies()

        # BUFF 登录
        if "buff" in self.platforms:
            session = get_valid_session_for_buff(self.steam_client, logger)
            if session:
                self.buff_client = BuffAccount(session)
                logger.info("BUFF 登录成功")
            else:
                logger.warning("无法获取有效的 BUFF session，BUFF 平台已从自动下架中移除")
                self.platforms.remove("buff")

        # 悠悠有品登录
        if "uu" in self.platforms:
            token = get_valid_token_for_uu(self.steam_client)
            if token:
                self.uu_client = uuyoupinapi.UUAccount(token)
                logger.info("悠悠有品登录成功")
            else:
                logger.warning("无法获取有效的悠悠有品 token，悠悠有品已从自动下架中移除")
                self.platforms.remove("uu")

        # ECOsteam 登录
        if "eco" in self.platforms:
            eco_cfg = self.config.get("ecosteam", {})
            partner_id = eco_cfg.get("partnerId", "")
            if not partner_id or not os.path.exists(ECOSTEAM_RSAKEY_FILE):
                logger.warning("ECOsteam 未配置 partnerId 或 rsakey 文件不存在，ECOsteam 已从自动下架中移除")
                self.platforms.remove("eco")
            elif not self.steam_id:
                logger.warning("未登录 Steam（离线模式），无法定位 ECOsteam 绑定账号，ECOsteam 已从自动下架中移除")
                self.platforms.remove("eco")
            else:
                try:
                    with open(ECOSTEAM_RSAKEY_FILE, "r", encoding=get_encoding(ECOSTEAM_RSAKEY_FILE)) as f:
                        rsa_key = f.read()
                    self.eco_client = ECOsteamClient(partner_id, rsa_key, qps=eco_cfg.get("qps", 10))
                    user_info = self.eco_client.GetTotalMoney().json()
                    if not user_info["ResultData"].get("UserName"):
                        raise Exception("ECOsteam 登录校验失败")
                    logger.info(f"ECOsteam 登录成功，用户：{user_info['ResultData']['UserName']}")
                except Exception as e:
                    handle_caught_exception(e, "SoldAutoOffShelf", known=True)
                    logger.warning("ECOsteam 登录失败，已从自动下架中移除")
                    self.eco_client = None
                    self.platforms.remove("eco")

        if len(self.platforms) < 2:
            logger.error("可用平台不足 2 个，跨平台售出自动下架无意义，插件退出。")
            return True
        logger.info(f"跨平台售出自动下架已启用，平台：{[PLATFORM_NAMES[p] for p in self.platforms]}，轮询间隔 {self.interval} 秒")
        return False

    # ---------------- 售出检测（B） ----------------

    def get_sold_orders_buff(self) -> list:
        """返回 BUFF 已售出待发货订单：[{'order_id', 'assetid'|None, 'name'}]"""
        result = []
        data = self.buff_client.get_sell_order_to_deliver("csgo", 730)
        goods_infos = data.get("goods_infos", {}) if data else {}
        for trade in (data or {}).get("items", []):
            order_id = str(trade.get("id"))
            name = ""
            goods_id = str(trade.get("goods_id", ""))
            if goods_id in goods_infos:
                name = goods_infos[goods_id].get("market_hash_name", "")
            assetids = []
            for x in trade.get("items_to_trade") or []:
                if isinstance(x, dict):
                    aid = x.get("assetid") or x.get("id")
                else:
                    aid = x
                if aid:
                    assetids.append(str(aid))
            if assetids:
                for aid in assetids:
                    result.append({"order_id": order_id, "assetid": aid, "name": name})
            else:
                # 拿不到 assetid 时退化为按名称匹配
                result.append({"order_id": order_id, "assetid": None, "name": name})
        return result

    def get_sold_orders_uu(self) -> list:
        """返回悠悠已售出（待发货）订单：[{'order_id', 'assetid'|None, 'name'}]。只读，不发送报价。"""
        result = []
        page_index = 1
        page_size = 20
        while True:
            rsp = self.uu_client.call_api(
                "POST",
                "/api/youpin/bff/trade/sale/v1/sell/list",
                data={"keys": "", "orderStatus": "140", "pageIndex": page_index, "pageSize": page_size},
            ).json()
            order_list = (rsp.get("data") or {}).get("orderList", [])
            for order in order_list:
                name = (order.get("productDetail") or {}).get("commodityName", "")
                result.append({"order_id": str(order.get("orderNo")), "assetid": None, "name": name})
            if len(order_list) == page_size:
                page_index += 1
                time.sleep(0.5)
                continue
            break
        return result

    def get_sold_orders_eco(self) -> list:
        """返回 ECO 已售出（待发货）订单：[{'order_id', 'assetid'|None, 'name'}]"""
        result = []
        today = datetime.datetime.today()
        last_month = (today - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        tomorrow = (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        orders = self.eco_client.getFullSellerOrderList(last_month, tomorrow, DetailsState=8, SteamId=self.steam_id)
        for order in orders or []:
            result.append({"order_id": str(order.get("OrderNum")), "assetid": None, "name": order.get("GoodsName", "")})
        return result

    # ---------------- 在售货架 ----------------

    def get_shelf(self, platform) -> list:
        """返回平台在售挂单：[{'order_no', 'assetid', 'name'}]"""
        shelf = []
        if platform == "buff":
            data = self.buff_client.get_on_sale().json()["data"]
            items = data["items"]
            if data["total_count"] > 500:
                items += self.buff_client.get_on_sale(page_num=2).json()["data"]["items"]
            for item in items:
                goods_id = str(item.get("goods_id", ""))
                name = (data.get("goods_infos", {}).get(goods_id) or {}).get("market_hash_name", "")
                shelf.append(
                    {
                        "order_no": item["id"],
                        "assetid": str(item["asset_info"]["assetid"]),
                        "name": name,
                    }
                )
        elif platform == "uu":
            for item in self.uu_client.get_sell_list():
                shelf.append(
                    {
                        "order_no": item["id"],
                        "assetid": str(item["steamAssetId"]),
                        "name": item.get("name", ""),
                    }
                )
        elif platform == "eco":
            for item in self.eco_client.getFullSellGoodsList(self.steam_id):
                shelf.append(
                    {
                        "order_no": item["GoodsNum"],
                        "assetid": str(item["AssetId"]),
                        "name": item.get("GoodsName", ""),
                    }
                )
        return shelf

    # ---------------- 下架执行 ----------------

    def offshelf(self, platform, order_nos: list) -> bool:
        """在指定平台下架一批挂单，返回是否全部成功。"""
        if not order_nos:
            return True
        logger.warning(f"正在在 {PLATFORM_NAMES[platform]} 下架 {len(order_nos)} 个挂单（饰品已在其他平台售出）...")
        try:
            if platform == "buff":
                success, problems = self.buff_client.cancel_sale(order_nos)
                if problems:
                    logger.error(f"BUFF 下架部分失败：{problems}")
                return len(problems) == 0
            elif platform == "uu":
                rsp = self.uu_client.off_shelf([str(o) for o in order_nos]).json()
                if int(rsp.get("Code", -1)) == 0:
                    return True
                logger.error(f"悠悠有品下架失败：{rsp}")
                return False
            elif platform == "eco":
                success_count, failure_count = self.eco_client.OffshelfGoods(
                    [eco_models.GoodsNum(GoodsNum=o, SteamGameId="730") for o in order_nos]
                )
                if failure_count:
                    logger.error(f"ECOsteam 下架 {failure_count} 个失败")
                return failure_count == 0
        except Exception as e:
            handle_caught_exception(e, "SoldAutoOffShelf")
            logger.error(f"{PLATFORM_NAMES[platform]} 下架过程出错")
            return False
        return False

    # ---------------- 主流程 ----------------

    def process_sold_order(self, sold_platform: str, unit: dict, shelves: dict) -> bool:
        """处理一笔售出：在其他平台下架对应的一件挂单。返回是否全部处理成功。"""
        order_id = unit["order_id"]
        assetid = unit.get("assetid")
        name = unit.get("name") or ""
        ok = True
        for platform in self.platforms:
            if platform == sold_platform:
                continue
            shelf = shelves.get(platform) or []
            target = None
            if assetid:
                for listing in shelf:
                    if listing["assetid"] == assetid and not listing.get("_taken"):
                        target = listing
                        break
            if target is None and name:
                # 无 assetid（或按 assetid 未找到）时按名称匹配一件
                for listing in shelf:
                    if listing["name"] == name and not listing.get("_taken"):
                        target = listing
                        break
            if target is None:
                # 该平台没有对应挂单（可能本来就没挂），视为成功
                continue
            target["_taken"] = True
            display = name or target["name"] or target["assetid"]
            logger.warning(
                f"饰品「{display}」已在 {PLATFORM_NAMES[sold_platform]} 售出（订单 {order_id}），正在从 {PLATFORM_NAMES[platform]} 下架..."
            )
            if self.offshelf(platform, [target["order_no"]]):
                logger.info(f"{PLATFORM_NAMES[platform]} 下架「{display}」成功")
                try:
                    send_notification(
                        self.steam_client,
                        f"饰品「{display}」已在 {PLATFORM_NAMES[sold_platform]} 售出，已自动在 {PLATFORM_NAMES[platform]} 下架。",
                        title="跨平台自动下架",
                    )
                except Exception:
                    pass
            else:
                ok = False
        return ok

    def inventory_fallback(self, shelves: dict):
        """A 兜底：在售挂单的 assetid 已不在 Steam 库存 → 在该平台下架。"""
        inventory = get_cs2_inventory(self.steam_client, self.steam_client_mutex)
        if not inventory:
            logger.warning("无法获取 Steam 库存，跳过库存对账（兜底）")
            return
        for platform in self.platforms:
            offshelf_list = [
                listing["order_no"]
                for listing in (shelves.get(platform) or [])
                if listing["assetid"] not in inventory
            ]
            if offshelf_list:
                logger.warning(
                    f"检测到 {PLATFORM_NAMES[platform]} 有 {len(offshelf_list)} 个挂单饰品已不在 Steam 库存中，执行下架（兜底）"
                )
                self.offshelf(platform, offshelf_list)

    def run_once(self):
        # 1. 拉取各平台货架
        shelves = {}
        for platform in self.platforms:
            try:
                shelves[platform] = self.get_shelf(platform)
            except Exception as e:
                handle_caught_exception(e, "SoldAutoOffShelf", known=True)
                logger.error(f"获取 {PLATFORM_NAMES[platform]} 在售货架失败，本轮跳过该平台")
                shelves[platform] = None
            time.sleep(1)

        # 2. 拉取各平台已售出订单，处理新订单
        fetchers = {"buff": self.get_sold_orders_buff, "uu": self.get_sold_orders_uu, "eco": self.get_sold_orders_eco}
        for platform in self.platforms:
            try:
                sold_orders = fetchers[platform]()
            except Exception as e:
                handle_caught_exception(e, "SoldAutoOffShelf", known=True)
                logger.error(f"获取 {PLATFORM_NAMES[platform]} 售出订单失败，本轮跳过")
                continue
            for unit in sold_orders:
                key = f"{platform}:{unit['order_id']}"
                if key in self.handled_orders:
                    continue
                # 依赖其他平台货架；若某平台货架拉取失败则跳过对该平台的下架
                if self.process_sold_order(platform, unit, {p: s for p, s in shelves.items() if s is not None}):
                    self.handled_orders.add(key)
                    self.order_retries.pop(key, None)
                else:
                    self.order_retries[key] = self.order_retries.get(key, 0) + 1
                    if self.order_retries[key] >= MAX_RETRY:
                        self.handled_orders.add(key)
                        self.order_retries.pop(key, None)
                        logger.error(
                            f"订单 {key} 跨平台下架重试 {MAX_RETRY} 次仍失败，请手动检查其他平台挂单！"
                        )
                        try:
                            send_notification(
                                self.steam_client,
                                f"售出订单 {unit['order_id']}（{PLATFORM_NAMES[platform]}）在其他平台自动下架多次失败，请手动检查，谨防一单多卖！",
                                title="跨平台自动下架失败",
                            )
                        except Exception:
                            pass
            time.sleep(1)

        # 3. A 兜底：库存对账
        if self.inventory_check:
            try:
                self.inventory_fallback(shelves)
            except Exception as e:
                handle_caught_exception(e, "SoldAutoOffShelf", known=True)
                logger.error("库存对账（兜底）失败，本轮跳过")

    def exec(self):
        logger.info("跨平台售出自动下架插件已启动")
        while True:
            try:
                self.run_once()
            except Exception as e:
                handle_caught_exception(e, "SoldAutoOffShelf")
                logger.error("发生未知错误，稍后重试")
            logger.info(f"等待 {self.interval} 秒后继续检查跨平台售出情况...")
            time.sleep(self.interval)
