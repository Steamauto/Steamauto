import datetime
import platform

with open("utils/static.py", "r+") as f:
    static = f.read()
    static = static.replace(
        "正在使用源码运行",
        f'{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} on {platform.system()} {platform.release()}({platform.version()})',
    )
    f.seek(0)
    f.truncate()
    f.write(static)
