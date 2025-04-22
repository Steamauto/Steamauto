import datetime
import platform

import pytz

asia = pytz.timezone("Asia/Shanghai")
with open("utils/build_info.py", "w", encoding='utf-8') as f:
    f.write(f'info = "{datetime.datetime.now(asia).strftime("%Y-%m-%d %H:%M:%S")} on {platform.system()} {platform.release()}({platform.version()})"')
