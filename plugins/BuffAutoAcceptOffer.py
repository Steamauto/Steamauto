import time

from BuffApi import BuffAccount
from utils.buff_helper import get_valid_session_for_buff
from utils.logger import PluginLogger, handle_caught_exception
from utils.steam_client import accept_trade_offer
from utils.tools import exit_code

logger = PluginLogger("BuffAutoAcceptOffer")

class BuffAutoAcceptOffer:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.SUPPORT_GAME_TYPES = [{"game": "csgo", "app_id": 730}]
        self.config = config
        self.order_info = {}

    def init(self) -> bool:
        return False

    def require_buyer_send_offer(self):
        try:
            logger.info('正在开启只允许买家发起报价功能...')
            result = self.buff_account.set_force_buyer_send_offer()
            if result:
                logger.info("已开启买家发起交易报价功能")
            else:
                logger.error("开启买家发起交易报价功能失败")
        except Exception as e:
            logger.error(f"开启买家发起交易报价功能失败: {str(e)}")

    def check_buff_account_state(self):
        try:
            username = self.buff_account.get_user_nickname()
            if username:
                # 检查是否能正常访问steam_trade接口
                trades = self.buff_account.get_steam_trade()
                if trades is None:
                    logger.error("BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试!")
                    return ""
                return username
        except Exception as e:
            logger.error(f"检查BUFF账户状态失败: {str(e)}")

        logger.error("BUFF账户登录状态失效, 请检查buff_cookies.txt或稍后再试!")
        return ""

    def format_item_info(self, trade):
        """格式化物品信息用于显示在交易接受描述中"""
        result = "发货平台：网易BUFF\n"

        for good_id, good_item in trade["goods_infos"].items():
            result += f"发货商品：{good_item['name']}"
            if trade.get('items_to_trade'):
                if len(trade["items_to_trade"]) > 1:
                    result += f" 等{len(trade['items_to_trade'])}个物品"
            else:
                result += " (求购报价，数量未知)"

            if trade["tradeofferid"] in self.order_info:
                price = float(self.order_info[trade["tradeofferid"]]["price"])
                result += f"\n售价：{price} 元"

            break  # 只处理第一个物品，因为批量购买的物品通常是相同的

        return result

    def exec(self):
        logger.info("BUFF自动接受报价插件已启动.请稍候...")

        session = get_valid_session_for_buff(self.steam_client, logger)
        self.buff_account = BuffAccount(session)

        try:
            user_info = self.buff_account.get_user_info()
            if not user_info or "steamid" not in user_info:
                logger.error("获取BUFF绑定的SteamID失败")
                exit_code.set(1)
                return 1

            steamid_buff = user_info["steamid"]
        except Exception as e:
            logger.error(f"获取BUFF绑定的SteamID失败: {str(e)}")
            exit_code.set(1)
            return 1

        if self.steam_client.get_steam64id_from_cookies() != steamid_buff:
            logger.error("当前登录账号与BUFF绑定的Steam账号不一致!")
            exit_code.set(1)
            return 1

        logger.info(f"已经登录至BUFF 用户名: {user_info["nickname"]}")
        if not user_info['force_buyer_send_offer']:
            logger.warning("当前账号未开启只允许买家发起报价功能，正在自动开启...")
            self.require_buyer_send_offer()
        else:
            logger.info("当前账号已开启只允许买家发起报价功能")

        ignored_offer = []
        interval = self.config["buff_auto_accept_offer"]["interval"]
        dota2_support = self.config["buff_auto_accept_offer"].get("dota2_support", False)

        if 'sell_protection' in self.config['buff_auto_accept_offer']:
            logger.warning('你正在使用旧版本配置文件，BUFF自动发货插件已经重写并精简功能，建议删除配置文件重新生成！')

        if dota2_support:
            self.SUPPORT_GAME_TYPES.append({"game": "dota2", "app_id": 570})

        while True:
            try:
                logger.info("正在检查BUFF账户登录状态...")
                username = self.check_buff_account_state()
                if username == "":
                    logger.info("BUFF账户登录状态失效, 尝试重新登录...")
                    session = get_valid_session_for_buff(self.steam_client, logger)
                    if session == "":
                        logger.error("BUFF账户登录状态失效, 无法自动重新登录!")
                        return
                    self.buff_account = BuffAccount(session)
                logger.info("为了避免访问接口过于频繁，休眠5秒...")
                time.sleep(5)
                
                logger.info("正在进行BUFF待发货/待收货饰品检查...")
                notification = self.buff_account.get_notification()

                # 处理响应检查是否有错误
                if isinstance(notification, dict) and "to_deliver_order" in notification:
                    to_deliver_order = notification["to_deliver_order"]
                    try:
                        csgo_count = 0 if "csgo" not in to_deliver_order else int(to_deliver_order["csgo"])
                        dota2_count = 0 if (dota2_support or ("dota2" not in to_deliver_order)) else int(to_deliver_order["dota2"])
                        total_count = csgo_count + dota2_count

                        if csgo_count != 0 or dota2_count != 0:
                            logger.info(f"检测到{total_count}个待发货请求!")
                            logger.info(f"CSGO待发货: {csgo_count}个")
                            if dota2_support:
                                logger.info(f"DOTA2待发货: {dota2_count}个")
                    except TypeError as e:
                        handle_caught_exception(e, "BuffAutoAcceptOffer", known=True)
                        logger.error("Buff接口返回数据异常! 请检查网络连接或稍后再试!")

                if any(list(notification["to_deliver_order"].values()) + list(notification["to_confirm_sell"].values())):
                    # 获取待处理交易
                    trades = self.buff_account.get_steam_trade()
                    logger.info("为了避免访问接口过于频繁，休眠5秒...")
                    time.sleep(5)

                    # 处理响应检查是否有错误
                    if trades is None:
                        logger.error("获取Steam交易失败，稍后重试")
                        time.sleep(5)
                        continue

                    for index, game in enumerate(self.SUPPORT_GAME_TYPES):
                        response_data = self.buff_account.get_sell_order_to_deliver(game["game"], game["app_id"])
                        if response_data and "items" in response_data:
                            trade_supply = response_data["items"]
                            for trade_offer in trade_supply:
                                if trade_offer["tradeofferid"] is not None and trade_offer["tradeofferid"] != "":
                                    self.order_info[trade_offer["tradeofferid"]] = trade_offer
                                    if not any(trade_offer["tradeofferid"] == trade["tradeofferid"] for trade in trades):
                                        
                                        for goods_id,goods_info in response_data["goods_infos"].items():
                                            goods_id = str(goods_id)
                                            trade_offer["goods_id"] = str(trade_offer["goods_id"])
                                            if goods_id == trade_offer["goods_id"]:
                                                trade_offer["goods_infos"] = {}
                                                trade_offer["goods_infos"][goods_id] = goods_info
                                                break
                                        trades.append(trade_offer)

                        if index != len(self.SUPPORT_GAME_TYPES) - 1:
                            logger.info("为了避免访问接口过于频繁，休眠5秒...")
                            time.sleep(5)

                    unprocessed_count = len(trades)

                    logger.info(f"查找到 {unprocessed_count} 个待处理的BUFF报价")

                    try:
                        if len(trades) != 0:
                            for i, trade in enumerate(trades):
                                offer_id = trade["tradeofferid"]

                                logger.info(f"正在处理第 {i+1} 个交易报价 报价ID：{offer_id}")
                                if offer_id not in ignored_offer:
                                    try:
                                        logger.info("正在接受报价...")
                                        desc = self.format_item_info(trade)
                                        if accept_trade_offer(self.steam_client, self.steam_client_mutex, offer_id, desc=desc):
                                            ignored_offer.append(offer_id)
                                            logger.info("接受完成! 已经将此交易报价加入忽略名单!")

                                        if trades.index(trade) != len(trades) - 1:
                                            logger.info("为了避免频繁访问Steam接口, 等待5秒后继续...")
                                            time.sleep(5)
                                    except Exception as e:
                                        logger.error(f"处理交易报价时出错: {str(e)}", exc_info=True)
                                        logger.info("出现错误, 稍后再试!")
                                else:
                                    logger.info("该报价已经被处理过, 跳过.")

                    except Exception as e:
                        handle_caught_exception(e, "BuffAutoAcceptOffer")
                        logger.info("出现错误, 稍后再试!")
                else:
                    logger.info("没有待处理的交易报价")
            except Exception as e:
                handle_caught_exception(e, "BuffAutoAcceptOffer")
                logger.info("出现未知错误, 稍后再试!")

            logger.info(f"将在{interval}秒后再次检查待发货订单信息!")
            time.sleep(interval)
