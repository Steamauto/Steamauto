import os
import sys
from steampy.client import SteamClient
import undetected_chromedriver as webdriver
from selenium.webdriver.common.by import By as by
import requests
import pickle
import time
import builtins as __builtin__

clientid = ""
steamid = ""
steampassword = ""


def print(*args, **kwargs):
    # __builtin__.print('New print function')
    return __builtin__.print(time.strftime("[%Y-%m-%d %H:%M:%S]", time.localtime()), *args, **kwargs)


if __name__ == '__main__':
    os.system("title Buff-Bot 作者：甲甲")
    print("欢迎使用Buff-Bot 作者：甲甲")
    print("正在初始化...")
    driver = webdriver.Chrome()
    ignoredoffer = list()
    print("正在准备登录至BUFF...")
    driver.get("https://buff.163.com/market/sell_order/to_deliver?game=csgo")
    try:
        cookiefile = open('cookies.pkl', 'rb')
        cookies = pickle.load(cookiefile)
        print("已检测到cookies，尝试登录")
        for cookie in cookies:
            driver.add_cookie(cookie)
        driver.refresh()
        userid = driver.find_element(
            by.XPATH, "/html//strong[@id='navbar-user-name']").text
        print("BUFF用户名", userid)
        print("使用cookies登录成功\n")
    except Exception:
        print("请在当前界面登录BUFF！")
        current = driver.current_url
        while True:
            if driver.current_url != current:
                break
            time.sleep(1)
        cookiefile = open('cookies.pkl', 'wb')
        pickle.dump(driver.get_cookies(), cookiefile)
        cookiefile.close()
        print("Cookies已经保存，请重启Buff-Bot")
        os.system("pause")
        sys.exit()

    print("正在登录Steam...")
    client = SteamClient(clientid)
    SteamClient.login(client, steamid, steampassword, 'steamguard.txt')
    print("登录完成！\n")

    while True:
        print("正在进行例行待发货物品检查...")
        driver.get("https://buff.163.com/market/sell_order/to_deliver?game=csgo")
        try:
            requestedOffers = driver.find_elements(
                by.CSS_SELECTOR, ".to_steam_processing")
            requestedOffersHref = list()
            for p in requestedOffers:
                requestedOffersHref.append(p.get_attribute("href"))
            print("查找到", len(requestedOffers), "个待处理的交易报价请求！")
            if len(requestedOffers) != 0:
                i = 0
                for go in requestedOffersHref:
                    i += 1
                    print("正在处理第", i, "个交易报价！")
                    try:
                        driver.get(go)
                        time.sleep(3)
                        url = driver.current_url
                        offerid = url[url.find(
                            "%2Ftradeoffer%2F") + len("%2Ftradeoffer%2F"):len(url)]
                        print("获取到交易报价ID", offerid)
                        if offerid in ignoredoffer:
                            print("该报价已经处理。忽略！")
                        else:
                            print("正在接受报价...")
                            client.accept_trade_offer(offerid)
                            ignoredoffer.append(offerid)
                            print("接受完成！已经将此交易报价加入忽略名单！\n")
                    except Exception as e:
                        print("出现错误，稍后再试！")
                print("暂无待发货请求.60秒后再次获取BUFF交易信息！\n")
            else:
                print("暂无待发货请求.60秒后再次获取BUFF交易信息！\n")
        except Exception:
            print("出现错误，稍后再试！")
        time.sleep(60)
