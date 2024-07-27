import copy
import datetime
import json
import os
from sre_constants import SUCCESS
import time
from threading import Thread
from typing import List

from BuffApi import BuffAccount
from PyECOsteam import ECOsteamClient
from steampy.client import SteamClient
from steampy.models import GameOptions
from utils.buff_helper import get_valid_session_for_buff
from utils.logger import LogFilter, PluginLogger, handle_caught_exception
from utils.static import ECOSTEAM_RSAKEY_FILE
from utils.tools import exit_code, get_encoding
from utils.uu_helper import get_valid_token_for_uu
from uuyoupinapi import UUAccount

sync_shelf_enabled = False


class Asset:
    assetid: int
    appid: int = 730
    classid: int
    instanceid: int
    contextid: int = 2
    market_hash_name: str
    orderNo: str
    price: float
    eco_stockid: int


def toECO(obj: dict):
    return {"AssetId": obj["assetid"], "Price": obj["price"], "Description": "", "SteamGameId": obj["appid"]}


def shelf_processor(assets: List[Asset]):
    processed_list = list()
    for asset in assets:
        asset_dict = {
            "assetid": asset.assetid,
            "appid": asset.appid,
            "classid": asset.classid,
            "instanceid": asset.instanceid,
            "contextid": asset.contextid,
            "market_hash_name": asset.market_hash_name,
            "price": asset.price,
            "orderNo": asset.orderNo,
        }
        processed_list.append(asset_dict)
    return processed_list


def compare_lists(A, B, ratio: float):
    result = {"add": [], "delete": [], "change": []}
    ratio = round(ratio, 2)

    # 创建字典用于快速查找
    A_dict = {item["assetid"]: item for item in A}
    B_dict = {item["assetid"]: item for item in B}

    # 查找添加和删除的字典
    for assetid in A_dict:
        if assetid not in B_dict:
            # 调整price符合比例
            adjusted_dict = A_dict[assetid].copy()
            adjusted_dict["price"] = round(adjusted_dict["price"] / ratio, 2)
            result["add"].append(adjusted_dict)

    for assetid in B_dict:
        if assetid not in A_dict:
            result["delete"].append(B_dict[assetid])

    # 查找需要更改的字典
    for assetid in A_dict:
        if assetid in B_dict:
            A_price = A_dict[assetid]["price"]
            B_price = B_dict[assetid]["price"]

            if abs(round(A_price / B_price, 2) - ratio) >= 0.01:
                # 调整B表中字典的price
                adjusted_dict = B_dict[assetid].copy()
                adjusted_dict["price"] = round(A_price / ratio, 2)
                result["change"].append(adjusted_dict)

    return result


def compare_shelf(target_shelf: List[Asset], shelf: List[Asset], ratio: float):
    processed_target_shelf = shelf_processor(target_shelf)
    processed_shelf = shelf_processor(shelf)
    output = compare_lists(processed_target_shelf, processed_shelf, ratio)
    if output == {"add": [], "delete": [], "change": []}:
        return False
    else:
        return output


