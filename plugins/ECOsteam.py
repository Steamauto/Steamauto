import copy
import datetime
import json
import os
import time
from threading import Thread
from typing import Dict, List, Union

from BuffApi import BuffAccount
from BuffApi.models import BuffOnSaleAsset
from PyECOsteam import ECOsteamClient, models
from steampy.client import SteamClient
from steampy.models import GameOptions
from utils.buff_helper import get_valid_session_for_buff
from utils.logger import LogFilter, PluginLogger, handle_caught_exception
from utils.models import Asset, LeaseAsset, ModelEncoder
from utils.static import ECOSTEAM_RSAKEY_FILE
from utils.tools import exit_code, get_encoding
from utils.uu_helper import get_valid_token_for_uu
from uuyoupinapi import UUAccount

sync_sell_shelf_enabled = False
sync_lease_shelf_enabled = False

uu_queue = None
eco_queue = None

logger = PluginLogger("ECOsteam.cn")
sell_logger = PluginLogger("[ECOsteam.cn] [同步多平台出售]")
lease_logger = PluginLogger("[ECOsteam.cn] [同步租赁货架]")
accept_offer_logger = PluginLogger("[ECOsteam.cn] [自动发货]")


def compare_shelves(A: List[Asset], B: List[Asset], ratio: float) -> Union[bool, dict[str, list[Asset]]]:
    result = {"add": [], "delete": [], "change": []}
    ratio = round(ratio, 2)

    # 创建字典用于快速查找
    A_dict = {item.assetid: item for item in A}
    B_dict = {item.assetid: item for item in B}

    # 查找添加和删除的字典
    for assetid in A_dict:
        if assetid not in B_dict:
            # 调整price符合比例
            adjusted_dict = A_dict[assetid].model_copy()
            adjusted_dict.price = round(adjusted_dict.price / ratio, 2)
            result["add"].append(adjusted_dict)

    for assetid in B_dict:
        if assetid not in A_dict:
            result["delete"].append(B_dict[assetid])

    # 查找需要更改的字典
    for assetid in A_dict:
        if assetid in B_dict:
            A_price = A_dict[assetid].price
            B_price = B_dict[assetid].price
            if abs(round(A_price / B_price - ratio, 2)) > 0.01:
                # 调整B表中字典的price
                adjusted_dict = B_dict[assetid].model_copy()
                adjusted_dict.price = round(A_price / ratio, 2)
                result["change"].append(adjusted_dict)

    return result


def compare_lease_shelf(A: List[LeaseAsset], B: List[LeaseAsset], ratio: float) -> Dict[str, List[LeaseAsset]]:
    result = {"add": [], "delete": [], "change": []}
    ratio = round(ratio, 2)

    # 创建字典用于快速查找
    A_dict = {item.assetid: item for item in A}
    B_dict = {item.assetid: item for item in B}

    # 查找添加和删除的字典
    for assetid in A_dict:
        if assetid not in B_dict:
            result["add"].append(A_dict[assetid])

    for assetid in B_dict:
        if assetid not in A_dict:
            result["delete"].append(B_dict[assetid])

    # 查找需要更改的字典
    for assetid in A_dict:
        if assetid in B_dict:
            A_item = A_dict[assetid]
            B_item = B_dict[assetid]

            # 比较 LeaseDeposit, LeaseMaxDays, LeaseUnitPrice 和 LongLeaseUnitPrice
            changes_needed = False

            if A_item.LeaseDeposit != B_item.LeaseDeposit:
                changes_needed = True

            if A_item.LeaseMaxDays != B_item.LeaseMaxDays:
                changes_needed = True

            if round(abs(A_item.LeaseUnitPrice / B_item.LeaseUnitPrice - ratio), 2) >= 0.01:
                changes_needed = True

            if A_item.LongLeaseUnitPrice and B_item.LongLeaseUnitPrice:
                if round(abs(A_item.LongLeaseUnitPrice / B_item.LongLeaseUnitPrice - ratio), 2) > 0.01:
                    changes_needed = True
            elif A_item.LongLeaseUnitPrice != B_item.LongLeaseUnitPrice:
                changes_needed = True

            if changes_needed:
                adjusted_dict = A_item.model_copy()
                adjusted_dict.orderNo = B_item.orderNo
                adjusted_dict.LeaseUnitPrice = round(A_item.LeaseUnitPrice / ratio, 2)
                if A_item.LongLeaseUnitPrice:
                    adjusted_dict.LongLeaseUnitPrice = round(A_item.LongLeaseUnitPrice / ratio, 2)
                result["change"].append(adjusted_dict)

    return result


