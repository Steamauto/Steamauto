import json
import requests

from utils.logger import PluginLogger


class C5Account:
    BASE_URL = "https://openapi.c5game.com"
    REQUEST_TIMEOUT = (10, 30)

    # https://apifox.com/apidoc/shared-bcbf0c5d-caf4-4ea6-b2c1-0bc292a2e6b2/doc-3014376 查看API文档
    def __init__(self, app_key):
        self.app_key = app_key
        self.client = requests.Session()
        self.client.headers.update(
            {
                # C5 自 6 月 17 日起要求所有请求显式携带完整的压缩格式列表。
                "Accept-Encoding": "gzip, br, zstd, deflate",
            }
        )
        self.logger = PluginLogger("C5Game API")

    def post(self, path, data):
        url = f"{self.BASE_URL}/{path.lstrip('/')}"
        resp = self.client.post(
            url,
            params={"app-key": self.app_key},
            json=data,
            timeout=self.REQUEST_TIMEOUT,
        )
        self.logger.debug(f"POST {path} {json.dumps(data, ensure_ascii=False)} {resp.text}")
        resp.raise_for_status()
        return resp.json()

    def get(self, path, params):
        url = f"{self.BASE_URL}/{path.lstrip('/')}"
        request_params = dict(params)
        # C5 当前规范要求 app-key 作为 query parameter，而不是普通 header。
        request_params["app-key"] = self.app_key
        resp = self.client.get(
            url,
            params=request_params,
            timeout=self.REQUEST_TIMEOUT,
        )
        self.logger.debug(f"GET {path} {params} {resp.text}")
        resp.raise_for_status()
        return resp.json()

    def balance(self):
        return self.get("/merchant/account/v1/balance", {})

    def checkAppKey(self):
        resp = self.balance()
        if resp.get("success", False):
            return True
        else:
            return False

    def orderList(self, status=0, page=1, steamId=None):
        # 订单状态,不传:全部订单,0,待付款 1 是待发货 2 发货中 3 是待收货 10 已完成 11 已取消
        if steamId:
            data = {"status": status, "page": page, "steamId": steamId}
        else:
            data = {"status": status, "page": page}
        return self.get("/merchant/order/v1/list", data)

    def deliver(self, order_list: list):
        return self.post("/merchant/order/v1/deliver", order_list)
