

# Steamauto  
![Steamauto](https://socialify.git.ci/jiajiaxd/Steamauto/image?description=1&forks=1&language=1&logo=https%3A%2F%2Ficons.bootcss.com%2Fassets%2Ficons%2Fsteam.svg&name=1&owner=1&stargazers=1&theme=Light)
> 开源的 Steam 自动收发货解决方案  
> 杜绝收费、安全稳定

**使用前请仔细阅读本文档！**  
**欢迎有能力者提交PR来完善本程序。**  
**请勿违反开源协议，包括但不限于闭源倒卖此程序或修改后不进行开源等。**

## 它能做什么？  

#### 在 [Buff饰品交易平台](https://buff.163.com) 上:
- 自动发货
- 自动求购收货(需要开启 自动接受礼物报价 功能)
- 供应求购确认报价
- 以最低价上架全部库存

#### 在 [悠悠有品饰品交易平台](https://www.youpin898.com/) 上:
- 自动发货出售商品

#### 在 Steam 上:
- 自动接受礼物报价(无需支出任何Steam库存中的物品的报价)

## 如何使用?
1. 前往 [Github Releases](https://github.com/jiajiaxd/Steamauto/releases/latest) 下载适合自己系统的Steamauto
2. 将所得文件解压缩
3. 打开 `config` 文件夹
4. 将 `config.example.json` 复制到 `config.json` 并修改配置(相关教程见FAQ)
5. 打开`steam_account_info.json`, 修改所有参数(相关教程见配置说明)
6. **(若有需求Buff相关功能)** 打开`buff_cookies.txt`, 填入[网易BUFF](https://buff.163.com)的cookie(包含session即可)
7. **(若有需求悠悠有品相关功能)** 打开`uu_token.txt`,填入[悠悠有品](https://www.youpin898.com/)的token(如何获取token,见FAQ) 
8. ~~给予本仓库一个star(手动狗头)~~

## 配置说明
**部分配置项数据(如获取Steam账户信息、Buff的cookie等)在附录中，请自行查阅！**
##### 在正确运行本程序后，config文件夹应包含以下文件
| 文件名                       | 描述                                        | 
|---------------------------|-------------------------------------------|
| `config.json`             | 主配置文件，可以修改程序的大多数设置                        |
| `steam_account_info.json` | 用于填入Steam账户相关信息                           |
| `buff_cookies.txt`        | **启用网易Buff相关功能后才会创建** 用于填入网易BUFF的Cookie信息 |
| `uu_token.txt`            | **启用悠悠有品相关功能后才会创建** 用于填入网易BUFF的Cookie信息   |
##### `config.json` 
| 配置项                                                           | 描述                                                                                             | 
|---------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| development_mode                                              | 是否开启开发者模式, 非开发者请勿开启, 具体效果请自行查看代码                                                               |
| steam_login_ignore_ssl_error                                  | 设置为true后，关闭SSL验证(请自行确保网络环境安全)                                                                  |
| buff_auto_accept_offer.enable                                 | 设置为true后，启用BUFF自动接收报价功能                                                                        |
| buff_auto_accept_offer.interval                               | 每次检查是否有新报价间隔(轮询间隔)，单位为秒                                                                        |
| buff_auto_accept_offer.sell_protection                        | 是否开启出售保护, 开启后将不会自动接收低于价格过低的出售请求                                                                |
| buff_auto_accept_offer.protection_price                       | 出售保护价格, 若其他卖家最低价低于此价格, 则不会进行出售保护                                                               |
| buff_auto_accept_offer.protection_price_percentage            | 出售价格保护比例, 若出售价格低于此比例*其他卖家最低价格，则不会自动接收报价                                                        |
| buff_auto_accept_offer.sell_notification.title                | 出售通知标题(如不需要可直接删除)，详见FAQ和[Apprise](https://github.com/caronc/apprise)                           |
| buff_accept_offer.sell_notification.body                      | 出售通知内容(如不需要可直接删除)，详见FAQ和[Apprise](https://github.com/caronc/apprise)                           |
| buff_auto_accept_offer.protection_notification.title          | 出售保护通知标题(如不需要可直接删除)，详见notification配置项说明和[Apprise](https://github.com/caronc/apprise)           |
| buff_auto_accept_offer.protection_notification.body           | 出售保护通知内容(如不需要可直接删除)，详见notification配置项说明和[Apprise](https://github.com/caronc/apprise)           |
| buff_auto_accept_offer.buff_cookie_expired_notification.title | BUFF Cookies失效通知标题(如不需要可直接删除)，详见notification配置项说明和[Apprise](https://github.com/caronc/apprise) |
| buff_auto_accept_offer.buff_cookie_expired_notification.body  | BUFF Cookies失效通知内容(如不需要可直接删除)，详见notification配置项说明和[Apprise](https://github.com/caronc/apprise) |
| buff_auto_accept_offer.servers                                | 通知服务器，详见[Apprise](https://github.com/caronc/apprise)                                           |
| buff_auto_on_sale.enable                                      | 设置为true后，启用BUFF自动以最低价上架所有库存                                                                    |
| buff_auto_on_sale.interval                                    | 检查库存间隔时间                                                                                       |
| uu_auto_accept_offer.enable                                   | 默认为disabled，填入悠悠有品token后可启用悠悠有品自动发货功能,token获取教程见FAQ                                            |
| uu_auto_accept_offer.interval                                 | 每次检查是否有新报价间隔(轮询间隔)，单位为秒                                                                        |
| steam_auto_accept_offer.enable                                | 是否开启自动接受Steam礼物报价(无需支出任何Steam库存中的物品的报价)                                                        |
| steam_auto_accept_offer.interval                              | 每次检查报价列表间隔(轮询间隔)，单位为秒                                                                          |

##### `steam_account_info.json`
| 配置项             | 描述                   |
|-----------------|----------------------|
| steamid         | Steam 的数字 ID (字符串格式) |
| shared_secret   | Steam 令牌参数           |
| identity_secret | Steam 令牌参数           |
| api_key         | Steam 网页 API 密钥      |
| steam_username  | Steam 登录时填写的用户名      |
| steam_password  | Steam 登录时填写的密码       |

##### `notification`相关配置项说明
| 配置项                              | 描述                                                                                                                               |
|----------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| sell_notification                | 出售通知(如不需要可直接删除)                                                                                                                  |
| protection_notification          | 出售保护通知(如不需要可直接删除)                                                                                                                |
| buff_cookie_expired_notification | BUFF Cookies失效通知(如不需要可直接删除)                                                                                                      |
| ---                              | ---                                                                                                                              |
| title                            | 通知标题                                                                                                                             |
| body                             | 通知内容                                                                                                                             |
| servers                          | Apprise格式服务器列表 - 详见[Apprise](https://github.com/caronc/apprise)<br>- 额外支持 [Server酱](https://sct.ftqq.com/) 格式为`ftqq://<SENDKEY>` |

## FAQ
##### 账号安全问题?
Steamauto的所有源代码均开放在GitHub，可供所有人自行查看代码安全性  
在用户的电脑不被恶意软件入侵的情况下，账号不可能泄露  

##### 如何获取悠悠有品token?
在安装好所有依赖后，直接运行`python get_uu_token.py`并按照提示操作即可

##### 可否关闭Buff自动发货，只是有悠悠有品自动发货？
将`config.json`中`buff_auto_accept_offer.enable`设置为false即可
## 附录
关于`steam_account_info.json`相关参数的获取教程都在下面, 请自行参阅  
个人推荐使用[ Watt Toolkit ](https://github.com/BeyondDimension/SteamTools)获取Steam令牌参数 操作非常简便

[获取Steam网页API KEY](http://steamcommunity.com/dev/apikey)  
[buffhelp 网易buff自动发货-哔哩哔哩(请查看P2-P7)](https://www.bilibili.com/video/BV1DT4y1P7Dx)  
[Obtaining SteamGuard from mobile device]( https://github.com/SteamTimeIdler/stidler/wiki/Getting-your-%27shared_secret%27-code-for-use-with-Auto-Restarter-on-Mobile-Authentication )  
[Obtaining SteamGuard using Android emulation]( https://github.com/codepath/android_guides/wiki/Genymotion-2.0-Emulators-with-Google-Play-support)

## 鸣谢
感谢 [**@lupohan44**](https://github.com/lupohan44) 为本项目提交的大量代码！  

## JetBrains
感谢 [JetBrains](https://www.jetbrains.com/) 为开源项目提供免费的 [PyCharm](https://www.jetbrains.com/pycharm/) 等 IDE 的授权  
[<img src="https://resources.jetbrains.com/storage/products/company/brand/logos/jb_beam.svg" width="200"/>](https://jb.gg/OpenSourceSupport)