class tasks:
    def __init__(self, client, steamid) -> None:
        self.sell_queue = []
        self.sell_change_queue = []
        self.lease_queue = []
        self.lease_change_queue = []
        self.client = client
        self.steamid = steamid
        if isinstance(self.client, ECOsteamClient):
            self.platform = "ECOsteam"
        elif isinstance(self.client, UUAccount):
            self.platform = "悠悠有品"

    def sell_add(self, assets: List[Asset]):
        self.sell_queue += assets

    def sell_change(self, assets: List[Asset]):
        self.sell_change_queue += assets

    def sell_remove(self, assetId: str):
        for asset in self.sell_queue:
            if asset.assetid == assetId:
                self.sell_queue.remove(asset)
                break

    def lease_add(self, assets: List[LeaseAsset]):
        self.lease_queue += assets

    def lease_change(self, assets: List[LeaseAsset]):
        self.lease_change_queue += assets

    def lease_remove(self, assetId: str):
        for asset in self.lease_queue:
            if asset.assetid == assetId:
                self.lease_queue.remove(asset)
                break

    def process(self):
        logger.debug(self.platform + "出售队列：" + json.dumps(self.sell_queue, cls=ModelEncoder, ensure_ascii=False))
        logger.debug(self.platform + "租赁队列：" + json.dumps(self.lease_queue, cls=ModelEncoder, ensure_ascii=False))
        logger.debug(self.platform + "出售改价队列：" + json.dumps(self.sell_change_queue, cls=ModelEncoder, ensure_ascii=False))
        logger.debug(self.platform + "租赁改价队列：" + json.dumps(self.lease_change_queue, cls=ModelEncoder, ensure_ascii=False))
        if (
            len(self.sell_queue) > 0
            or len(self.lease_queue) > 0
            or len(self.sell_change_queue) > 0
            or len(self.lease_change_queue) > 0
        ):
            logger.info(self.platform + '平台任务队列开始执行')
        else:
            logger.info(self.platform + '平台任务队列为空，不需要处理')

        if len(self.sell_queue) > 0 or len(self.lease_queue) > 0:
            logger.info(f'即将向出售货架上架 {len(self.sell_queue)} 个商品')
            logger.info(f'即将向租赁货架上架 {len(self.lease_queue)} 个商品')
            success_count, failure_count = 0, 0
            if isinstance(self.client, ECOsteamClient):
                success_count, failure_count = self.client.PublishRentAndSaleGoods(
                    self.steamid, 1, self.sell_queue, self.lease_queue
                )
            elif isinstance(self.client, UUAccount):
                success_count, failure_count = self.client.onshelf_sell_and_lease(self.sell_queue, self.lease_queue)
            self.sell_queue = []
            self.sell_change_queue = []
            if failure_count != 0:
                logger.error(f'上架失败 {failure_count} 个商品')
            logger.info(f'上架成功 {success_count} 个商品')

        if len(self.sell_change_queue) > 0 or len(self.lease_change_queue) > 0:
            logger.info(f'即将在出售货架改价 {len(self.sell_change_queue)} 个商品')
            logger.info(f'即将在租赁货架改价 {len(self.lease_change_queue)} 个商品')
            success_count, failure_count = 0, 0
            if isinstance(self.client, ECOsteamClient):
                success_count, failure_count = self.client.PublishRentAndSaleGoods(
                    self.steamid, 2, self.sell_change_queue, self.lease_change_queue
                )
            elif isinstance(self.client, UUAccount):
                success_count, failure_count = self.client.change_price_sell_and_lease(
                    self.sell_change_queue, self.lease_change_queue
                )
            self.lease_queue = []
            self.lease_change_queue = []
            if failure_count != 0:
                logger.error(f'改价失败 {failure_count} 个商品')
            logger.info(f'改价成功 {success_count} 个商品')


