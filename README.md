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

> 广告:
> 【ECOSteam】复制整段打开ECOSteam加入「Elsa可达鸭」PN：01J0TGJPQZF7G9FK0P6QJWQGRJ「欢迎加入Elsa可达鸭的团队或点击链接https://share.ecosteam.cn/share/01J0TGJPQZF7G9FK0P6QJWQGRJ「欢迎加入Elsa可达鸭的团队
> ECOSteam 新CSGO皮肤交易平台
> 交易0手续费 提现1% 满1万余额需要提现可私聊我免费领取提现券
> 货多的还可以联系群管申请交易补贴!(2%)
> 可租可售 上架就有利息拿 最高可拿15%利息 加入我的团队拿额外2%利息(总金额要求已经达到 只需要人数)
> 本软件完美支持ECOSteam, 请放心使用

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
- 出租/出售自动上架
  - 出售支持:
    - [ ] 按区间定价
    - [X] 按止盈率定价（需要设定购入价）

#### 在 [ECOSteam交易平台](https://www.ecosteam.cn/) 上:

- 自动发货
- 与BUFF、悠悠有品所上架商品同步 (支持比例)

#### 在 Steam 上:

- 内置Steam加速器
- 自动接受礼物报价(无需支出任何Steam库存中的物品的报价)

## 如何使用?

