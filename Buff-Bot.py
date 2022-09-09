import os
import sys
import json
import traceback
from steampy.client import SteamClient
from lxml import etree
import requests
import time
import builtins as __builtin__
import FileUtils


def print(*args, **kwargs):
    # __builtin__.print('New print function')
    return __builtin__.print(time.strftime("[%Y-%m-%d %H:%M:%S]", time.localtime()), *args, **kwargs)


if __name__ == '__main__':
    os.system("title Buff-Bot 作者：甲甲")
    print("欢迎使用Buff-Bot 作者：甲甲")
    print("正在初始化...")
    ignoredoffer = list()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27',
    }
    print("正在准备登录至BUFF...")
    try:
        headers['Cookie'] = FileUtils.readfile('cookies.txt')
        print("已检测到cookies，尝试登录")
        response = requests.get('https://buff.163.com/market/sell_order/to_deliver?game=csgo', headers=headers)
        userid = etree.HTML(response.text).xpath("/html//strong[@id='navbar-user-name']/text()")
        print("BUFF用户名", userid[0])
        print("使用cookies登录成功\n")
    except FileNotFoundError:
        print('未检测到cookies.txt，请添加cookies.txt后再进行操作！')
        os.system('pause')
        sys.exit()

    try:
        print("正在登录Steam...")
        acc = json.loads(FileUtils.readfile('steamaccount.txt'))
        client = SteamClient(acc.get('api_key'))
        SteamClient.login(client, acc.get('steam_username'), acc.get('steam_password'), 'steamaccount.txt')
        print("登录完成！\n")
    except FileNotFoundError:
        print('未检测到steamaccount.txt，请添加到steamaccount.txt后再进行操作！')
        os.system('pause')
        sys.exit()

    while True:
        print("正在进行待发货/待收货饰品检查...")
        response = requests.get("https://buff.163.com/api/message/notification", headers=headers)
        to_deliver_count = int(json.loads(response.text).get('data').get('to_deliver_order').get('csgo'))
        if to_deliver_count != 0:
            print("检测到", to_deliver_count, "个待发货请求！")
        response = requests.get("https://buff.163.com/api/market/steam_trade", headers=headers)
        trade = json.loads(response.text).get('data')
        print("查找到", len(trade), "个待处理的交易报价请求！")
        try:
            if len(trade) != 0:
                i = 0
                for go in trade:
                    i += 1
                    offerid = go.get('tradeofferid')
                    print("正在处理第", i, "个交易报价 报价ID", offerid)
                    if offerid not in ignoredoffer:
                        try:
                            print("正在接受报价...")
                            client.accept_trade_offer(offerid)
                            ignoredoffer.append(offerid)
                            print("接受完成！已经将此交易报价加入忽略名单！\n")
                        except Exception as e:
                            print(traceback.print_exc())
                            print("出现错误，稍后再试！")
                    else:
                        print("该报价已经被处理过，跳过.\n")
                print("暂无BUFF报价请求.将在180秒后再次检查BUFF交易信息！\n")
            else:
                print("暂无BUFF报价请求.将在180秒后再次检查BUFF交易信息！\n")
        except Exception:
            print("出现错误，稍后再试！")
        time.sleep(60)
