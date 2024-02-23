# Steamauto  
![Steamauto](https://socialify.git.ci/jiajiaxd/Steamauto/image?description=1&language=1&name=1&owner=1&theme=Light)
<div align="center">
      <a href="http://qm.qq.com/cgi-bin/qm/qr?_wv=1027&k=TMyvQMePF7GeJxz27fLzKHuhC2iAN6Bj&authKey=VAgXngXUeaHBfGwY2uNzE%2F8C7S5FN6HsRJDm8LREGeLObTRLSHoYsWxLHPcI9Llz&noverify=0&group_code=425721057" alt="QQ Group">
        <img src="https://img.shields.io/badge/%E5%AE%98%E6%96%B9QQ%E7%BE%A4-425721057-brightgreen?logo=TencentQQ&link=http%3A%2F%2Fqm.qq.com%2Fcgi-bin%2Fqm%2Fqr%3F_wv%3D1027%26k%3DTMyvQMePF7GeJxz27fLzKHuhC2iAN6Bj%26authKey%3DVAgXngXUeaHBfGwY2uNzE%252F8C7S5FN6HsRJDm8LREGeLObTRLSHoYsWxLHPcI9Llz%26noverify%3D0%26group_code%3D425721057" /></a>
      <a href="https://www.bilibili.com/video/BV1ph4y1y7mz/" alt="Video tutorial">
        <img src="https://img.shields.io/badge/%E8%A7%86%E9%A2%91%E6%95%99%E7%A8%8B-%E7%82%B9%E5%87%BB%E8%A7%82%E7%9C%8B-brightgreen?logo=bilibili" /></a>
      <a href="https://github.com/jiajiaxd/Steamauto/stargazers" alt="GitHub Repo stars">
        <img src="https://img.shields.io/github/stars/jiajiaxd/Steamauto?logo=github" /></a>
      <a href="https://github.com/jiajiaxd/Steamauto/forks" alt="GitHub forks">
        <img src="https://img.shields.io/github/forks/jiajiaxd/Steamauto?logo=github" /></a>
</div>

> 开源的 Steam 自动收发货解决方案  
> 杜绝收费、安全稳定

**使用前请仔细阅读本文档！**  
**欢迎有能力者提交PR来完善本程序。**  
**请勿违反开源协议，包括但不限于闭源倒卖此程序或修改后不进行开源等。**  
**[欢迎加入Steamauto 官方QQ群:425721057](http://qm.qq.com/cgi-bin/qm/qr?_wv=1027&k=TMyvQMePF7GeJxz27fLzKHuhC2iAN6Bj&authKey=VAgXngXUeaHBfGwY2uNzE%2F8C7S5FN6HsRJDm8LREGeLObTRLSHoYsWxLHPcI9Llz&noverify=0&group_code=425721057)**
**网络不好的可以加QQ群在群文件内下载最新构建**

**强烈谴责平头哥CSGO违反开源协议闭源修改本软件并收费出售, 我们将对其采取行动**
**[快照证据](https://web.archive.org/web/20240202005724/https://www.ptgcsgo.com/products/872)**

## 它能做什么？  

#### 在 [Buff饰品交易平台](https://buff.163.com) 上:
- 自动发货
- 自动求购收货(需要开启 自动接受礼物报价 功能)
- 供应求购确认报价
- 以最低价上架全部库存
  - 支持自动上架描述
  - 支持自动上架时间段黑白名单
  - **支持选择塞给求购订单, 利益最大化**

#### 在 [悠悠有品饰品交易平台](https://www.youpin898.com/) 上:
- 自动发货出售商品

#### 在 Steam 上:
- 内置Steam加速器
- 自动接受礼物报价(无需支出任何Steam库存中的物品的报价)

## 如何使用?
[推荐观看视频教程](https://www.bilibili.com/video/BV1ph4y1y7mz)
0. ~~给予本仓库一个star(手动狗头)~~
1. 前往 [Github Releases](https://github.com/jiajiaxd/Steamauto/releases/latest) 下载适合自己系统的Steamauto
2. 运行一次程序，程序会释放配置文件
3. 编辑`config`文件夹下的`config.json5`(相关教程见FAQ)  
4. 修改`config`文件夹下的`steam_account_info.json5`中所有的参数(相关教程见配置说明)  
5. **(若有需求Buff相关功能)** 在`config.json5`中启用BUFF相关功能并直接运行程序(程序会自动填写buff_cookies.txt)  
6. **(若有需求悠悠有品相关功能)** 打开`uu_token.txt`,填入[悠悠有品](https://www.youpin898.com/)的token(如何获取token,见FAQ) 

## 配置说明
**部分配置项数据(如获取Steam账户信息、Buff的cookie等)在附录中，请自行查阅！**
##### 在正确运行本程序后，config文件夹应包含以下文件
| 文件名                       | 描述                                        | 
|---------------------------|-------------------------------------------|
| `config.json5`             | 主配置文件，可以修改程序的大多数设置                        |
| `steam_account_info.json5` | 用于填入Steam账户相关信息                           |
| `buff_cookies.txt`        | **启用网易Buff相关插件后才会创建** 用于存取网易BUFF的Cookie信息 |
| `uu_token.txt`            | **启用悠悠有品相关插件后才会创建** 用于存取悠悠有品的Cookie信息(悠悠有品token获取方法见FAQ)   |
##### [config.json5](utils/static.py) (仅供参考 以实际文件为主)
```json5
{
  // 登录Steam时是否开启SSL验证，正常情况下不建议关闭SSL验证
  "steam_login_ignore_ssl_error": false,

  // 是否开启本地加速功能
  // 本地加速功能并非100%可用, 若开启后仍然无法正常连接Steam属于正常情况, 最优解决方案是使用海外服务器
  // 请注意：开启此功能必须关闭Steam登录SSL验证，即steam_login_ignore_ssl_error必须设置为true
  "steam_local_accelerate": false,

  // 是否使用Steam代理功能(该功能只会代理Steam)
  "use_proxies": false,

  // 本地代理地址, 使用前需要确保use_proxies已经设置为true
  // 这里以clash为例，clash默认监听7890端口，如果你使用的是其他代理软件，请自行修改端口
  "proxies": {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890"
  },

  // 填写为true后，程序在出现错误后就会直接停止运行。如果你不知道你在做什么，请不要将它设置为true
  "no_pause": false,

  // BUFF 自动发货插件配置
  "buff_auto_accept_offer": {
    // 是否启用BUFF自动发货报价功能
    "enable": true,
    // 每次检查是否有新报价的间隔（轮询间隔），单位为秒
    "interval": 300,
    // 是否开启出售保护(自动发货前检查其他卖家最低价，若低于保护价格则不会自动接受报价s)
    "sell_protection": false,
    // 出售保护价格，若其他卖家最低价低于此价格，则不会进行出售保护
    "protection_price": 30,
    // 出售价格保护比例，若出售价格低于此比例乘以其他卖家最低价格，则不会自动接受报价
    "protection_price_percentage": 0.9,
    // 出售通知配置(如不需要可直接删除)
    "sell_notification": {
      // 出售通知标题
      "title": "成功出售{game}饰品: {item_name} * {sold_count}",
      // 出售通知内容
      "body": "![good_icon]({good_icon})\n游戏: {game}\n饰品: {item_name}\n出售单价: {buff_price} RMB\nSteam单价(参考): {steam_price} USD\nSteam单价(参考): {steam_price_cny} RMB\n![buyer_avatar]({buyer_avatar})\n买家: {buyer_name}\n订单时间: {order_time}"
    },
    // 出售保护通知配置(如不需要可直接删除)
    "protection_notification": {
      // 出售保护通知标题（如不需要可直接删除）
      "title": "{game}饰品: {item_name} 未自动接受报价, 价格与市场最低价相差过大",
      // 出售保护通知内容（如不需要可直接删除）
      "body": "请自行至BUFF确认报价!"
    },
    // 报价与BUFF出售商品不匹配通知配置(如不需要可直接删除)
    "item_mismatch_notification": {
      // 报价与BUFF出售商品不匹配通知标题
      "title": "BUFF出售饰品与Steam报价饰品不匹配",
      // 报价与BUFF出售商品不匹配通知内容
      "body": "请自行至BUFF确认报价!(Offer: {offer_id})"
    },
    // BUFF Cookies失效通知配置
    "buff_cookie_expired_notification": {
      // BUFF Cookies失效通知标题（如不需要可直接删除）
      "title": "BUFF Cookie已过期, 请重新登录",
      // BUFF Cookies失效通知内容（如不需要可直接删除）
      "body": "BUFF Cookie已过期, 请重新登录"
    },
    // 二维码登录BUFF通知配置
    "buff_login_notification": {
      // 二维码登录BUFF通知标题（如不需要可直接删除）
      "title": "请扫描二维码登录BUFF"
    },
    // 通知服务器列表，使用Apprise格式，详见https://github.com/caronc/apprise/
    "servers": [
    ]
  },
  // BUFF 自动备注购买价格插件配置
  "buff_auto_comment": {
    // 是否启用BUFF自动备注购买价格功能
    "enable": true
  },
  // BUFF 自动计算利润插件配置
  "buff_profit_report": {
    // 是否启用BUFF自动计算利润功能
    "enable": false,
    // 通知服务器列表，使用Apprise格式，详见https://github.com/caronc/apprise/
    "servers": [
    ],
    // 每日发送报告时间, 24小时制
    "send_report_time": "20:30"
  },
  // BUFF 自动上架插件配置
  "buff_auto_on_sale": {
    // 是否启用BUFF自动以最低价上架所有库存
    "enable": false,
    // 每次检查库存强制刷新BUFF库存, 若为否, 刷新不一定会加载最新库存
    "force_refresh": true,
    // 使用磨损区间最低价上架, 若为否, 则使用类型最低价上架
    // 注意: 该功能会导致增加更多的请求, 请谨慎开启
    "use_range_price": false,
    // 黑名单时间, 为小时, int格式, 空为不启用黑名单, 当前小时如果等于黑名单时间, 则不会自动上架
    "blacklist_time": [],
    // 白名单时间, 为小时, int格式, 空为不启用白名单, 当前小时如果不等于白名单时间, 则不会自动上架
    "whitelist_time": [],
    // 随机上架几率, 为整数, 1~100, 100为100%上架, 1为1%上架, 0为不上架
    "random_chance": 100,
    // 商品上架描述, 为字符串, 为空则不填写描述
    "description": "",
    // 检查库存间隔时间
    "interval": 1800,
    // 每个请求间隔时间 (秒) - 用于防止被BUFF封禁
    "sleep_seconds_to_prevent_buff_ban": 10,
    // 供应求购相关配置
    "buy_order": {
      // 是否供应求购订单
      "enable": true,
      // 是否只供应给开启自动收货的求购订单
      "only_auto_accept": true,
      // 支持收款方式 支付宝 微信
      "supported_payment_method": ["支付宝"],
      // 低于多少金额的商品直接塞求购
      "min_price": 5
    },
    // 上架通知配置(如不需要可直接删除)
    "on_sale_notification": {
      // 上架通知标题
      "title": "游戏 {game} 成功上架 {sold_count} 件饰品",
      // 上架通知内容
      "body": "上架详情:\n{item_list}"
    },
    // 出现验证码通知配置(如不需要可直接删除)
    "captcha_notification": {
      // 出现验证码通知标题
      "title": "上架饰品时出现验证码",
      // 出现验证码通知内容
      "body": "使用session={session}并使用浏览器打开以下链接并完成验证:\n{captcha_url}"
    },
    // 通知服务器列表，使用Apprise格式，详见https://github.com/caronc/apprise/
    "servers": [
    ]
  },
  // 悠悠有品自动发货插件配置
  "uu_auto_accept_offer": {
    // 悠悠有品自动发货功能是否启用，默认为false
    "enable": false,
    // 每次检查是否有新报价的间隔（轮询间隔），单位为秒
    "interval": 300
  },
  // Steam 自动接受礼物报价插件配置
  "steam_auto_accept_offer": {
    // 是否开启自动接受Steam礼物报价（无需支出任何Steam库存中的物品的报价）
    "enable": false,
    // 每次检查报价列表的间隔（轮询间隔），单位为秒
    "interval": 300
  },
  // 是否开启开发者模式，具体功能请查看代码，非开发者请勿开启！开启后无法正常使用！
  "development_mode": false
}
```

##### `steam_account_info.json5`
```json5
{

  // 新版Steamauto已经无需手动填写API_KEY、steamid、buff_cookies.txt(均可自动获取)，视频教程暂未更新，请悉知！！！
  // 新版Steamauto已经无需手动填写API_KEY、steamid、buff_cookies.txt(均可自动获取)，视频教程暂未更新，请悉知！！！
  // 新版Steamauto已经无需手动填写API_KEY、steamid、buff_cookies.txt(均可自动获取)，视频教程暂未更新，请悉知！！！

  // Steam 令牌参数（用于身份验证）
  "shared_secret": "",

  // Steam 令牌参数（用于身份验证）
  "identity_secret": "",

  // Steam 登录时填写的用户名
  "steam_username": "",

  // Steam 登录时填写的密码
  "steam_password": ""
}
```

##### `notification`相关配置项说明
| 配置项                              | 描述                                                                                                                               |
|----------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| sell_notification                | 出售通知(如不需要可直接删除)                                                                                                                  |
| protection_notification          | 出售保护通知(如不需要可直接删除)                                                                                                                |
| item_mismatch_notification       | 报价与BUFF出售商品不匹配通知配置(如不需要可直接删除)                                                                                                    |
| buff_cookie_expired_notification | BUFF Cookies失效通知(如不需要可直接删除)                                                                                                      |
| ---                              | ---                                                                                                                              |
| title                            | 通知标题                                                                                                                             |
| body                             | 通知内容                                                                                                                             |
| servers                          | Apprise格式服务器列表 - 详见[Apprise](https://github.com/caronc/apprise)<br>- 额外支持 [Server酱](https://sct.ftqq.com/) 格式为`ftqq://<SENDKEY>` |

## FAQ
##### 账号安全问题?
Steamauto的所有源代码均开放在GitHub，可供所有人自行查看代码安全性  
在用户的电脑不被恶意软件入侵的情况下，账号不可能泄露  

##### SDA报错`未将对象引用设置到对象的实例`?  
![报错如图](https://github.com/jiajiaxd/Steamauto/assets/51043917/b1282372-11f6-4649-be5f-7bc52faf4c16)  
请先移除手机令牌再使用SDA

##### 为什么我打开配置文件后，编辑器提示该文件有语法错误？
本程序使用的配置文件类型为json5，因此在不受支持编辑器中会提示语法错误，但实际上并不影响程序的运行  

##### 能否处理卖家发起报价的情况？
不支持，但是有以下解决方案。
在BUFF上，你可以打开[BUFF网页版的个人设置页面](https://buff.163.com/user-center/profile)，并勾上偏好设置中的`出售限定买家先发报价`  
在悠悠有品上，暂无解决方案，你需要手动处理  

##### 如何获取悠悠有品token?
使用`-uu`参数或者在程序所在目录下创建`uu.txt`(无需填入任何内容),运行Steamauto程序,根据程序向导操作即可  

##### 是否支持多开？  
支持。但是需要复制多份程序，分别在不同的文件夹内运行  
如果你只需要Buff自动发货多开，你也可以尝试[支持多账户的Fork版本](https://github.com/ZWN2001/Steamauto)  

##### 可否关闭Buff自动发货？
将`config.json`中`buff_auto_accept_offer.enable`设置为false即可

##### 使用`proxies`配置运行源码时出现代理错误但本地代理没问题

该错误在特定`urllib`下会出现，安装特定版本可以解决

```
pip install urllib3==1.25.11
```

`steampy/client.py` 44-48行注释掉的代码解除注释后若出现报错则说明是此问题

## 附录
关于`steam_account_info.json`相关参数的获取教程都在下面, 请自行参阅  
个人推荐使用[ SteamDesktopAuthenticator(简称SDA) ](https://github.com/Jessecar96/SteamDesktopAuthenticator)获取Steam令牌参数 操作简便(请勿使用1.0.13版本,存在无法获取的问题)  
[官方视频教程](https://www.bilibili.com/video/BV1ph4y1y7mz/)    
[已Root安卓手机获取新版Steam手机令牌教程](https://github.com/BeyondDimension/SteamTools/issues/2598)

## 鸣谢
感谢 [**@lupohan44**](https://github.com/lupohan44) 为本项目提交的大量代码！ 

感谢 devgod, 14m0k(QQ群用户) 在开发供应求购订单功能时的巨大帮助！

感谢 [1Password](https://1password.com/) 为开源项目提供免费的 [1Password](https://1password.com/) 团队账户的授权