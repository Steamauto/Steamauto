"""悠悠有品（UU）插件：自动发货 + 自动出售 + 自动出租，三个功能模块合并为一个插件。

由原 UUAutoAcceptOffer / UUAutoSellItem / UUAutoLeaseItem 三插件合并而来：
- accept_offer：自动发货（轮询待发货报价 → 接受 Steam 报价）
- sell_item：自动出售上架 + 定时改价
- lease_item：自动出租上架 + 改价 + 0cd 设置

三者共享同一个 UUAccount（token 只登录一次），配置统一收在 config 的 ``uu`` 段：
``uu.accept_offer`` / ``uu.sell_item`` / ``uu.lease_item``。
"""

import datetime
import random
import time
from threading import Thread

import schedule

import api.uuyoupinapi as uuyoupinapi
from api.uuyoupinapi import models
from utils import runtime
from utils.logger import PluginLogger, handle_caught_exception, logger
from utils.notifier import send_notification
from utils.tools import exit_code, is_subsequence
from utils.uu_helper import get_valid_token_for_uu

# 出售价格缓存（模块级，跨实例共享）
sale_price_cache = {}


def _mean(values):
    return sum(values) / len(values) if values else 0.0


class UUAuto:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.logger = PluginLogger("UUAuto")
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        self.uuyoupin = None
        # 出售模块状态
        self.inventory_list = []
        self.buy_price_cache = {}
        self.sale_inventory_list = None
        # 出租模块状态
        self.lease_price_cache = {}
        self.compensation_type = 0

    @property
    def leased_inventory_list(self) -> list:
        return self.uuyoupin.get_uu_leased_inventory()

    def init(self) -> bool:
        uu_cfg = self.config.get("uu", {})
        any_enabled = (
            uu_cfg.get("accept_offer", {}).get("enable")
            or uu_cfg.get("sell_item", {}).get("enable")
            or uu_cfg.get("lease_item", {}).get("enable")
        )
        if not any_enabled:
            return False
        token = get_valid_token_for_uu(self.steam_client)
        if not token:
            self.logger.error("悠悠有品登录失败！即将关闭程序！")
            exit_code.set(1)
            return True
        self.uuyoupin = uuyoupinapi.UUAccount(token)
        self.logger = PluginLogger(f"UUAuto-{self.uuyoupin.get_user_nickname()}-steam:{self.steam_client.username}")
        return False

    def exec(self):
        uu_cfg = self.config.get("uu", {})
        accept_cfg = uu_cfg.get("accept_offer", {})
        sell_cfg = uu_cfg.get("sell_item", {})
        lease_cfg = uu_cfg.get("lease_item", {})

        # 出售模块
        if sell_cfg.get("enable"):
            self.logger.info(f"以下物品会出售：{sell_cfg['name']}")
            self.auto_sell()
            schedule.every().day.at(sell_cfg["run_time"]).do(self.auto_sell)
            schedule.every(sell_cfg["interval"]).minutes.do(self.sell_change_price)
            self.logger.info(f"[自动出售] 等待到 {sell_cfg['run_time']} 开始执行")
            self.logger.info(f"[自动修改价格] 每隔 {sell_cfg['interval']} 分钟执行一次")

        # 出租模块
        if lease_cfg.get("enable"):
            self.logger.info(f"以下物品不会出租：{lease_cfg['filter_name']}")
            self.compensation_type = lease_cfg.get("compensation_type", 0)
            self.pre_check_price()
            self.auto_lease()
            self.auto_set_zero_cd()
            zero_cd_run_time = lease_cfg.get("zero_cd_run_time", "23:30")
            schedule.every().day.at(lease_cfg["run_time"]).do(self.auto_lease)
            schedule.every(lease_cfg["interval"]).minutes.do(self.lease_change_price)
            schedule.every().day.at(zero_cd_run_time).do(self.auto_set_zero_cd)
            self.logger.info(f"[自动出租] 等待到 {lease_cfg['run_time']} 开始执行。")
            self.logger.info(f"[自动修改价格] 每隔 {lease_cfg['interval']} 分钟执行一次。")
            self.logger.info(f"[设置0cd] 等待到 {zero_cd_run_time} 开始执行。")

        # 发货模块（独立线程轮询）
        if accept_cfg.get("enable"):
            Thread(target=self._accept_offer_loop, daemon=True).start()

        while not runtime.shutdown_event.is_set():
            schedule.run_pending()
            runtime.interruptible_sleep(1)

    def operate_sleep(self, sleep=None):
        if sleep is None:
            random.seed()
            sleep = random.randint(5, 15)
        self.logger.info(f"为了避免频繁访问接口，操作间隔 {sleep} 秒")
        runtime.interruptible_sleep(sleep)

    # ==================== 发货模块（原 UUAutoAcceptOffer） ====================

    def _accept_offer_loop(self):
        ignored_offer = {}
        accept_cfg = self.config["uu"]["accept_offer"]
        while not runtime.shutdown_event.is_set():
            try:
                self.uuyoupin.send_device_info()
                self.logger.info("正在检查悠悠有品待发货信息...")
                uu_wait_deliver_list = self.uuyoupin.get_wait_deliver_list()
                len_uu_wait_deliver_list = len(uu_wait_deliver_list)
                self.logger.info("" + str(len_uu_wait_deliver_list) + "个悠悠有品待发货订单")
                if len(uu_wait_deliver_list) != 0:
                    for item in uu_wait_deliver_list:
                        accepted = False
                        self.logger.info(f"正在接受悠悠有品待发货报价, 商品名: {item['item_name']}, 报价ID: {item['offer_id']}")
                        if item["offer_id"] is None:
                            self.logger.warning("此订单为需要手动发货(或异常)的订单, 不能自动处理, 跳过此订单! ")
                        elif item["offer_id"] in ignored_offer and ignored_offer[item["offer_id"]] <= 10:
                            self.logger.info("此交易报价已经被Steamauto处理过, 出现此提示的原因是悠悠系统延迟或者该订单为批量购买订单.这不是一个报错!")
                            ignored_offer[item["offer_id"]] += 1
                        else:
                            from utils.steam_client import accept_trade_offer

                            if accept_trade_offer(self.steam_client, self.steam_client_mutex, str(item["offer_id"]), desc=f"发货平台：悠悠有品\n发货饰品：{item['item_name']}"):
                                ignored_offer[str(item["offer_id"])] = 1
                                self.logger.info(f"接受报价[{str(item['offer_id'])}]完成!")
                                accepted = True
                        if (uu_wait_deliver_list.index(item) != len_uu_wait_deliver_list - 1) and accepted:
                            self.logger.info("为了避免频繁访问Steam接口, 等待5秒后继续...")
                            runtime.interruptible_sleep(5)
            except Exception as e:
                if "登录状态失效，请重新登录" in str(e):
                    handle_caught_exception(e, "UUAuto", known=True)
                    send_notification(self.steam_client, "检测到悠悠有品登录已经失效,请重新登录", title="悠悠有品登录失效")
                    self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                    self.logger.error("由于登录失败，插件将自动退出")
                    exit_code.set(1)
                    return 1
                else:
                    handle_caught_exception(e, "UUAuto", known=False)
                    self.logger.error("出现未知错误, 稍后再试! ")
            interval = accept_cfg["interval"]
            self.logger.info("将在{0}秒后再次检查待发货订单信息!".format(str(interval)))
            runtime.interruptible_sleep(interval)

    # ==================== 出售模块（原 UUAutoSellItem） ====================

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
        num = len(items)
        if num == 0:
            self.logger.info(f"没有物品可以出售")
            return 0

        try:
            result = self.uuyoupin.sell_items(items)
            success_count = result["success"]
            self.logger.info(f"成功上架 {success_count} 个物品")
            return success_count
        except Exception as e:
            self.logger.error(f"调用 SellInventoryWithLeaseV2 上架失败: {e}", exc_info=True)
            return -1

    def change_sale_price(self, items):
        num = len(items)
        if num == 0:
            self.logger.info(f"没有物品可以修改价格")
            return 0

        try:
            rsp = self.uuyoupin.change_items_price_v2(items).json()
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

    def get_take_profile_price(self, buy_price):
        take_profile_ratio = self.config["uu"]["sell_item"]["take_profile_ratio"]
        return buy_price * (1 + take_profile_ratio)

    def auto_sell(self):
        self.logger.info("悠悠有品出售自动上架插件已启动")
        self.operate_sleep()
        sell_cfg = self.config["uu"]["sell_item"]

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

                    if not any((s and s in short_name) for s in sell_cfg["name"]):
                        continue

                    blacklist_words = sell_cfg.get("blacklist_words", [])
                    if blacklist_words:
                        if any(s != "" and s in short_name for s in blacklist_words):
                            self.logger.info(f"物品 {short_name} 命中黑名单，将不会上架")
                            continue

                    try:
                        sale_price = self.get_market_sale_price(item_id, good_name=short_name)
                    except Exception as e:
                        handle_caught_exception(e, "UUAuto", known=True)
                        logger.error(f"获取 {short_name} 的市场价格失败: {e}，暂时跳过")
                        continue

                    if sell_cfg["take_profile"]:
                        self.logger.info(f"按{sell_cfg['take_profile_ratio']:.2f}止盈率设置价格")
                        if buy_price > 0:
                            sale_price = max(sale_price, self.get_take_profile_price(buy_price))
                            self.logger.info(f"最终出售价格{sale_price:.2f}")
                        else:
                            self.logger.info("未获取到购入价格")

                    if sale_price == 0:
                        continue

                    price_threshold = sell_cfg.get("price_adjustment_threshold", 1.0)
                    if sell_cfg.get("use_price_adjustment", True):
                        if sale_price > price_threshold:
                            sale_price = max(price_threshold, sale_price - 0.01)
                            sale_price = round(sale_price, 2)

                    max_price = sell_cfg.get("max_on_sale_price", 0)
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
                handle_caught_exception(e, "UUAuto")
                self.logger.error("悠悠有品出售自动上架出现错误")
                exit_code.set(1)
                return 1
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("出现未知错误, 稍后再试! ")
                try:
                    self.uuyoupin.get_user_nickname()
                except KeyError as e:
                    handle_caught_exception(e, "UUAuto", known=True)
                    self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                    send_notification(self.steam_client, "检测到悠悠有品登录已经失效,请重新登录", title="悠悠有品登录失效")
                    self.logger.error("由于登录失败，插件将自动退出")
                    exit_code.set(1)
                    return 1

    def sell_change_price(self):
        self.logger.info("悠悠有品出售自动修改价格已启动")
        self.operate_sleep()
        sell_cfg = self.config["uu"]["sell_item"]

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

                if not any((s and s in short_name) for s in sell_cfg["name"]):
                    continue

                blacklist_words = sell_cfg.get("blacklist_words", [])
                if blacklist_words:
                    if any(s != "" and s in short_name for s in blacklist_words):
                        self.logger.info(f"改价跳过：{short_name} 命中黑名单")
                        continue

                sale_price = self.get_market_sale_price(item_id, good_name=short_name)

                if sell_cfg["take_profile"]:
                    self.logger.info(f"按{sell_cfg['take_profile_ratio']:.2f}止盈率设置价格")
                    if buy_price > 0:
                        self.logger.debug(sale_price)
                        self.logger.debug(self.get_take_profile_price(buy_price))
                        sale_price = max(sale_price, self.get_take_profile_price(buy_price))
                        self.logger.info(f"最终出售价格{sale_price:.2f}")
                    else:
                        self.logger.info("未获取到购入价格")

                if sale_price == 0:
                    continue

                price_threshold = sell_cfg.get("price_adjustment_threshold", 1.0)
                if sell_cfg.get("use_price_adjustment", True):
                    if sale_price > price_threshold:
                        sale_price = max(price_threshold, sale_price - 0.01)
                        sale_price = round(sale_price, 2)

                sale_item = {"CommodityId": asset_id, "IsCanLease": False, "IsCanSold": True, "Price": sale_price, "Remark": ""}
                new_sale_item_list.append(sale_item)

            self.logger.info(f"{len(new_sale_item_list)} 件物品可以更新出售价格")
            self.operate_sleep()
            self.change_sale_price(new_sale_item_list)

        except TypeError as e:
            handle_caught_exception(e, "UUAuto-AutoChangePrice")
            self.logger.error("悠悠有品出售自动上架出现错误")
            exit_code.set(1)
            return 1
        except Exception as e:
            self.logger.error(e, exc_info=True)
            self.logger.info("出现未知错误, 稍后再试! ")
            try:
                self.uuyoupin.get_user_nickname()
            except KeyError as e:
                handle_caught_exception(e, "UUAuto-AutoChangePrice", known=True)
                send_notification(self.steam_client, "检测到悠悠有品登录已经失效,请重新登录", title="悠悠有品登录失效")
                self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                self.logger.error("由于登录失败，插件将自动退出")
                exit_code.set(1)
                return 1

    # ==================== 出租模块（原 UUAutoLeaseItem） ====================

    def get_lease_price(self, template_id, min_price=0, max_price=20000, cnt=15):
        lease_cfg = self.config["uu"]["lease_item"]
        if template_id in self.lease_price_cache:
            if datetime.datetime.now() - self.lease_price_cache[template_id]["cache_time"] <= datetime.timedelta(minutes=20):
                commodity_name = self.lease_price_cache[template_id]["commodity_name"]
                lease_unit_price = self.lease_price_cache[template_id]["lease_unit_price"]
                long_lease_unit_price = self.lease_price_cache[template_id]["long_lease_unit_price"]
                lease_deposit = self.lease_price_cache[template_id]["lease_deposit"]
                self.logger.info(f"物品 {commodity_name} 使用缓存价格设置，短租价格：{lease_unit_price:.2f}，长租价格：{long_lease_unit_price:.2f}，押金：{lease_deposit:.2f}")
                return {
                    "LeaseUnitPrice": lease_unit_price,
                    "LongLeaseUnitPrice": long_lease_unit_price,
                    "LeaseDeposit": lease_deposit,
                }
        max_price = 20000 if max_price == 0 else max_price
        rsp_list = self.uuyoupin.get_market_lease_price(template_id, min_price=min_price, max_price=max_price, cnt=cnt)
        if len(rsp_list) > 0:
            rsp_cnt = len(rsp_list)
            commodity_name = rsp_list[0].CommodityName

            lease_unit_price_list = []
            long_lease_unit_price_list = []
            lease_deposit_list = []
            for i, item in enumerate(rsp_list):
                if item.LeaseUnitPrice and i < min(10, rsp_cnt):
                    lease_unit_price_list.append(float(item.LeaseUnitPrice))
                    if item.LeaseDeposit:
                        lease_deposit_list.append(float(item.LeaseDeposit))
                if item.LongLeaseUnitPrice:
                    long_lease_unit_price_list.append(float(item.LongLeaseUnitPrice))

            if len(lease_unit_price_list) > 0:
                lease_unit_price = _mean(lease_unit_price_list) * 0.97
                lease_unit_price = max(lease_unit_price, float(lease_unit_price_list[0]), 0.01)
            else:
                lease_unit_price = 0

            if len(long_lease_unit_price_list) == 0:
                long_lease_unit_price = max(lease_unit_price - 0.01, 0.01) if lease_unit_price > 0 else 0
            else:
                long_lease_unit_price = min(lease_unit_price * 0.98, _mean(long_lease_unit_price_list) * 0.95)
                long_lease_unit_price = max(long_lease_unit_price, float(long_lease_unit_price_list[0]), 0.01)

            if len(lease_deposit_list) > 0:
                lease_deposit = max(_mean(lease_deposit_list) * 0.98, float(min(lease_deposit_list)))
            else:
                lease_deposit = 0

            self.logger.info(f"短租参考价格：{lease_unit_price_list}，长租参考价格：{long_lease_unit_price_list}")
        else:
            lease_unit_price = long_lease_unit_price = lease_deposit = 0
            commodity_name = ""

        lease_unit_price = round(lease_unit_price, 2)
        long_lease_unit_price = min(round(long_lease_unit_price, 2), lease_unit_price)
        lease_deposit = round(lease_deposit, 2)

        if lease_cfg["enable_fix_lease_ratio"] and min_price > 0:
            ratio = lease_cfg["fix_lease_ratio"]
            lease_unit_price = max(lease_unit_price, min_price * ratio)
            long_lease_unit_price = max(long_lease_unit_price, lease_unit_price * 0.98)

            self.logger.info(f"物品 {commodity_name}，启用比例定价，市场价 {min_price}，租金比例 {ratio}")

        self.logger.info(f"物品 {commodity_name}，短租价格：{lease_unit_price:.2f}，长租价格：{long_lease_unit_price:.2f}，押金：{lease_deposit:.2f}")
        if lease_unit_price != 0:
            self.lease_price_cache[template_id] = {
                "commodity_name": commodity_name,
                "lease_unit_price": lease_unit_price,
                "long_lease_unit_price": long_lease_unit_price,
                "lease_deposit": lease_deposit,
                "cache_time": datetime.datetime.now(),
            }

        return {
            "LeaseUnitPrice": lease_unit_price,
            "LongLeaseUnitPrice": long_lease_unit_price,
            "LeaseDeposit": lease_deposit,
        }

    def auto_lease(self):
        self.logger.info("悠悠有品出租自动上架插件已启动")
        self.operate_sleep()
        lease_cfg = self.config["uu"]["lease_item"]
        if self.uuyoupin is not None:
            try:
                lease_item_list = []
                self.uuyoupin.send_device_info()
                self.logger.info("正在获取悠悠有品库存...")

                self.inventory_list = self.uuyoupin.get_inventory(refresh=True)

                for i, item in enumerate(self.inventory_list):
                    if item["AssetInfo"] is None:
                        continue
                    asset_id = item["SteamAssetId"]
                    template_id = item["TemplateInfo"]["Id"]
                    short_name = item["ShotName"]
                    price = item["TemplateInfo"]["MarkPrice"]
                    if (
                        price < lease_cfg["filter_price"]
                        or item["Tradable"] is False
                        or item["AssetStatus"] != 0
                        or any(s != "" and is_subsequence(s, short_name) for s in lease_cfg["filter_name"])
                    ):
                        continue
                    self.operate_sleep()

                    price_rsp = self.get_lease_price(template_id, min_price=price, max_price=price * 2)
                    if price_rsp["LeaseUnitPrice"] == 0:
                        continue

                    lease_item = models.UUOnLeaseShelfItem(
                        AssetId=asset_id,
                        IsCanLease=True,
                        IsCanSold=False,
                        LeaseMaxDays=lease_cfg["lease_max_days"],
                        LeaseUnitPrice=price_rsp["LeaseUnitPrice"],
                        LongLeaseUnitPrice=price_rsp["LongLeaseUnitPrice"],
                        LeaseDeposit=str(price_rsp["LeaseDeposit"]),
                        CompensationType=self.compensation_type,
                    )
                    if lease_cfg["lease_max_days"] <= 8:
                        lease_item.LongLeaseUnitPrice = None

                    lease_item_list.append(lease_item)

                self.logger.info(f"共 {len(lease_item_list)} 件物品可以出租。")

                self.operate_sleep()
                if len(lease_item_list) > 0:
                    success_count = self.uuyoupin.put_items_on_lease_shelf(lease_item_list)
                    if success_count > 0:
                        self.logger.info(f"成功上架 {success_count} 个物品。")
                    else:
                        self.logger.error("上架失败！请查看日志获得详细信息。")
                    if len(lease_item_list) - success_count > 0:
                        self.logger.error(f"有 {len(lease_item_list) - success_count} 个商品上架失败。")

            except TypeError as e:
                handle_caught_exception(e, "UUAuto")
                self.logger.error("悠悠有品出租出现错误。")
                exit_code.set(1)
                return 1
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("出现未知错误, 稍后再试! ")
                try:
                    self.uuyoupin.get_user_nickname()
                except KeyError as e:
                    handle_caught_exception(e, "UUAuto", known=True)
                    send_notification(self.steam_client, "检测到悠悠有品登录已经失效,请重新登录", title="悠悠有品登录失效")
                    self.logger.error("检测到悠悠有品登录已经失效,请重新登录。")
                    self.logger.error("由于登录失败，插件将自动退出。")
                    exit_code.set(1)
                    return 1

    def lease_change_price(self):
        self.logger.info("悠悠出租自动修改价格已启动")
        self.operate_sleep(15)
        lease_cfg = self.config["uu"]["lease_item"]
        try:
            self.uuyoupin.send_device_info()
            self.logger.info("正在获取悠悠有品出租已上架物品...")
            leased_item_list = self.leased_inventory_list
            for i, item in enumerate(leased_item_list):
                template_id = item.templateid
                short_name = item.short_name
                price = item.price

                if any(s != "" and is_subsequence(s, short_name) for s in lease_cfg["filter_name"]):
                    continue

                price_rsp = self.get_lease_price(template_id, min_price=price, max_price=price * 2)
                if price_rsp["LeaseUnitPrice"] == 0:
                    continue

                item.LeaseUnitPrice = price_rsp["LeaseUnitPrice"]
                item.LongLeaseUnitPrice = price_rsp["LongLeaseUnitPrice"]
                item.LeaseDeposit = price_rsp["LeaseDeposit"]
                item.LeaseMaxDays = lease_cfg["lease_max_days"]
                if lease_cfg["lease_max_days"] <= 8:
                    item.LongLeaseUnitPrice = None

            self.logger.info(f"{len(leased_item_list)} 件物品可以更新出租价格。")
            self.operate_sleep()
            if len(leased_item_list) > 0:
                success_count = self.uuyoupin.change_leased_price(leased_item_list, compensation_type=self.compensation_type)
                self.logger.info(f"成功修改 {success_count} 件物品出租价格。")
                if len(leased_item_list) - success_count > 0:
                    self.logger.error(f"{len(leased_item_list) - success_count} 件物品出租价格修改失败。")
            else:
                self.logger.info(f"没有物品可以修改价格。")

        except TypeError as e:
            handle_caught_exception(e, "UUAuto-AutoChangePrice")
            self.logger.error("悠悠有品出租出现错误")
            exit_code.set(1)
            return 1
        except Exception as e:
            self.logger.error(e, exc_info=True)
            self.logger.info("出现未知错误, 稍后再试! ")
            try:
                self.uuyoupin.get_user_nickname()
            except KeyError as e:
                handle_caught_exception(e, "UUAuto-AutoChangePrice", known=True)
                self.logger.error("检测到悠悠有品登录已经失效,请重新登录")
                self.logger.error("由于登录失败，插件将自动退出")
                exit_code.set(1)
                return 1

    def auto_set_zero_cd(self):
        self.logger.info("悠悠有品出租自动设置0cd已启动")
        self.operate_sleep()
        lease_cfg = self.config["uu"]["lease_item"]
        if self.uuyoupin is not None:
            try:
                zero_cd_valid_list = self.uuyoupin.get_zero_cd_list()
                enable_zero_cd_list = []
                for order in zero_cd_valid_list:
                    name = order["commodityInfo"]["name"]
                    if any(s != "" and is_subsequence(s, name) for s in lease_cfg["filter_name"]):
                        continue
                    enable_zero_cd_list.append(int(order["orderId"]))
                self.logger.info(f"共 {len(enable_zero_cd_list)} 件物品可以设置为0cd。")
                if len(enable_zero_cd_list) > 0:
                    self.uuyoupin.enable_zero_cd(enable_zero_cd_list)
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("出现未知错误, 稍后再试! ")

    def pre_check_price(self):
        self.get_lease_price(44444, 1000)
        self.logger.info("请检查押金获取是否有问题，如有请终止程序，否则开始运行该插件。")
        self.operate_sleep()