class ECOsteamPlugin:
    def __init__(self, steam_client: SteamClient, steam_client_mutex, config):
        self.logger = PluginLogger("ECOsteam.cn")
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
        self.logger.info(f"ECOsteam插件已启动")
        self.logger.info("正在登录ECOsteam...")
        try:
            with open(ECOSTEAM_RSAKEY_FILE, "r", encoding=get_encoding(ECOSTEAM_RSAKEY_FILE)) as f:
                rsa_key = f.read()
            if "PUBLIC" in rsa_key:
                self.logger.error("请使用私钥文件(Private key)！")
                return 1
            LogFilter.add_sensitive_data(self.config["ecosteam"]["partnerId"])
            self.client = ECOsteamClient(
                self.config["ecosteam"]["partnerId"],
                rsa_key,
                qps=self.config["ecosteam"]["qps"],
            )
            user_info = self.client.GetTotalMoney().json()
            if user_info["ResultData"].get("UserName", None):
                self.logger.info(
                    f'登录成功，用户ID为{user_info["ResultData"]["UserName"]}，当前余额为{user_info["ResultData"]["Money"]}元'
                )
            else:
                raise Exception
        except Exception as e:
            self.logger.error(
                f"登录失败！请检查{ECOSTEAM_RSAKEY_FILE}和parterId是否正确！由于无法登录ECOsteam，插件将退出。"
            )
            handle_caught_exception(e)
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
            self.logger.error(f"当前登录的Steam账号{self.steam_id}不在ECOsteam绑定账号列表内！插件将退出。")
            exit_code.set(1)
            return 1
        if exist and len(accounts_list) > 1:
            self.logger.warning(
                f"检测到你的ECOsteam绑定了多个Steam账号。插件的所有操作仅对SteamID为{self.steam_id}的账号生效！如需同时操作多个账号，请多开Steamauto实例！"
            )

        if self.config["ecosteam"]["auto_sync_sell_shelf"]["enable"]:
            threads = []
            threads.append(Thread(target=self.auto_accept_offer))
            threads.append(Thread(target=self.auto_sync_sell_shelf))
            for thread in threads:
                thread.daemon = True
                thread.start()
            for thread in threads:
                thread.join()
        else:
            self.auto_accept_offer()

    # 自动发货相关功能
    def auto_accept_offer(self):
        while True:
            try:
                self.__auto_accept_offer()
            except Exception as e:
                handle_caught_exception(e, "ECOsteam.cn")
                self.logger.error("发生未知错误，请稍候再试！")
                time.sleep(self.config["ecosteam"]["auto_accept_offer"]["interval"])

    def __auto_accept_offer(self):
        self.logger.info("正在检查待发货列表...")
        today = datetime.datetime.today()
        tomorrow = datetime.datetime.today() + datetime.timedelta(days=1)
        last_month = today - datetime.timedelta(days=30)
        tomorrow = tomorrow.strftime("%Y-%m-%d")
        last_month = last_month.strftime("%Y-%m-%d")
        wait_deliver_orders = self.client.GetSellerOrderList(
            last_month, tomorrow, DetailsState=8, SteamId=self.steam_id
        ).json()["ResultData"]["PageResult"]
        self.logger.info(f"检测到{len(wait_deliver_orders)}个待发货订单！")
        if len(wait_deliver_orders) > 0:
            for order in wait_deliver_orders:
                self.logger.debug(f'正在获取订单号{order["OrderNum"]}的详情！')
                detail = self.client.GetSellerOrderDetail(OrderNum=order["OrderNum"]).json()["ResultData"]
                tradeOfferId = detail["TradeOfferId"]
                goodsName = detail["GoodsName"]
                if tradeOfferId not in self.ignored_offer:
                    self.logger.info(f"正在发货商品{goodsName}，报价号{tradeOfferId}...")
                    try:
                        with self.steam_client_mutex:
                            self.steam_client.accept_trade_offer(str(tradeOfferId))
                        self.ignored_offer.append(tradeOfferId)
                        self.logger.info(f"已接受报价号{tradeOfferId}！")
                    except Exception as e:
                        handle_caught_exception(e, "ECOsteam.cn")
                        self.logger.error("Steam异常, 暂时无法接受报价, 请稍后再试! ")
                else:
                    self.logger.info(f"已经自动忽略报价号{tradeOfferId}，商品名{goodsName}，因为它已经被程序处理过！")
        interval = self.config["ecosteam"]["auto_accept_offer"]["interval"]
        self.logger.info(f"等待{interval}秒后继续检查待发货列表...")
        time.sleep(interval)

    def get_steam_inventory(self):
        inventory = None
        try:
            with self.steam_client_mutex:
                inventory = self.steam_client.get_my_inventory(game=GameOptions.CS)
        except Exception as e:
            handle_caught_exception(e, "ECOsteam.cn")
            self.logger.error("Steam异常, 暂时无法获取库存, 请稍后再试! ")
        return inventory

    # 自动同步上架启动线程
    def auto_sync_sell_shelf(self):
        time.sleep(3)  # 与自动发货错开运行

        # 配置检查
        tc = copy.deepcopy(self.config["ecosteam"]["auto_sync_sell_shelf"])
        sync_shelf_enabled = True
        tc["enabled_platforms"].append("eco")
        if not tc["main_platform"] in tc["enabled_platforms"]:
            self.logger.error("主平台必须在enabled_platforms中！请重新修改检查配置文件！")
            sync_shelf_enabled = False
        platforms = list(copy.deepcopy(tc["enabled_platforms"]))
        while len(platforms) > 0:
            platform = platforms.pop()
            if not (platform == "uu" or platform == "eco" or platform == "buff"):
                self.logger.error("当前仅支持UU/ECO/BUFF平台，请检查配置！")
                sync_shelf_enabled = False
                break
        if not tc["main_platform"] in tc["enabled_platforms"]:
            self.logger.error("由于主平台未启用，自动同步平台功能已经自动关闭")
            sync_shelf_enabled = False
        if not sync_shelf_enabled:
            self.logger.error("由于配置错误，自动同步平台功能已经自动关闭")
            return

        # BUFF登录
        if "buff" in tc["enabled_platforms"]:
            self.logger.info("由于已经启用BUFF平台，正在联系BuffLoginSolver获取有效的session...")
            with self.steam_client_mutex:
                buff_session = get_valid_session_for_buff(self.steam_client, self.logger)
            if not buff_session:
                self.logger.warning("无法获取有效的BUFF session，BUFF平台相关已经自动关闭")
                tc["enabled_platforms"].remove("buff")
            else:
                self.buff_client = BuffAccount(buff_session)
                self.logger.info(f"已经获取到有效的BUFF session")

        # 悠悠登录
        if "uu" in tc["enabled_platforms"]:
            self.logger.info("由于已经启用悠悠平台，正在联系UULoginSolver获取有效的session...")
            token = get_valid_token_for_uu()
            if token:
                self.uu_client = UUAccount(token)
            else:
                self.logger.warning("无法获取有效的悠悠token，悠悠有品平台相关已经自动关闭")
                tc["enabled_platforms"].remove("uu")

        # 检查是否有平台可用
        if len(tc["enabled_platforms"]) == 1:
            self.logger.error("无平台可用。已经关闭自动同步平台功能！")
            sync_shelf_enabled = False

        while sync_shelf_enabled:
            self.sync_shelf(tc)
            self.logger.info(f'等待{tc["interval"]}秒后重新检查多平台上架物品')
            time.sleep(tc["interval"])

    def get_shelf(self, platform, inventory):
        # 如果需要下架，则返回orderNo列表，否则返回assets列表
        assets = list()
        if platform == "eco":
            result = self.client.getFullSellGoodsList(self.steam_id)
            if not inventory:
                raise SystemError
            for item in result:
                asset = Asset()
                asset.assetid = item["AssetId"]
                asset.orderNo = item["GoodsNum"]
                asset.price = float(item["Price"])
                try:
                    asset.appid = inventory[asset.assetid]["appid"]
                    asset.classid = inventory[asset.assetid]["classid"]
                    asset.contextid = inventory[asset.assetid]["contextid"]
                    asset.instanceid = inventory[asset.assetid]["instanceid"]
                    asset.market_hash_name = inventory[asset.assetid]["market_hash_name"]
                    assets.append(asset)
                except KeyError:
                    self.logger.warning(f"检测到ECOsteam上架物品 {item['GoodsName']} 不在Steam库存中！")
                    assets.append(asset.orderNo)
            return assets
        elif platform == "buff":
            data = self.buff_client.get_on_sale().json()["data"]
            items = data["items"]
            for item in items:
                asset = Asset()
                asset.assetid = item["asset_info"]["assetid"]
                asset.orderNo = item["id"]
                asset.price = float(item["price"])
                try:
                    asset.appid = inventory[asset.assetid]["appid"]
                    asset.classid = inventory[asset.assetid]["classid"]
                    asset.contextid = inventory[asset.assetid]["contextid"]
                    asset.instanceid = inventory[asset.assetid]["instanceid"]
                    asset.market_hash_name = inventory[asset.assetid]["market_hash_name"]
                    assets.append(asset)
                except KeyError:
                    self.logger.warning(
                        f"检测到BUFF上架物品 {data['goods_infos'][str(item['goods_id'])]['market_hash_name']} 不在Steam库存中！"
                    )
                    assets.append(asset.orderNo)

            return assets
        elif platform == "uu":
            data = self.uu_client.get_sell_list()
            for item in data:
                asset = Asset()
                asset.assetid = str(item["steamAssetId"])
                asset.price = float(item["sellAmount"])
                asset.orderNo = item["id"]

                try:
                    asset.appid = inventory[asset.assetid]["appid"]
                    asset.classid = inventory[asset.assetid]["classid"]
                    asset.contextid = inventory[asset.assetid]["contextid"]
                    asset.instanceid = inventory[asset.assetid]["instanceid"]
                    asset.market_hash_name = inventory[asset.assetid]["market_hash_name"]
                    assets.append(asset)
                except KeyError:
                    self.logger.warning(f"检测到悠悠上架物品不在Steam库存中！")  # TODO: 提示物品名
                    assets.append(asset.orderNo)

            return assets

    # 轮询实现
    def sync_shelf(self, tc):
        main_platform = tc["main_platform"]
        shelves = {}
        ratios = {}
        for platform in tc["enabled_platforms"]:
            shelves[platform] = list()
            ratios[platform] = tc["ratio"][platform]
        self.logger.info("正在从Steam获取库存信息...")
        inventory = self.get_steam_inventory()

        try:
            for platform in tc["enabled_platforms"]:
                self.logger.info(f"正在从{platform.upper()}平台获取上架物品信息...")
                shelves[platform] = self.get_shelf(platform, inventory)
                # 判断是否需要下架
                if len(shelves[platform]) > 0:
                    offshelf_list = []
                    for good in shelves[platform]:
                        if not isinstance(good, Asset):
                            offshelf_list.append(good)
                    if len(offshelf_list) > 0:
                        self.logger.warning(
                            f"检测到{platform.upper()}平台上架的{len(offshelf_list)}个物品不在Steam库存中！即将下架！"
                        )
                        if platform == "eco":
                            response = self.client.OffshelfGoods(
                                {"goodsNumList": [{"GoodsNum": good, "SteamGameId": 730} for good in offshelf_list]}
                            )
                            if response.json()["ResultCode"] == "0":
                                self.logger.info(f"下架{len(offshelf_list)}个商品成功！")
                            else:
                                self.logger.error(
                                    f'下架{len(offshelf_list)}个商品失败！错误信息{response.json().get("ResultMsg", None)}'
                                )
                        elif platform == "buff":
                            try:
                                count = self.buff_client.cancel_sale(offshelf_list)
                                self.logger.info(f"下架{count}个商品成功！下架{len(offshelf_list) - count}个商品失败！")
                            except Exception as e:
                                handle_caught_exception(e, "ECOsteam.cn")
                                self.logger.error(f"下架商品失败！可能有部分下架成功")

                        elif platform == "uu":
                            response = self.uu_client.off_shelf(offshelf_list)
                            if int(response.json()["code"]) == "0":
                                self.logger.info(f"下架{len(offshelf_list)}个商品成功！")
                            else:
                                self.logger.error(f"下架{len(offshelf_list)}个商品失败！错误信息{str(response.json())}")
                        # 重新获取上架物品
                        shelves[platform] = self.get_shelf(platform, inventory)
        except Exception as e:
            handle_caught_exception(e, "ECOsteam.cn")

        for platform in tc["enabled_platforms"]:
            if platform != main_platform:
                difference = compare_shelf(
                    shelves[main_platform],
                    shelves[platform],
                    ratios[main_platform] / ratios[platform],
                )
                self.logger.debug(f"当前平台：{platform.upper()}\nDifference: {json.dumps(difference)}")
                if difference:
                    self.logger.debug(json.dumps(difference))
                    self.logger.info(f"{platform.upper()}平台需要更新上架商品/价格")
                    try:
                        self.solve_platform_difference(platform, difference)
                    except Exception as e:
                        handle_caught_exception(e, "ECOsteam.cn")
                        self.logger.error("发生未知错误，请稍候再试！")
                else:
                    self.logger.info(f"{platform.upper()}平台已经保持同步")

    def solve_platform_difference(self, platform, difference):
        if platform == "eco":
            # 上架商品
            assets = [toECO(asset) for asset in difference["add"]]
            if len(assets) > 0:
                self.logger.info(f"即将上架{len(assets)}个商品到ECOsteam")

                def publish_assets_in_batches(assets, batch_size=100):
                    batches = [assets[i : i + batch_size] for i in range(0, len(assets), batch_size)]
                    return batches

                try:
                    batches = publish_assets_in_batches(assets)
                    for batch in batches:
                        response = self.client.PublishStock({"Assets": batch})
                        self.logger.info(f"上架{len(batch)}个商品到ECOsteam成功！")
                except Exception as e:
                    if "饰品状态变化" in str(e):
                        self.logger.info("ECO平台库存数据已过期，正在请求刷新...")
                        self.client.RefreshUserSteamStock()
                        self.logger.info("已经请求刷新，将在30秒后重新尝试上架！")
                        time.sleep(30)
                        self.logger.info("正在重新尝试上架...")
                        try:
                            for batch in batches:
                                response = self.client.PublishStock({"Assets": batch})
                                self.logger.info(f"上架{len(batch)}个商品到ECOsteam成功！")
                        except Exception as e:
                            handle_caught_exception(e, "ECOsteam.cn")
                            self.logger.error("发生未知错误，请稍候再试！")
                            return
                    else:
                        handle_caught_exception(e, "ECOsteam.cn")
                        self.logger.error("发生未知错误，请稍候再试！")
                        return

            # 下架商品
            assets = [asset["orderNo"] for asset in difference["delete"]]
            if len(assets) > 0:
                self.logger.info(f"即将在{platform.upper()}平台下架{len(assets)}个商品")
                response = self.client.OffshelfGoods(
                    {"goodsNumList": [{"GoodsNum": good, "SteamGameId": 730} for good in assets]}
                )

                self.logger.info(f"下架{len(assets)}个商品成功！")

            # 修改价格
            assets = [{"GoodsNum": asset["orderNo"], "SellingPrice": asset["price"]} for asset in difference["change"]]
            if len(assets) > 0:
                self.logger.info(f"即将在{platform.upper()}平台修改{len(assets)}个商品的价格")
                if len(assets) > 100:
                    self.logger.warning("ECOsteam平台一次最多支持100个商品修改价格，将分批次修改")
                    for i in range(0, len(assets), 100):
                        self.client.GoodsPublishedBatchEdit({"goodsBatchEditList": assets[i : i + 100]})
                        self.logger.info(f"修改{len(assets[i:i+100])}个商品的价格成功！")
                        if i + 100 < len(assets):
                            self.logger.info(f"等待5秒后继续修改...")
                            time.sleep(5)
                self.logger.info(f"修改{len(assets)}个商品的价格成功！")

        elif platform == "buff":
            # 上架商品
            assets = difference["add"]
            if len(assets) > 0:
                buff_assets = [
                    {
                        "appid": asset["appid"],
                        "assetid": asset["assetid"],
                        "classid": asset["classid"],
                        "instanceid": asset["instanceid"],
                        "contextid": asset["contextid"],
                        "market_hash_name": asset["market_hash_name"],
                        "price": asset["price"],
                        "market_hash_name": asset["market_hash_name"],
                        "price": asset["price"],
                        "income": asset["price"],
                        "desc": "",
                    }
                    for asset in assets
                ]
                self.logger.info(f"即将上架{len(assets)}个商品到BUFF")
                try:
                    success = self.buff_client.on_sale(buff_assets)
                    self.logger.info(
                        f"上架{len(success)}个商品到BUFF成功！上架{len(assets) - len(success)}个商品失败！"
                    )
                except Exception as e:
                    handle_caught_exception(e, "ECOsteam.cn")
                    self.logger.error(f"上架商品失败！可能部分上架成功！")

            # 下架商品
            assets = difference["delete"]
            if len(assets) > 0:
                sell_orders = [asset["orderNo"] for asset in difference["delete"]]
                self.logger.info(f"即将在{platform.upper()}平台下架{len(assets)}个商品")
                try:
                    count = self.buff_client.cancel_sale(sell_orders)
                    self.logger.info(f"下架{count}个商品成功！下架{len(assets) - count}个商品失败！")
                except Exception as e:
                    handle_caught_exception(e, "ECOsteam.cn")
                    self.logger.error(f"下架商品失败！可能部分下架成功！")

            # 更改价格
            assets = difference["change"]
            if len(assets) > 0:
                sell_orders = [
                    {
                        "sell_order_id": asset["orderNo"],
                        "price": asset["price"],
                        "income": asset["price"],
                        "desc": "",
                    }
                    for asset in assets
                ]
                self.logger.info(f"即将在{platform.upper()}平台修改{len(assets)}个商品的价格")
                success, problem_sell_orders = self.buff_client.change_price(sell_orders)
                for sell_order in problem_sell_orders.keys():
                    for asset in assets:
                        if sell_order == asset["orderNo"]:
                            self.logger.error(f"修改 {asset['market_hash_name']}(ID:{asset['assetid']}) 的价格失败！错误信息: {problem_sell_orders[sell_order]}")
                self.logger.info(f"修改{success}个商品的价格成功！")
        elif platform == "uu":
            # 上架商品
            add = difference["add"]
            assets = dict()
            for item in add:
                assets[item["assetid"]] = item["price"]
            if len(assets) > 0:
                self.logger.info(f"即将上架{len(assets)}个商品到UU有品")
                response = self.uu_client.sell_items(assets)
                if int(response.json()["Code"]) == 0:
                    self.logger.info(f"上架{len(assets)}个商品到UU有品成功！")
                else:
                    self.logger.error(
                        f'上架{len(assets)}个商品到UU有品失败(可能部分上架成功)！错误信息：{str(response.json()["Msg"])}'
                    )

            # 下架商品
            delete = difference["delete"]
            assets = [str(item["orderNo"]) for item in delete]
            if len(assets) > 0:
                self.logger.info(f"即将在{platform.upper()}平台下架{len(assets)}个商品")
                response = self.uu_client.off_shelf(assets)
                if int(response.json()["Code"]) == 0:
                    self.logger.info(f"下架{len(assets)}个商品成功！")
                else:
                    self.logger.error(f'下架{len(assets)}个商品失败！错误信息：{str(response.json()["Msg"])}')

            # 修改价格
            change = difference["change"]
            assets = dict()
            for item in change:
                assets[item["orderNo"]] = item["price"]
            if len(assets) > 0:
                self.logger.info(f"即将在{platform.upper()}平台修改{len(assets)}个商品的价格")
                response = self.uu_client.change_price(assets)
                if int(response.json()["Code"]) == 0:
                    self.logger.info(f"修改{len(assets)}个商品的价格成功！悠悠平台刷新可能有延迟，请稍候几分钟后查看！")
                else:
                    self.logger.error(
                        f'修改{len(assets)}个商品的价格失败(可能部分修改成功)！错误信息：{str(response.json()["Msg"])}'
                    )
