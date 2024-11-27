import json
import requests

from utils.logger import PluginLogger

class C5Account:
    # https://apifox.com/apidoc/shared-bcbf0c5d-caf4-4ea6-b2c1-0bc292a2e6b2/doc-3014376 查看API文档
    def __init__(self, app_key):
        self.app_key = app_key
        self.client = requests.Session()
        self.client.headers.update({
            "app-key": self.app_key,
        })
        self.logger = PluginLogger("C5Game API")
    
    def post(self, path, data):
        url = 'http://openapi.c5game.com/'+path
        resp = self.client.post(url, json=data)
        self.logger.debug(f"POST {path} {json.dumps(data,ensure_ascii=False)} {resp.text}")
    
    def get(self, path, params):
        url = 'http://openapi.c5game.com/'+path
        resp = self.client.get(url, params=params)
        self.logger.debug(f"GET {path} {params} {resp.text}")
        return resp.json()
    
    def balance(self):
        return self.get('/merchant/account/v1/balance', {})
    
    def checkAppKey(self):
        resp = self.balance()
        if resp.get('success', False):
            return True
        else:
            return False
    
    def orderList(self,status=0,page=1,steamId=None):
        # 订单状态,不传:全部订单,0,待付款 1 是待发货 2 发货中 3 是待收货 10 已完成 11 已取消
        if steamId:
            data = {'status':status,'page':page,'steamId':steamId}
        else:
            data = {'status':status,'page':page}
        return self.get('/merchant/order/v1/list', data)
    
    def deliver(self,order_list: list):
        return self.post('/merchant/order/v1/deliver', order_list)