# Buff-Bot  
> 免费、开源的网易Buff及悠悠有品饰品交易平台自动收发货解决方案  
> 杜绝收费、安全稳定

使用Python3和Requests库实现的网易BUFF饰品交易平台、悠悠有品饰品交易平台全自动发货/求购收货  
**使用前请仔细阅读本文档！**  
**欢迎有能力者提交PR来完善本程序。**  
**请勿违反开源协议，包括但不限于倒卖此程序或修改后不进行开源等。**

## 如何使用?
1. 前往 [Github Releases](https://github.com/jiajiaxd/Buff-Bot/releases/latest) 下载适合自己系统的Buff-Bot
2. 将所得文件解压缩
3. 打开 `config` 文件夹
4. 将 `config.example.json` 复制到 `config.json` 并修改配置(相关教程见FAQ)
5. 打开`steam_account_info.json`, 修改所有参数(相关教程见FAQ)
6. 打开`buff_cookies.txt`, 填入[网易BUFF](https://buff.163.com)的cookie(包含session即可).如果要使用悠悠有品发货功能，你还需要编辑uu_token.txt并填入悠悠有品的token(相关配置项见FAQ) 
7. ~~给予本仓库一个star(手动狗头)~~
## FAQ

**1. `config.json` 说明**  
| 配置项 | 描述 | 
| ------ | ---- |
| development_mode  | 是否开启开发者模式, 非开发者请勿开启, 具体效果请自行查看代码    |
| steam_login_ignore_ssl_error  | 设置为true后，关闭SSL验证(请自行确保网络环境安全)   |
| buff_auto_accept_offer.enable  | 设置为true后，启用BUFF自动接收报价功能 |
| buff_auto_accept_offer.interval   | 每次检查是否有新报价间隔(轮询间隔)，单位为秒   |
| buff_auto_accept_offer.sell_protection  | 是否开启出售保护, 开启后将不会自动接收低于价格过低的出售请求      |
| buff_auto_accept_offer.protection_price        | 出售保护价格, 若其他卖家最低价低于此价格, 则不会进行出售保护   |
| buff_auto_accept_offer.protection_price_percentage    | 出售价格保护比例, 若出售价格低于此比例*其他卖家最低价格，则不会自动接收报价         |
| buff_auto_accept_offer.sell_notification.title       | 出售通知标题(如不需要可直接删除)           |
| buff_accept_offer.sell_notification.body          | 出售通知内容(如不需要可直接删除)       |
| buff_auto_accept_offer.protection_notification.title         | 出售保护通知标题(如不需要可直接删除)，详见FAQ和[Apprise](https://github.com/caronc/apprise)|
| buff_auto_accept_offer.protection_notification.body         | 出售保护通知内容(如不需要可直接删除)，详见FAQ和[Apprise](https://github.com/caronc/apprise)|
| buff_auto_accept_offer.servers         | 通知服务器，详见[Apprise](https://github.com/caronc/apprise)|
| uu_auto_accept_offer.enable  | 默认为disabled，填入悠悠有品token后可启用悠悠有品自动发货功能,token获取教程见FAQ    |
| uu_auto_accept_offer.interval   | 每次检查是否有新报价间隔(轮询间隔)，单位为秒   |

**2.`steam_account_info.json`说明**  
| 配置项              | 描述                                                         |
|------------------|--------------------------------------------------------------|
| steamid          | Steam 的数字 ID                                             |
| shared_secret    | Steam 令牌参数                                               |
| identity_secret  | Steam 令牌参数                                               |
| api_key          | Steam 网页 API 密钥                                          |
| steam_username   | Steam 登录时填写的用户名                                     |
| steam_password   | Steam 登录时填写的密码                                       |
**部分参数获取教程请查看附录**

**3.账号安全问题?**  
Buff-Bot所有源代码均开放在GitHub，可供所有人自行查看代码安全性  
在用户的电脑不被恶意软件入侵的情况下，账号不可能泄露  

**4.notification配置项说明**
| 配置项 | 描述 |
| --- | --- |
| sell_notification | 出售通知(如不需要可直接删除) |
| title | 通知标题 |
| body | 通知内容 |
| protection_notification | 出售保护通知(如不需要可直接删除) |
| title | 通知标题 |
| body | 通知内容 |
| servers   | Apprise格式服务器列表 - 详见[Apprise](https://github.com/caronc/apprise)<br>- 额外支持 [Server酱](https://sct.ftqq.com/) 格式为`ftqq://<SENDKEY>`    

**5.悠悠有品token获取教程**  
在安装好所有依赖后，直接运行`python get_uu_token.py`并按照提示操作即可

**6.可否关闭Buff自动发货，只是有悠悠有品自动发货？**  
由于本程序第一次编写时未考虑到支持其它平台，暂时无法关闭.后期会推出重写版本.
## 附录
关于`steam_account_info.json`相关参数的获取教程都在下面, 请自行参阅  
个人推荐使用[ Watt Toolkit ](https://github.com/BeyondDimension/SteamTools)获取Steam令牌参数 操作非常简便

[获取Steam网页API KEY](http://steamcommunity.com/dev/apikey)  
[buffhelp 网易buff自动发货-哔哩哔哩(请查看P2-P7)](https://www.bilibili.com/video/BV1DT4y1P7Dx)  
[Obtaining SteamGuard from mobile device]( https://github.com/SteamTimeIdler/stidler/wiki/Getting-your-%27shared_secret%27-code-for-use-with-Auto-Restarter-on-Mobile-Authentication )  
[Obtaining SteamGuard using Android emulation]( https://github.com/codepath/android_guides/wiki/Genymotion-2.0-Emulators-with-Google-Play-support)

## 鸣谢
感谢 [**@lupohan44**](https://github.com/lupohan44) 为本项目提交的大量代码！  
特别感谢 [JetBrains](https://www.jetbrains.com/) 为开源项目提供免费的 [PyCharm](https://www.jetbrains.com/pycharm/) 等 IDE 的授权  
[<img src="https://resources.jetbrains.com/storage/products/company/brand/logos/jb_beam.svg" width="200"/>](https://jb.gg/OpenSourceSupport)
[<img src="https://resources.jetbrains.com/storage/products/company/brand/logos/PyCharm_icon.svg" width="200"/>](https://jb.gg/OpenSourceSupport)
