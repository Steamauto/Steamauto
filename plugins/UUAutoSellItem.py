import datetime
import random
import time

import schedule

import uuyoupinapi
from utils.logger import PluginLogger, handle_caught_exception, logger
from utils.notifier import send_notification
from utils.tools import exit_code
from utils.uu_helper import get_valid_token_for_uu

# 将sale_price_cache从实例变量改为模块级变量
sale_price_cache = {}


class UUAutoSellItem:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.logger = PluginLogger("UUAutoSellItem")
        self.config = config
        self.timeSleep = 10.0
        self.inventory_list = []
        self.buy_price_cache = {}
        self.sale_inventory_list = None
        self.steam_client = steam_client

    def init(self) -> bool:
        return False

    def get_uu_sale_inventory(self):
        try:
            sale_inventory_list = self.uuyoupin.get_sell_list()
            self.logger.info(f"已上架物品数量 {len(sale_inventory_list)}")
            self.sale_inventory_list = sale_inventory_list
            return sale_inventory_list
        except Exception as e:
            self.logger.error(f"获取UU上架物品失败! 错误: {e}", exc_info=True)
            return []

    def get_market_sale_price(self, item_id, cnt=10, good_name=None):
        if item_id in sale_price_cache:
            if datetime.datetime.now() - sale_price_cache[item_id]["cache_time"] <= datetime.timedelta(minutes=5):
                commodity_name = sale_price_cache[item_id]["commodity_name"]
                sale_price = sale_price_cache[item_id]["sale_price"]
                self.logger.info(f"{commodity_name} 使用缓存结果，出售价格： {sale_price:.2f}")
                return sale_price

        sale_price_rsp = self.uuyoupin.get_market_sale_list_with_abrade(item_id).json()
        if sale_price_rsp["Code"] == 0:
            rsp_list = sale_price_rsp["Data"]
            rsp_cnt = len(rsp_list)
            if rsp_cnt == 0:
                sale_price = 0
                commodity_name = ""
                self.logger.warning(f"市场上没有指定筛选条件的物品")
                return sale_price
            commodity_name = rsp_list[0]["commodityName"]

            sale_price_list = []
            cnt = min(cnt, rsp_cnt)
            for i in range(cnt):
                if rsp_list[i]["price"] and i < cnt:
                    sale_price_list.append(float(rsp_list[i]["price"]))

            if len(sale_price_list) == 1:
                sale_price = sale_price_list[0]
            elif len(sale_price_list) > 1:
                sale_price_list.sort()
                # 检索这个区间里的最低的两个价格，价格差距在5%以内就按最低定价，差距大于5%就按价格更高的定
                minPrice = min(sale_price_list[0], sale_price_list[1])
                if sale_price_list[1] < minPrice * 1.05:
                    sale_price = minPrice
                else:
                    sale_price = sale_price_list[1]

            self.logger.info(f"物品名称：{commodity_name}，出售价格：{sale_price:.2f}, 参考价格列表：{sale_price_list}")
        else:
            sale_price = 0
            commodity_name = ""
            self.logger.error(f"查询出售价格失败，返回结果：{sale_price_rsp['Code']}，全部内容：{sale_price_rsp}")

        sale_price = round(sale_price, 2)

        if sale_price != 0:
            sale_price_cache[item_id] = {
                "commodity_name": commodity_name,
                "sale_price": sale_price,
                "cache_time": datetime.datetime.now(),
            }

        return sale_price

    def sell_item(self, items):
        item_infos = items
        num = len(item_infos)
        if num == 0:
            self.logger.info(f"没有物品可以出售")
            return 0

        try:
            rsp = self.uuyoupin.call_api(
                "POST",
                "/api/commodity/Inventory/SellInventoryWithLeaseV2",
                data={"GameId": "730", "itemInfos": item_infos},  # Csgo
            ).json()
            if rsp["Code"] == 0:
                success_count = len(item_infos)
                self.logger.info(f"成功上架 {success_count} 个物品")
                return success_count
            else:
                self.logger.error(f"上架失败，返回结果：{rsp['Code']}，全部内容：{rsp}")
                return -1
        except Exception as e:
            self.logger.error(f"调用 SellInventoryWithLeaseV2 上架失败: {e}", exc_info=True)
            return -1

    def change_sale_price(self, items):
        item_infos = items
        num = len(item_infos)
        if num == 0:
            self.logger.info(f"没有物品可以修改价格")
            return 0

        try:
            rsp = self.uuyoupin.call_api(
                "PUT",
                "/api/commodity/Commodity/PriceChangeWithLeaseV2",
                data={
                    "Commoditys": item_infos,
                },
            ).json()
            if rsp["Code"] == 0:
                success_count = 0
                fail_count = 0
                data_section = rsp.get("Data", {})

                if isinstance(data_section, dict) and "Commoditys" in data_section:
                    total_processed = len(data_section["Commoditys"])
                    for commodity_result in data_section["Commoditys"]:
                        if commodity_result.get("IsSuccess") == 1:
                            success_count += 1
                        else:
                            fail_count += 1
                            error_msg = commodity_result.get("Message", "未知错误")
                            comm_id = commodity_result.get("CommodityId", "未知ID")
                            self.logger.error(f"修改商品 {comm_id} 价格失败: {error_msg}")

                    if "SuccessCount" in data_section:
                        success_count = data_section.get("SuccessCount", success_count)
                        fail_count = data_section.get("FailCount", fail_count)

                if total_processed == 0 and success_count == 0 and fail_count == 0:
                    success_count = num

                self.logger.info(f"尝试修改 {num} 个物品价格，成功 {success_count} 个，失败 {fail_count} 个")
                return success_count
            else:
                self.logger.error(f"修改出售价格失败，返回结果：{rsp['Code']}，全部内容：{rsp}")
                return -1
        except Exception as e:
            self.logger.error(f"调用 PriceChangeWithLeaseV2 修改价格失败: {e}", exc_info=True)
            return -1

    def auto_sell(self):
        self.logger.info("悠悠有品出售自动上架插件已启动")
        self.operate_sleep()

        if self.uuyoupin is not None:
            try:
                sale_item_list = []
                self.uuyoupin.send_device_info()
                self.logger.info("正在获取悠悠有品库存...")

                self.inventory_list = self.uuyoupin.get_inventory(refresh=True)

                for i, item in enumerate(self.inventory_list):
                    if item["AssetInfo"] is None:
                        continue
                    asset_id = item["SteamAssetId"]
                    item_id = item["TemplateInfo"]["Id"]
                    short_name = item["TemplateInfo"]["CommodityName"]
                    buy_price = float(item.get("AssetBuyPrice", "0").replace("购￥", ""))

                    self.buy_price_cache[item_id] = buy_price

                    if item["Tradable"] is False or item["AssetStatus"] != 0:
                        continue

                    if not any((s and s in short_name) for s in self.config["uu_auto_sell_item"]["name"]):
                        continue

                    blacklist_words = self.config["uu_auto_sell_item"].get("blacklist_words", [])
                    if blacklist_words:
                        if any(s != "" and s in short_name for s in blacklist_words):
                            self.logger.info(f"物品 {short_name} 命中黑名单，将不会上架")
                            continue

                    try:
                        sale_price = self.get_market_sale_price(item_id, good_name=short_name)
                    except Exception as e:
                        handle_caught_exception(e, "UUAutoSellItem", known=True)
                        logger.error(f"获取 {short_name} 的市场价格失败: {e}，暂时跳过")
                        continue

                    if self.config["uu_auto_sell_item"]["take_profile"]:
                        self.logger.info(f"按{self.config['uu_auto_sell_item']['take_profile_ratio']:.2f}止盈率设置价格")
                        if buy_price > 0:
                            sale_price = max(sale_price, self.get_take_profile_price(buy_price))
                            self.logger.info(f"最终出售价格{sale_price:.2f}")
                        else:
                            self.logger.info("未获取到购入价格")

                    if sale_price == 0:
                        continue

                    price_threshold = self.config["uu_auto_sell_item"].get("price_adjustment_threshold", 1.0)
                    if self.config["uu_auto_sell_item"].get("use_price_adjustment", True):
                        if sale_price > price_threshold:
                            sale_price = max(price_threshold, sale_price - 0.01)
                            sale_price = round(sale_price, 2)

                    max_price = self.config["uu_auto_sell_item"].get("max_on_sale_price", 0)
                    if max_price > 0 and sale_price > max_price:
                        self.logger.info(f"物品 {short_name} 的价格超过了设定的最高价格，将不会上架")
                        continue

                    self.logger.warning(f"即将上架：{short_name} 价格：{sale_price}")

                    sale_item = {
                        "AssetId": asset_id,
                        "IsCanLease": False,
                        "IsCanSold": True,
                        "Price": sale_price,
                        "Remark": "",
                    }

                    sale_item_list.append(sale_item)

                self.logger.info(f"上架{len(sale_item_list)} 件物品中...")

                self.operate_sleep()
                self.sell_item(sale_item_list)
                self.logger.info("上架完成")

            except TypeError as e:
                handle_caught_exception(e, "UUAutoSellItem")
                self.logger.error("悠悠有品出售自动上架出现错误")
                exit_code.set(1)
                return 1
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("出现未知错误, 稍后再试! ")
                try:
                    self.uuyoupin.get_user_nickname()
                except KeyError as e:
                    handle_caught_exception(e, "UUAutoSellItem", known=True)
                    self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                    send_notification(self.steam_client, "检测到悠悠有品登录已经失效,请重新登录", title="悠悠有品登录失效")
                    self.logger.error("由于登录失败，插件将自动退出")
                    exit_code.set(1)
                    return 1

    def auto_change_price(self):
        self.logger.info("悠悠有品出售自动修改价格已启动")
        self.operate_sleep()

        try:
            self.uuyoupin.send_device_info()
            self.logger.info("正在获取悠悠有品出售已上架物品...")
            self.get_uu_sale_inventory()

            new_sale_item_list = []
            if not self.sale_inventory_list:
                self.logger.info("没有可用于改价的在售物品")
                return
            for i, item in enumerate(self.sale_inventory_list):
                asset_id = item["id"]
                item_id = item["templateId"]
                short_name = item["name"]
                buy_price = self.buy_price_cache.get(item_id, 0)

                if not any((s and s in short_name) for s in self.config["uu_auto_sell_item"]["name"]):
                    continue

                blacklist_words = self.config["uu_auto_sell_item"].get("blacklist_words", [])
                if blacklist_words:
                    if any(s != "" and s in short_name for s in blacklist_words):
                        self.logger.info(f"改价跳过：{short_name} 命中黑名单")
                        continue

                sale_price = self.get_market_sale_price(item_id, good_name=short_name)

                if self.config["uu_auto_sell_item"]["take_profile"]:
                    self.logger.info(f"按{self.config['uu_auto_sell_item']['take_profile_ratio']:.2f}止盈率设置价格")
                    if buy_price > 0:
                        self.logger.debug(sale_price)
                        self.logger.debug(self.get_take_profile_price(buy_price))
                        sale_price = max(sale_price, self.get_take_profile_price(buy_price))
                        self.logger.info(f"最终出售价格{sale_price:.2f}")
                    else:
                        self.logger.info("未获取到购入价格")

                if sale_price == 0:
                    continue

                price_threshold = self.config["uu_auto_sell_item"].get("price_adjustment_threshold", 1.0)
                if self.config["uu_auto_sell_item"].get("use_price_adjustment", True):
                    if sale_price > price_threshold:
                        sale_price = max(price_threshold, sale_price - 0.01)
                        sale_price = round(sale_price, 2)

                sale_item = {"CommodityId": asset_id, "IsCanLease": False, "IsCanSold": True, "Price": sale_price, "Remark": ""}
                new_sale_item_list.append(sale_item)

            self.logger.info(f"{len(new_sale_item_list)} 件物品可以更新出售价格")
            self.operate_sleep()
            self.change_sale_price(new_sale_item_list)

        except TypeError as e:
            handle_caught_exception(e, "UUAutoSellItem-AutoChangePrice")
            self.logger.error("悠悠有品出售自动上架出现错误")
            exit_code.set(1)
            return 1
        except Exception as e:
            self.logger.error(e, exc_info=True)
            self.logger.info("出现未知错误, 稍后再试! ")
            try:
                self.uuyoupin.get_user_nickname()
            except KeyError as e:
                handle_caught_exception(e, "UUAutoSellItem-AutoChangePrice", known=True)
                send_notification(self.steam_client, "检测到悠悠有品登录已经失效,请重新登录", title="悠悠有品登录失效")
                self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                self.logger.error("由于登录失败，插件将自动退出")
                exit_code.set(1)
                return 1

    def exec(self):
        self.uuyoupin = uuyoupinapi.UUAccount(get_valid_token_for_uu())  # type: ignore
        if not self.uuyoupin:
            self.logger.error("由于登录失败，插件将自动退出")
            exit_code.set(1)
            return 1
        self.logger.info(f"以下物品会出售：{self.config['uu_auto_sell_item']['name']}")
        self.auto_sell()

        run_time = self.config["uu_auto_sell_item"]["run_time"]
        interval = self.config["uu_auto_sell_item"]["interval"]

        self.logger.info(f"[自动出售] 等待到 {run_time} 开始执行")
        self.logger.info(f"[自动修改价格] 每隔 {interval} 分钟执行一次")

        schedule.every().day.at(f"{run_time}").do(self.auto_sell)
        schedule.every(interval).minutes.do(self.auto_change_price)

        while True:
            schedule.run_pending()
            time.sleep(1)

    def operate_sleep(self, sleep=None):
        if sleep is None:
            random.seed()
            sleep = random.randint(5, 15)
        self.logger.info(f"为了避免频繁访问接口，操作间隔 {sleep} 秒")
        time.sleep(sleep)

    def get_take_profile_price(self, buy_price):
        take_profile_ratio = self.config["uu_auto_sell_item"]["take_profile_ratio"]
        return buy_price * (1 + take_profile_ratio)
