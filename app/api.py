"""
FastAPI application entry point.
All routes are registered via `app.routes`, and all background
workers are started from `app.services.workers`.  This file is
intentionally kept minimal.
"""
import sys
import threading
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from app.database import init_db, migrate_from_json
init_db()
migrate_from_json()
from app.services.workers import (
    exchange_rate_worker,
    holdings_report_worker,
    listing_check_worker,
    receive_worker,
    sync_account_region_worker,
    session_keepalive_worker,
)
from app.services.task_queue import get_task_queue
from app.state import log, request_stop
from app.runtime_env import get_runtime_profile
from app.services.trade_delivery_worker import start_trade_delivery_worker, stop_trade_delivery_worker
_bg_started = False
_bg_lock = threading.Lock()
def _start_background_workers() -> None:
    global _bg_started
    with _bg_lock:
        if _bg_started:
            return
        _bg_started = True
    q = get_task_queue()
    q.submit(sync_account_region_worker, name="sync_account_region", max_retries=3, retry_base_delay=10.0)
    for fn in (
        receive_worker,
        listing_check_worker,
        exchange_rate_worker,
        holdings_report_worker,
        session_keepalive_worker,
    ):
        q.submit(fn, name=fn.__name__, max_retries=3, retry_base_delay=5.0)
@asynccontextmanager
async def _lifespan(application: FastAPI):
    _start_background_workers()
    start_trade_delivery_worker()
    yield
    stop_trade_delivery_worker()
app = FastAPI(title="aetherswap", lifespan=_lifespan)
@app.get("/api/runtime")
def api_runtime():
    return {"ok": True, "runtime": get_runtime_profile().as_dict()}

@app.get("/api/tasks")
def api_tasks(limit: int = 50):
    q = get_task_queue()
    return {"tasks": q.list_tasks(limit=limit), "active": q.active_count()}
@app.get("/api/tasks/{task_id}")
def api_task_detail(task_id: str):
    q = get_task_queue()
    info = q.get_task(task_id)
    if info is None:
        return {"ok": False, "error": "任务不存在"}
    return {"ok": True, "task": info}
@app.post("/api/system/shutdown")
def shutdown_system(background_tasks: BackgroundTasks):
    def _do_shutdown():
        time.sleep(1)  
        log("system: 收到前端关机请求，正在通知所有 worker 停止...", "info", category="system")
        request_stop()  
        time.sleep(1)  
        log("system: 正在退出进程...", "info", category="system")
        import os, signal
        os.kill(os.getpid(), signal.SIGINT)
    background_tasks.add_task(_do_shutdown)
    return {"ok": True, "message": "正在彻底退出系统..."}
from app.routes import register_routes
register_routes(app)
