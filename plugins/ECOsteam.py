import datetime
import os
import time
from threading import Thread

from requests.exceptions import ProxyError

from PyECOsteam import ECOsteamClient
from steampy.exceptions import ConfirmationExpected, InvalidCredentials
from utils.logger import PluginLogger, handle_caught_exception
from utils.static import ECOSTEAM_RSAKEY_FILE
from utils.tools import exit_code, get_encoding


class ECOsteamPlugin:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.logger = PluginLogger("ECOsteam.cn")
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        self.ignored_offer = []

    def init(self):
        if not os.path.exists(ECOSTEAM_RSAKEY_FILE):
            with open(ECOSTEAM_RSAKEY_FILE, "w", encoding="utf-8") as f:
                f.write("")
            return True
        return False

    def exec(self):
        self.logger.info("正在登录ECOsteam...")
        try:
            with open(
                ECOSTEAM_RSAKEY_FILE, "r", encoding=get_encoding(ECOSTEAM_RSAKEY_FILE)
            ) as f:
                rsa_key = f.read()
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
        threads = []
        threads.append(Thread(target=self.auto_accept_offer))
        for thread in threads:
            thread.daemon = True
            thread.start()
        for thread in threads:
            thread.join()

    def auto_accept_offer(self):
        self.logger.info("正在检查待发货列表...")
        today = datetime.datetime.today()
        tomorrow = datetime.datetime.today() + datetime.timedelta(days=1)
        last_month = today - datetime.timedelta(days=30)
        tomorrow = tomorrow.strftime("%Y-%m-%d")
        last_month = last_month.strftime("%Y-%m-%d")
        wait_deliver_orders = self.client.GetSellerOrderList(
            last_month, tomorrow, DetailsState=8
        ).json()["ResultData"]["PageResult"]
        self.logger.info(f"检测到{len(wait_deliver_orders)}个待发货订单！")
        if len(wait_deliver_orders) > 0:
            for order in wait_deliver_orders:
                self.logger.debug(f'正在获取订单号{order["OrderNum"]}的详情！')
                detail = self.client.GetSellerOrderDetail(
                    OrderNum=order["OrderNum"]
                ).json()["ResultData"]
                tradeOfferId = detail["TradeOfferId"]
                goodsName = detail["GoodsName"]
                if tradeOfferId not in self.ignored_offer:
                    self.logger.info(
                        f"正在发货商品{goodsName}，报价号{tradeOfferId}..."
                    )
                    try:
                        with self.steam_client_mutex:
                            self.steam_client.accept_trade_offer(str(tradeOfferId))
                        self.ignored_offer.append(tradeOfferId)
                        self.logger.info(f"已接受报价号{tradeOfferId}！")
                    except ProxyError:
                        self.logger.error("代理异常, 本软件可不需要代理或任何VPN")
                        self.logger.error("可以尝试关闭代理或VPN后重启软件")
                    except (
                        ConnectionError,
                        ConnectionResetError,
                        ConnectionAbortedError,
                        ConnectionRefusedError,
                    ):
                        self.logger.error("网络异常, 请检查网络连接")
                        self.logger.error(
                            "这个错误可能是由于代理或VPN引起的, 本软件可无需代理或任何VPN"
                        )
                        self.logger.error(
                            "如果你正在使用代理或VPN, 请尝试关闭后重启软件"
                        )
                        self.logger.error("如果你没有使用代理或VPN, 请检查网络连接")
                    except InvalidCredentials as e:
                        self.logger.error(
                            "mafile有问题, 请检查mafile是否正确"
                            "(尤其是identity_secret)"
                        )
                        self.logger.error(str(e))
                    except ConfirmationExpected as e:
                        handle_caught_exception(e)
                        self.logger.error(
                            "Steam Session已经过期, 请删除session文件夹并重启Steamauto"
                        )
                    except ValueError as e:
                        self.logger.error("Steam 宵禁限制, 请稍后再试!")
                        handle_caught_exception(e)
                    except Exception as e:
                        handle_caught_exception(e)
                        self.logger.error("Steam异常, 暂时无法接受报价, 请稍后再试! ")
                else:
                    self.logger.info(
                        f"已经自动忽略报价号{tradeOfferId}，商品名{goodsName}，因为它已经被程序处理过！"
                    )
        interval = self.config["ecosteam"]["auto_accept_offer"]["interval"]
        self.logger.info(f"等待{interval}秒后继续检查待发货列表...")
        time.sleep(interval)
