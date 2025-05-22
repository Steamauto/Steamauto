import os # Retained for pause, though not strictly necessary for the remaining functions.
from apscheduler.job import Job

from utils.logger import logger
import utils.static as static

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
    def add(job: Job):
        global jobs
        jobs.append(job)

    @staticmethod
    def terminate_all():
        global jobs
        # Iterate safely for removal
        for job in list(jobs): # Create a copy for iteration
            try:
                job.pause()
                job.remove()
                if job in jobs: # Check if still present before removing
                    jobs.remove(job)
            except Exception as e:
                logger.error(f"终止任务 {job} 时出错: {e}")


def pause():
    if not static.no_pause:
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
