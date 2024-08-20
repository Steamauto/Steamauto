import os
import random
import re

import chardet

from utils.logger import logger
from utils.static import get_no_pause

from apscheduler.job import Job

current_exit_code = 0
jobs = []
class exit_code:
    @staticmethod
    def set(code: int):
        global current_exit_code
        current_exit_code = code 

    @staticmethod
    def get() -> int:
        global current_exit_code
        return current_exit_code

class jobHandler:
    @staticmethod
    def add(job:Job):
        global jobs
        jobs.append(job)

    @staticmethod
    def terminate_all():
        global jobs
        for job in jobs:
            job.pause()
            job.remove()
            del jobs[jobs.index(job)]

# 用于解决读取文件时的编码问题
def get_encoding(file_path):
    if not os.path.exists(file_path):
        return "utf-8"
    with open(file_path, "rb") as f:
        data = f.read()
        charset = chardet.detect(data)["encoding"]
    return charset


def pause():
    if not get_no_pause():
        logger.info("点击回车键继续...")
        input()


def compare_version(ver1, ver2):
    version1_parts = ver1.split(".")
    version2_parts = ver2.split(".")

    for i in range(max(len(version1_parts), len(version2_parts))):
        v1 = int(version1_parts[i]) if i < len(version1_parts) else 0
        v2 = int(version2_parts[i]) if i < len(version2_parts) else 0

        if v1 < v2:
            return -1
        elif v1 > v2:
            return 1

    return 0


class accelerator:
    def __call__(self, r):
        domain_list = [
            "steamcommunity-a.akamaihd.net",
        ]
        match = re.search(r"(https?://)([^/\s]+)", r.url)
        if match:
            domain = match.group(2)
            r.headers["Host"] = domain
            r.url = re.sub(r"(https?://)([^/\s]+)(.*)", r"\1" + random.choice(domain_list) + r"\3", r.url)
        return r

def is_subsequence(s, t):
    t_index = 0
    s_index = 0
    while t_index < len(t) and s_index < len(s):
        if s[s_index] == t[t_index]:
            s_index += 1
        t_index += 1
    return s_index == len(s)
