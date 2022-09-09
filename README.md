# PyBuffHelper
# 注意：此库为Selenium版的buff-bot，且已经停止更新支持！
使用Selenium实现的BUFF全自动发货(csgo)  
## 如何使用？
1. 确保你的系统内已经安装Chrome和Python  
2. 安装依赖
```
pip install undetected-chromedriver
pip install steampy
```
3. 下载仓库并解压 
4. 打开main.py，修改clientid、steamid和steampassword（见FAQ）
5. 在程序运行目录新建steamguard.txt，并添加以下内容
```
{
    "steamid": "YOUR_STEAM_ID_64",
    "shared_secret": "YOUR_SHARED_SECRET",
    "identity_secret": "YOUR_IDENTITY_SECRET"
}
```
6. 在命令行内输入```python main.py```来运行PyBuffHelper  
## FAQ
1.clientid是什么？  
即 网页APIKEY  

2.steamguard.txt内的内容如何填写？  
见附录

3.支持Linux？  
不推荐但是支持。可能会出现部分不影响程序正常运行的BUG。

## 附录
[Obtaining API Key](http://steamcommunity.com/dev/apikey)

[Obtaining SteamGuard from mobile device]( https://github.com/SteamTimeIdler/stidler/wiki/Getting-your-%27shared_secret%27-code-for-use-with-Auto-Restarter-on-Mobile-Authentication )

[Obtaining SteamGuard using Android emulation]( https://github.com/codepath/android_guides/wiki/Genymotion-2.0-Emulators-with-Google-Play-support)
