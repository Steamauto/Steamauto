# Buff-Bot
使用Python3和Requests库实现的网易BUFF饰品交易平台全自动发货/求购收货  
**请仔细阅读教程！**  
**欢迎有能力者提交PR来完善本程序。**  

## 如何使用?
1. 确保你的系统内已经安装Python  
2. 下载仓库并解压
3. 安装依赖
    ```
    pip install -r requirements.txt
    ```
4. 执行程序, 在命令行内输入```python Buff-Bot.py```
5. 将 `config.example.json` 复制到 `config.json` 并修改配置(相关教程见FAQ)
6. 打开`steamaccount.json`, 修改所有参数(相关教程见FAQ)
7. 打开`cookies.txt`, 填入[网易BUFF](https://buff.163.com)的cookie(包含session即可)
8. Enjoy.
## FAQ
**1.支持Linux?**  
完美支持.

**2. `config.json` 说明**
title:通知标题  
content:通知内容  
sell_notification:出售通知(如不需要可直接删除)  
servers:Apprise格式服务器列表 - 详见[Apprise](https://github.com/caronc/apprise)  
- 额外支持 [Server酱](https://sct.ftqq.com/) 格式为`ftqq://<SENDKEY>`

**3.`steamaccount.json`说明**  
steamid:Steam的数字ID  
shared_secret:Steam令牌参数  
identity_secret:Steam令牌参数  
api_key:Steam网页API密钥  
steam_username:Steam登录时填写的用户名  
steam_password:Steam登录时填写的密码  
**部分参数获取教程请查看附录**

## 附录
关于`steamaccount.json`相关参数的获取教程都在下面, 请自行参阅  
个人推荐使用[ Watt Toolkit ](https://github.com/BeyondDimension/SteamTools)获取Steam令牌参数 操作非常简便

[获取Steam网页API KEY](http://steamcommunity.com/dev/apikey)   
[Steam令牌介绍以及提取转移](https://steam.red/blog/archives/Steamguard.html)  
[buffhelp 网易buff自动发货-哔哩哔哩(请查看P2-P7)](https://www.bilibili.com/video/BV1DT4y1P7Dx)  
[Obtaining SteamGuard from mobile device]( https://github.com/SteamTimeIdler/stidler/wiki/Getting-your-%27shared_secret%27-code-for-use-with-Auto-Restarter-on-Mobile-Authentication )  
[Obtaining SteamGuard using Android emulation]( https://github.com/codepath/android_guides/wiki/Genymotion-2.0-Emulators-with-Google-Play-support)