class ECOsteamPlugin:
    def __init__(self, steam_client: SteamClient, steam_client_mutex, config):
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        self.ignored_offer = []
        with steam_client_mutex:
            self.steam_id = steam_client.get_steam64id_from_cookies()

    def init(self):
        if not os.path.exists(ECOSTEAM_RSAKEY_FILE):
            with open(ECOSTEAM_RSAKEY_FILE, "w", encoding="utf-8") as f:
                f.write("")
            return True
        return False

    def exec(self):
        logger.info(f"ECOsteam插件已启动")
        logger.info("正在登录ECOsteam...")
        try:
            with open(ECOSTEAM_RSAKEY_FILE, "r", encoding=get_encoding(ECOSTEAM_RSAKEY_FILE)) as f:
                rsa_key = f.read()
            if "PUBLIC" in rsa_key:
                logger.error("你在rsakey文件中放入的不是私钥！请填入私钥信息(Private key)！")
                return 1
            LogFilter.add_sensitive_data(self.config["ecosteam"]["partnerId"])
            self.client = ECOsteamClient(
                self.config["ecosteam"]["partnerId"],
                rsa_key,
                qps=self.config["ecosteam"]["qps"],
            )
            user_info = self.client.GetTotalMoney().json()
            if user_info["ResultData"].get("UserName", None):
                logger.info(
                    f'登录成功，用户ID为{user_info["ResultData"]["UserName"]}，当前余额为{user_info["ResultData"]["Money"]}元'
                )
            else:
                raise Exception
        except Exception as e:
            logger.error(f"登录失败！请检查{ECOSTEAM_RSAKEY_FILE}和parterId是否正确！由于无法登录ECOsteam，插件将退出。")
            handle_caught_exception(e, known=True)
            exit_code.set(1)
            return 1

        # 检查当前登录的Steam账号是否在ECOsteam绑定账号列表内
        exist = False
        accounts_list = self.client.QuerySteamAccountList().json()["ResultData"]
        for account in accounts_list:
            if account["SteamId"] == self.steam_id:
                exist = True
                break
        if not exist:
            logger.error(f"当前登录的Steam账号{self.steam_id}不在ECOsteam绑定账号列表内！插件将退出。")
            exit_code.set(1)
            return 1
        if exist and len(accounts_list) > 1:
            logger.warning(
                f"检测到你的ECOsteam绑定了多个Steam账号。插件的所有操作仅对SteamID为{self.steam_id}的账号生效！如需同时操作多个账号，请多开Steamauto实例！"
            )

        threads = []
        threads.append(Thread(target=self.auto_accept_offer))
        if (
            self.config["ecosteam"]["auto_sync_sell_shelf"]["enable"]
            or self.config["ecosteam"]["auto_sync_lease_shelf"]["enable"]
        ):
            threads.append(Thread(target=self.auto_sync_shelves))
        if not len(threads) == 1:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        else:
            self.auto_accept_offer()

    # 获取各平台出售货架
    def get_shelf(self, platform, inventory):
        # 如果需要下架
        assets = list()
        if platform == "eco":
            result = self.client.getFullSellGoodsList(self.steam_id)
            if not inventory:
                raise SystemError
            for item in result:
                asset = Asset(assetid=item["AssetId"], orderNo=item["GoodsNum"], price=float(item["Price"]))
                try:
                    asset.appid = inventory[asset.assetid]["appid"]
                    asset.classid = inventory[asset.assetid]["classid"]
                    asset.contextid = inventory[asset.assetid]["contextid"]
                    asset.instanceid = inventory[asset.assetid]["instanceid"]
                    asset.market_hash_name = inventory[asset.assetid]["market_hash_name"]
                    assets.append(asset)
                except KeyError:
                    sell_logger.warning(f"检测到ECOsteam上架物品 {item['GoodsName']} 不在Steam库存中！")
                    assets.append(asset.orderNo)
            return assets
        elif platform == "buff":
            data = self.buff_client.get_on_sale().json()["data"]
            items = data["items"]
            if data['total_count']> 500:
                items += self.buff_client.get_on_sale(page_num=2).json()["data"]["items"]
            for item in items:
                asset = Asset(assetid=item["asset_info"]["assetid"], orderNo=item["id"], price=float(item["price"]))
                try:
                    asset.appid = inventory[asset.assetid]["appid"]
                    asset.classid = inventory[asset.assetid]["classid"]
                    asset.contextid = inventory[asset.assetid]["contextid"]
                    asset.instanceid = inventory[asset.assetid]["instanceid"]
                    asset.market_hash_name = inventory[asset.assetid]["market_hash_name"]
                    assets.append(asset)
                except KeyError:
                    sell_logger.warning(
                        f"检测到BUFF上架物品 {data['goods_infos'][str(item['goods_id'])]['market_hash_name']} 不在Steam库存中！"
                    )
                    assets.append(asset.orderNo)
            return assets
        elif platform == "uu":
            data = self.uu_client.get_sell_list()
            for item in data:
                asset = Asset(assetid=str(item["steamAssetId"]), orderNo=item["id"], price=float(item["sellAmount"]))
                try:
                    asset.appid = inventory[asset.assetid]["appid"]
                    asset.classid = inventory[asset.assetid]["classid"]
                    asset.contextid = inventory[asset.assetid]["contextid"]
                    asset.instanceid = inventory[asset.assetid]["instanceid"]
                    asset.market_hash_name = inventory[asset.assetid]["market_hash_name"]
                    assets.append(asset)
                except KeyError:
                    sell_logger.warning(f"检测到悠悠上架物品不在Steam库存中！")  # TODO: 提示物品名
                    assets.append(asset.orderNo)
            return assets

    # 获取Steam库存
    def get_steam_inventory(self):
        inventory = None
        try:
            with self.steam_client_mutex:
                inventory = self.steam_client.get_my_inventory(game=GameOptions.CS)  # type: ignore
                logger.log(5,'获取到的Steam库存:' + json.dumps(inventory, ensure_ascii=False))
        except Exception as e:
            handle_caught_exception(e, "ECOsteam.cn")
        return inventory

    # 自动发货线程
    def auto_accept_offer(self):
        while True:
            try:
                self.__auto_accept_offer()
            except Exception as e:
                handle_caught_exception(e, "ECOsteam.cn")
                accept_offer_logger.error("发生未知错误，请稍候再试！")
                time.sleep(self.config["ecosteam"]["auto_accept_offer"]["interval"])

    # 自动发货实现
    def __auto_accept_offer(self):
        accept_offer_logger.info("正在检查待发货列表...")
        today = datetime.datetime.today()
        tomorrow = datetime.datetime.today() + datetime.timedelta(days=1)
        last_month = today - datetime.timedelta(days=30)
        tomorrow = tomorrow.strftime("%Y-%m-%d")
        last_month = last_month.strftime("%Y-%m-%d")
        wait_deliver_orders = self.client.getFullSellerOrderList(last_month, tomorrow, DetailsState=8, SteamId=self.steam_id)
        # for order in wait_deliver_orders:
        #     if order['OrderStateCode'] == 2:
        #         wait_deliver_orders.remove(order)
        accept_offer_logger.info(f"检测到{len(wait_deliver_orders)}个待发货订单！")
        if len(wait_deliver_orders) > 0:
            for order in wait_deliver_orders:
                accept_offer_logger.debug(f'正在获取订单号{order["OrderNum"]}的详情！')
                detail = self.client.GetSellerOrderDetail(OrderNum=order["OrderNum"]).json()["ResultData"]
                tradeOfferId = detail["TradeOfferId"]
                goodsName = detail["GoodsName"]
                if tradeOfferId not in self.ignored_offer:
                    accept_offer_logger.info(f"正在发货商品{goodsName}，报价号{tradeOfferId}...")
                    try:
                        with self.steam_client_mutex:
                            self.steam_client.accept_trade_offer(str(tradeOfferId))
                        self.ignored_offer.append(tradeOfferId)
                        accept_offer_logger.info(f"已接受报价号{tradeOfferId}！")
                    except Exception as e:
                        handle_caught_exception(e, "ECOsteam.cn", known=True)
                        accept_offer_logger.error("Steam异常, 暂时无法接受报价, 请稍后再试! ")
                else:
                    accept_offer_logger.info(f"已经自动忽略报价号{tradeOfferId}，商品名{goodsName}，因为它已经被程序处理过！")
        interval = self.config["ecosteam"]["auto_accept_offer"]["interval"]
        accept_offer_logger.info(f"等待{interval}秒后继续检查待发货列表...")
        time.sleep(interval)

    # 自动同步上架启动线程
    def auto_sync_shelves(self):
        global sync_lease_shelf_enabled
        global sync_sell_shelf_enabled
        # 配置检查
        if self.config["ecosteam"]["auto_sync_sell_shelf"]["enable"]:
            config_sync_sell_shelf = self.config["ecosteam"]["auto_sync_sell_shelf"]
            sync_sell_shelf_enabled = True
            config_sync_sell_shelf["enabled_platforms"].append("eco")
            if not config_sync_sell_shelf["main_platform"] in config_sync_sell_shelf["enabled_platforms"]:
                sell_logger.error("主平台必须在enabled_platforms中！请重新修改检查配置文件！")
                sync_sell_shelf_enabled = False
            platforms = list(copy.deepcopy(config_sync_sell_shelf["enabled_platforms"]))
            while len(platforms) > 0:
                platform = platforms.pop()
                if not (platform == "uu" or platform == "eco" or platform == "buff"):
                    sell_logger.error("当前仅支持UU/ECO/BUFF平台，请检查配置！")
                    sync_sell_shelf_enabled = False
                    break
            if not config_sync_sell_shelf["main_platform"] in config_sync_sell_shelf["enabled_platforms"]:
                sell_logger.error("由于主平台未启用，自动同步平台功能已经自动关闭")
                sync_sell_shelf_enabled = False
            if not sync_sell_shelf_enabled:
                sell_logger.error("由于配置错误，自动同步平台功能已经自动关闭")
                return

            # BUFF登录
            if "buff" in config_sync_sell_shelf["enabled_platforms"]:
                sell_logger.info("由于已经启用BUFF平台，正在联系BuffLoginSolver获取有效的session...")
                buff_session = ""
                with self.steam_client_mutex:
                    buff_session = get_valid_session_for_buff(self.steam_client, sell_logger)
                if not buff_session:
                    sell_logger.warning("无法获取有效的BUFF session，BUFF平台相关已经自动关闭")
                    config_sync_sell_shelf["enabled_platforms"].remove("buff")
                else:
                    self.buff_client = BuffAccount(buff_session)
                    sell_logger.info(f"已经获取到有效的BUFF session")

            # 悠悠登录
            if "uu" in config_sync_sell_shelf["enabled_platforms"] and not (hasattr(self, "uu_client") and self.uu_client):
                sell_logger.info("由于已经启用悠悠平台，正在联系UULoginSolver获取有效的session...")
                token = get_valid_token_for_uu()
                if token:
                    self.uu_client = UUAccount(token)
                else:
                    sell_logger.warning("无法获取有效的悠悠token，悠悠有品平台自动同步售卖上架已经自动关闭")
                    config_sync_sell_shelf["enabled_platforms"].remove("uu")

            # 检查是否有平台可用
            if len(config_sync_sell_shelf["enabled_platforms"]) == 1:
                sell_logger.error("无平台可用。已经关闭自动同步出售货架功能！")
                sync_sell_shelf_enabled = False

        if self.config["ecosteam"]['auto_sync_lease_shelf']['enable']:
            # 检查悠悠是否正常登录
            if not (hasattr(self, "uu_client") and self.uu_client):
                lease_logger.info("由于已经启用悠悠平台，正在联系UULoginSolver获取有效的session...")
                token = get_valid_token_for_uu()
                if token:
                    self.uu_client = UUAccount(token)
                else:
                    lease_logger.warning("无法获取有效的悠悠token，悠悠有品平台自动同步租赁上架已经自动关闭")
                    return
            self.lease_main_platform = self.config["ecosteam"]["auto_sync_lease_shelf"]["main_platform"]
            if self.lease_main_platform != "uu" and self.lease_main_platform != "eco":
                lease_logger.error("主平台配置必须为uu或eco！请检查配置文件！")
                return
            sync_lease_shelf_enabled = True
        global uu_queue
        global eco_queue
        if hasattr(self, "uu_client") and self.uu_client:
            uu_queue = tasks(self.uu_client, self.steam_id)
        eco_queue = tasks(self.client, self.steam_id)

        while True:
            if sync_sell_shelf_enabled:
                self.sync_sell_shelves()
            if sync_lease_shelf_enabled:
                self.sync_lease_shelves()
            eco_queue.process()
            if isinstance(uu_queue, tasks):
                uu_queue.process()
            logger.info(f'等待 {self.config["ecosteam"]["sync_interval"]} 秒后重新检查多平台上架物品')
            time.sleep(self.config["ecosteam"]["sync_interval"])

    # 自动同步租赁货架实现
    def sync_lease_shelves(self):
        lease_logger.info("正在从ECOsteam获取租赁上架物品信息...")
        lease_shelves = {}
        lease_shelves['eco'] = self.client.getFulRentGoodsList(self.steam_id)
        lease_logger.debug(f'ECO租赁货架：{json.dumps(lease_shelves["eco"], cls=ModelEncoder)}')
        lease_logger.info(f"ECOsteam共上架{len(lease_shelves['eco'])}个租赁物品")

        lease_logger.info("正在从悠悠有品获取租赁上架物品信息...")
        lease_shelves['uu'] = self.uu_client.get_uu_leased_inventory()
        lease_logger.debug(f'悠悠租赁货架：{json.dumps(lease_shelves["uu"], cls=ModelEncoder)}')
        lease_logger.info(f"悠悠有品共上架{len(lease_shelves['uu'])}个租赁物品")

        if self.lease_main_platform == "eco":
            lease_logger.info('当前同步主平台为ECOsteam')
            self.lease_other_platform = "uu"
        else:
            lease_logger.info('当前同步主平台为悠悠有品')
            self.lease_other_platform = "eco"

        difference = compare_lease_shelf(
            lease_shelves[self.lease_main_platform],
            lease_shelves[self.lease_other_platform],
            self.config['ecosteam']['auto_sync_lease_shelf']['ratio'][self.lease_main_platform]
            / self.config['ecosteam']['auto_sync_lease_shelf']['ratio'][self.lease_other_platform],
        )
        lease_logger.debug(
            f"租赁-当前被同步平台：{self.lease_other_platform.upper()}\nDifference: {json.dumps(difference, cls=ModelEncoder)}"
        )
        if difference != {"add": [], "delete": [], "change": []}:
            lease_logger.warning(f"{self.lease_other_platform.upper()}平台需要更新租赁上架商品/价格")
            if self.lease_other_platform == "uu":
                # 上架商品
                if len(difference['add']) > 0:
                    if isinstance(uu_queue, tasks):
                        uu_queue.lease_add(difference['add'])
                        lease_logger.info(f"已经添加{len(difference['add'])}个商品到悠悠有品租赁上架队列")
                    else:
                        lease_logger.error('悠悠有品任务队列未初始化！')
                # 下架商品
                if len(difference['delete']) > 0:
                    lease_logger.info(f"即将在悠悠有品租赁货架下架{len(difference['delete'])}个商品")
                    rsp = self.uu_client.off_shelf([item.orderNo for item in difference["delete"]]).json()
                    if rsp['Code'] == 0:
                        lease_logger.info(f"下架{len(difference['delete'])}悠悠有品成功！")
                    else:
                        lease_logger.error(f"下架过程中出现失败！错误信息：{rsp['Msg']}")
                # 修改价格
                if len(difference['change']) > 0:
                    if isinstance(uu_queue, tasks):
                        uu_queue.lease_change(difference['change'])
                        lease_logger.info(f"已经添加{len(difference['change'])}个商品到悠悠有品租赁改价队列")
                    else:
                        lease_logger.error('悠悠有品任务队列未初始化！')
            elif self.lease_other_platform == "eco":
                # 上架商品
                if len(difference['add']) > 0:
                    if isinstance(eco_queue, tasks):
                        eco_queue.lease_add(difference['add'])
                        lease_logger.info(f"已经添加{len(difference['add'])}个商品到ECOsteam租赁上架队列")
                    else:
                        lease_logger.error('ECOsteam.cn任务队列未初始化！')

                # 下架商品
                if len(difference['delete']) > 0:
                    lease_logger.info(f"即将在ECOsteam租赁货架下架{len(difference['delete'])}个商品")
                    batches = [difference['delete'][i : i + 100] for i in range(0, len(difference['delete']), 100)]
                    success_count = 0
                    for batch in batches:
                        try:
                            rsp = self.client.OffshelfRentGoods(
                                [models.GoodsNum(AssetId=item.assetid, SteamGameId=str(item.appid)) for item in batch]
                            ).json()
                            if rsp['ResultCode'] == '0':
                                success_count += len(batch)
                            else:
                                lease_logger.error(f"下架租赁商品过程中出现失败！错误信息：{rsp['ResultMsg']}")
                        except Exception as e:
                            handle_caught_exception(e, "ECOsteam.cn", known=True)
                            lease_logger.error("发生未知错误，请稍候再试！")
                    lease_logger.info(f"下架{success_count}个商品成功！")

                # 修改价格
                if len(difference['change']) > 0:
                    if isinstance(eco_queue, tasks):
                        eco_queue.lease_change(difference['change'])
                        lease_logger.info(f"已经添加{len(difference['change'])}个商品到ECOsteam租赁改价队列")
                    else:
                        lease_logger.error('ECOsteam.cn任务队列未初始化！')

    # 自动同步出售货架实现
    def sync_sell_shelves(self):
        tc = copy.deepcopy(self.config["ecosteam"]["auto_sync_sell_shelf"])
        main_platform = tc["main_platform"]
        shelves = {}
        ratios = {}
        for platform in tc["enabled_platforms"]:
            shelves[platform] = list()
            ratios[platform] = tc["ratio"][platform]
        sell_logger.info("正在从Steam获取库存信息...")
        inventory = self.get_steam_inventory()
        if not inventory:
            sell_logger.error("Steam异常, 暂时无法获取库存, 请稍后再试! ")
            return
        else:
            sell_logger.info(f"Steam库存中共有{len(inventory)}个物品")

        try:
            for platform in tc["enabled_platforms"]:
                sell_logger.info(f"正在从{platform.upper()}平台获取上架物品信息...")
                shelves[platform] = self.get_shelf(platform, inventory)
                sell_logger.info(f"{platform.upper()}平台共上架{len(shelves[platform])}个物品")
                # 判断是否需要下架
                if len(shelves[platform]) > 0:
                    offshelf_list = []
                    for good in shelves[platform]:
                        if not isinstance(good, Asset):
                            offshelf_list.append(good)
                    if len(offshelf_list) > 0:
                        sell_logger.warning(
                            f"检测到{platform.upper()}平台上架的{len(offshelf_list)}个物品不在Steam库存中！即将下架！"
                        )
                        if platform == "eco":
                            success_count,failure_count = self.client.OffshelfGoods(
                                [models.GoodsNum(GoodsNum=good, SteamGameId='730') for good in offshelf_list]
                            )
                            sell_logger.info(f'下架{success_count}个商品成功！')
                            if failure_count != 0:
                                sell_logger.error(f'下架{failure_count}个商品失败！')
                        elif platform == "buff":
                            try:
                                count, problems = self.buff_client.cancel_sale(offshelf_list)
                                sell_logger.info(f"下架{count}个商品成功！下架{len(problems)}个商品失败！")
                            except Exception as e:
                                handle_caught_exception(e, "ECOsteam.cn", known=True)
                                sell_logger.error(f"下架商品失败！可能有部分下架成功")
                        elif platform == "uu":
                            response = self.uu_client.off_shelf(offshelf_list)
                            if int(response.json()["Code"]) == "0":
                                sell_logger.info(f"下架{len(offshelf_list)}个商品成功！")
                            else:
                                sell_logger.error(f"下架{len(offshelf_list)}个商品失败！错误信息{str(response.json())}")
                        # 重新获取上架物品
                        shelves[platform] = self.get_shelf(platform, inventory)
        except Exception as e:
            handle_caught_exception(e, "ECOsteam.cn")

        for platform in tc["enabled_platforms"]:
            if platform != main_platform:
                difference = compare_shelves(
                    shelves[main_platform],
                    shelves[platform],
                    ratios[main_platform] / ratios[platform],
                )
                sell_logger.debug(
                    f"当前平台：{platform.upper()}\nDifference: {json.dumps(difference,cls=ModelEncoder,ensure_ascii=False)}"
                )
                if difference != {"add": [], "delete": [], "change": []}:
                    sell_logger.warning(f"{platform.upper()}平台需要更新上架商品/价格")
                    try:
                        self.solve_platform_difference(platform, difference)
                    except Exception as e:
                        handle_caught_exception(e, "ECOsteam.cn")
                        sell_logger.error("发生未知错误，请稍候再试！")
                else:
                    sell_logger.info(f"{platform.upper()}平台已经保持同步")

    def solve_platform_difference(self, platform, difference):
        if platform == "eco":
            # 上架商品
            if len(difference["add"]) > 0:
                if isinstance(eco_queue, tasks):
                    eco_queue.sell_add(difference["add"])
                    sell_logger.info(f"已经添加 {len(difference['add'])} 个商品到ECOsteam出售上架队列")
                else:
                    sell_logger.error('ECOsteam.cn任务队列未初始化！')

            # 下架商品
            assets = [asset.orderNo for asset in difference["delete"]]
            if len(assets) > 0:
                sell_logger.info(f"即将在{platform.upper()}平台下架{len(assets)}个商品")
                success_count,failure_count = self.client.OffshelfGoods(
                    [models.GoodsNum(GoodsNum=goodsNum, SteamGameId='730') for goodsNum in assets]
                )
                sell_logger.info(f"下架{success_count}个商品成功！")
                if failure_count != 0:
                    sell_logger.error(f"下架{failure_count}个商品失败！")

            # 修改价格
            if len(difference["change"]) > 0:
                if isinstance(eco_queue, tasks):
                    eco_queue.sell_change(difference["change"])
                    sell_logger.info(f"已经添加 {len(difference['change'])} 个商品到ECOsteam出售改价队列")
                else:
                    sell_logger.error('ECOsteam.cn任务队列未初始化！')

        elif platform == "buff":
            # 上架商品
            assets = difference["add"]
            if len(assets) > 0:
                buff_assets = [BuffOnSaleAsset.from_Asset(asset) for asset in assets]
                sell_logger.info(f"即将上架{len(assets)}个商品到BUFF")
                try:
                    success, failure = self.buff_client.on_sale(buff_assets)
                    for asset in assets:
                        if asset.assetid in failure:
                            sell_logger.error(
                                f"上架 {asset.market_hash_name}(ID:{asset.assetid}) 失败！错误信息: {failure[asset.assetid]}"
                            )
                    sell_logger.info(f"上架{len(success)}个商品到BUFF成功！上架{len(failure)}个商品失败！")
                except Exception as e:
                    handle_caught_exception(e, "ECOsteam.cn")
                    sell_logger.error(f"上架商品失败！可能部分上架成功！")

            # 下架商品
            assets = difference["delete"]
            if len(assets) > 0:
                sell_orders = [asset.orderNo for asset in difference["delete"]]
                sell_logger.info(f"即将在{platform.upper()}平台下架{len(assets)}个商品")
                try:
                    success, failure = self.buff_client.cancel_sale(sell_orders)
                    for asset in assets:
                        if asset.orderNo in failure:
                            sell_logger.error(
                                f"下架 {asset.market_hash_name}(ID:{asset.assetid}) 失败！错误信息: {failure[asset.orderNo]}"
                            )
                    sell_logger.info(f"下架{success}个商品成功！下架{len(failure)}个商品失败！")
                except Exception as e:
                    handle_caught_exception(e, "ECOsteam.cn")
                    sell_logger.error(f"下架商品失败！可能部分下架成功！")

            # 更改价格
            assets = difference["change"]
            if len(assets) > 0:
                sell_orders = [
                    {
                        "sell_order_id": asset.orderNo,
                        "price": asset.price,
                        # "income": asset.price,
                        "desc": "",
                    }
                    for asset in assets
                ]
                sell_logger.info(f"即将在{platform.upper()}平台修改{len(assets)}个商品的价格")
                success, problem_sell_orders = self.buff_client.change_price(sell_orders)
                for asset in assets:
                    if asset.orderNo in problem_sell_orders.keys():
                        sell_logger.error(
                            f"修改 {asset.market_hash_name}(ID:{asset.assetid}) 的价格失败！错误信息: {problem_sell_orders[asset.orderNo]}"
                        )
                sell_logger.info(f"修改{success}个商品的价格成功！修改{len(problem_sell_orders)}个商品失败！")
        elif platform == "uu":
            # 上架商品
            if len(difference["add"]) > 0:
                if isinstance(uu_queue, tasks):
                    uu_queue.sell_add(difference["add"])
                    sell_logger.info(f"已经添加 {len(difference['add'])} 个商品到悠悠有品出售上架队列")
                else:
                    sell_logger.error('悠悠有品任务队列未初始化！')

            # 下架商品
            delete = difference["delete"]
            assets = [str(item.orderNo) for item in delete]
            if len(assets) > 0:
                sell_logger.info(f"即将在{platform.upper()}平台下架{len(assets)}个商品")
                response = self.uu_client.off_shelf(assets)
                if int(response.json()["Code"]) == 0:
                    sell_logger.info(f"下架{len(assets)}个商品成功！")
                else:
                    sell_logger.error(f'下架{len(assets)}个商品失败！错误信息：{str(response.json()["Msg"])}')

            # 修改价格
            if len(difference["change"]) > 0:
                if isinstance(uu_queue, tasks):
                    uu_queue.sell_change(difference["change"])
                    sell_logger.info(f"已经添加 {len(difference['change'])} 个商品到悠悠有品出售改价队列")
                else:
                    sell_logger.error('悠悠有品任务队列未初始化！')
