# Buff-Bot
使用Python3和Requests库实现的网易BUFF饰品交易平台(csgo)全自动发货  
**请仔细阅读教程！**  
**欢迎有能力者提交PR来完善本程序。**  

## 如何使用？
1. 确保你的系统内已经安装Python  
2. 安装依赖
    ```
    pip install steampy
    ```
3. 下载仓库并解压 
4. 打开`steamaccount.txt`，修改所有参数（相关教程见FAQ）
5. 打开`cookies.txt`，填入[网易BUFF](https://buff.163.com)的cookie
6. 在命令行内输入```python Buff-Bot.py```
7. Enjoy.
## FAQ
**1.支持Linux？**  
完美支持.

**2.`steamaccount.txt`说明**  
steamid:Steam的数字ID  
shared_secret:Steam令牌参数  
identity_secret:Steam令牌参数  
api_key:Steam网页API密钥  
steam_username:Steam登录时填写的用户名  
steam_password:Steam登录时填写的密码  
**部分参数获取教程请查看附录**

## 附录
关于`steamaccount.txt`相关参数的获取教程都在下面，请自行参阅  
个人推荐使用[ Watt Toolkit ](https://github.com/BeyondDimension/SteamTools)获取Steam令牌参数 操作非常简便

[获取Steam网页API KEY](http://steamcommunity.com/dev/apikey)   
[Steam令牌介绍以及提取转移](https://steam.red/blog/archives/Steamguard.html)  
[buffhelp 网易buff自动发货-哔哩哔哩（请查看P2-P7）](https://www.bilibili.com/video/BV1DT4y1P7Dx)  
[Obtaining SteamGuard from mobile device]( https://github.com/SteamTimeIdler/stidler/wiki/Getting-your-%27shared_secret%27-code-for-use-with-Auto-Restarter-on-Mobile-Authentication )  
[Obtaining SteamGuard using Android emulation]( https://github.com/codepath/android_guides/wiki/Genymotion-2.0-Emulators-with-Google-Play-support)