[推荐观看视频教程](https://www.bilibili.com/video/BV1ph4y1y7mz)
0. ~~给予本仓库一个star(手动狗头)~~

1. 前往 [Github Releases](https://github.com/jiajiaxd/Steamauto/releases/latest) 下载适合自己系统的Steamauto
2. 运行一次程序，程序会释放配置文件
3. 编辑 `config`文件夹下的 `config.json5`(相关教程见FAQ)
4. 修改 `config`文件夹下的 `steam_account_info.json5`中所有的参数(相关教程见配置说明)
5. **(若有需求Buff相关功能)** 在 `config.json5`中启用BUFF相关功能并直接运行程序(程序会自动填写buff_cookies.txt)
6. **(若有需求悠悠有品相关功能)** 打开 `uu_token.txt`,填入[悠悠有品](https://www.youpin898.com/)的token(如何获取token,见FAQ)

## 配置说明

**部分配置项数据(如获取Steam账户信息、Buff的cookie等)在附录中，请自行查阅！**

##### `config.json5`

请前往 [配置页面](https://pages.steamauto.jiajiaxd.com/config) 生成配置，并粘贴至`config/config.json5`

注意：配置页面目前处于测试阶段，若有BUG请及时反馈！

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

| 配置项                           | 描述                                                                                                                                                      |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| sell_notification                | 出售通知(如不需要可直接删除)                                                                                                                              |
| protection_notification          | 出售保护通知(如不需要可直接删除)                                                                                                                          |
| item_mismatch_notification       | 报价与BUFF出售商品不匹配通知配置(如不需要可直接删除)                                                                                                      |
| buff_cookie_expired_notification | BUFF Cookies失效通知(如不需要可直接删除)                                                                                                                  |
| ---                              | ---                                                                                                                                                       |
| title                            | 通知标题                                                                                                                                                  |
| body                             | 通知内容                                                                                                                                                  |
| servers                          | Apprise格式服务器列表 - 详见[Apprise](https://github.com/caronc/apprise)`<br>`- 额外支持 [pushplus](https://www.pushplus.plus/) 格式为 `pushplus://<token>` |

## FAQ

##### 账号安全问题?

Steamauto的所有源代码均开放在GitHub，可供所有人自行查看代码安全性
在用户的电脑不被恶意软件入侵的情况下，账号不可能泄露

##### SDA报错 `未将对象引用设置到对象的实例`?

![报错如图](https://github.com/jiajiaxd/Steamauto/assets/51043917/b1282372-11f6-4649-be5f-7bc52faf4c16)
请先移除手机令牌再使用SDA

##### 为什么我打开配置文件后，编辑器提示该文件有语法错误？

本程序使用的配置文件类型为json5，因此在不受支持编辑器中会提示语法错误，但实际上并不影响程序的运行

##### 能否处理卖家发起报价的情况？

不支持，但是有以下解决方案。
在BUFF上，你可以打开[BUFF网页版的个人设置页面](https://buff.163.com/user-center/profile)，并勾上偏好设置中的 `出售限定买家先发报价`
在悠悠有品上，暂无解决方案，你需要手动处理

##### 如何获取悠悠有品token?

~~使用 `-uu`参数或者在程序所在目录下创建 `uu.txt`(无需填入任何内容),运行Steamauto程序,根据程序向导操作即可~~
在最新版本中直接运行程序，若token无效程序会自动引导你获取有效的token

##### 是否支持多开？

支持。但是需要复制多份程序，分别在不同的文件夹内运行
如果你只需要Buff自动发货多开，你也可以尝试[支持多账户的Fork版本](https://github.com/ZWN2001/Steamauto)

##### 可否关闭Buff自动发货？

将 `config.json`中 `buff_auto_accept_offer.enable`设置为false即可

##### 使用 `proxies`配置运行源码时出现代理错误但本地代理没问题

该错误在特定 `urllib`下会出现，安装特定版本可以解决

```
pip install urllib3==1.25.11
```

`steampy/client.py` 44-48行注释掉的代码解除注释后若出现报错则说明是此问题

## 附录

### 获取 Steam 账户信息

关于 `steam_account_info.json`相关参数的获取教程都在下面, 请自行参阅
个人推荐使用[ SteamDesktopAuthenticator(简称SDA) ](https://github.com/Jessecar96/SteamDesktopAuthenticator)获取Steam令牌参数 操作简便(请勿使用1.0.13版本,存在无法获取的问题)
[官方视频教程](https://www.bilibili.com/video/BV1ph4y1y7mz/)
[已Root安卓手机获取新版Steam手机令牌教程](https://github.com/BeyondDimension/SteamTools/issues/2598)

### 如何注册 ECOSteam 开放平台 - 节选自[ECOSteam官方文档](https://docs.qq.com/aio/DRnR2U05aeG5MT0RS?p=tOOCPKrP8CUmptM7fhIq7p)

1. 申请接入流程
   1. 注册并登录ECO App：
   2. 进入【我的】，点击右上角设置；
   3. 点击【账号与安全】进入；
   4. 点击【开放能力申请】进入介绍页面；
   5. 点击申请入驻；
   6. 填写申请资料并提交，回调地址和回调开关配置审核通过后可修改；  // 备注: 此处如需上传身份证正反面照片, 可随意上传图片, 不会进行审核
   7. 等待审核；  // 备注: 实际上是自动审核, 申请后立刻可用
2. 审核通过后流程
   1. 审核通过的用户，可回到页面点击【查看身份ID】；
   2. 输入RSA公钥后，获取身份ID；  // 备注: RSA私钥在插件运行后需要填写进在config目录下的rsakey.txt中, 请自行生成RSA密钥对, 建议使用2048位或4096位密钥, 如果你不会生成且不想学习, 可以使用在线生成工具生成, 例如[https://www.ssleye.com/ssltool/pass_double.html](https://www.ssleye.com/ssltool/pass_double.html) (若使用此网站, 请设置算法: RSA, 强度: 2048或4096, 密码留空, 安全性我们不能作保证, 请自行判断)
      ~~只使用**不带换行格式**的密钥内容部分。~~ ECOSteam已经支持完整格式的密钥内容部分
   3. 如开启回调通知，则需配置回调地址和获取ECO的回调公钥；

## 鸣谢

感谢 [**@lupohan44**](https://github.com/lupohan44) 为本项目提交的大量代码！

感谢 devgod, 14m0k(QQ群用户) 在开发供应求购订单功能时的巨大帮助！

感谢 [1Password](https://1password.com/) 为开源项目提供免费的 [1Password](https://1password.com/) 团队账户的授权
