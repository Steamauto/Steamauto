"""
API routes for multi-platform delivery, trade offers, and Steam 2FA confirmations.
"""
from typing import Optional
from fastapi import APIRouter, Query, BackgroundTasks
from pydantic import BaseModel

from app.config_loader import load_app_config_validated, update_app_config_validated
from app.database import db_get_trade_orders
from app.services.trade_delivery_worker import (
    get_delivery_runtime_status,
    run_trade_confirmations,
    process_buff_delivery,
    process_uu_delivery,
    process_steam_gift_offers,
)

router = APIRouter(prefix="/api/delivery", tags=["delivery"])


class DeliverySettingsBody(BaseModel):
    enabled: Optional[bool] = None
    poll_interval_seconds: Optional[int] = None
    buff_auto_ship: Optional[bool] = None
    buff_auto_accept: Optional[bool] = None
    uu_token: Optional[str] = None
    uu_auto_ship: Optional[bool] = None
    uu_auto_accept: Optional[bool] = None
    uu_auto_lease: Optional[bool] = None
    c5_app_key: Optional[str] = None
    c5_app_secret: Optional[str] = None
    c5_auto_ship: Optional[bool] = None
    ecosteam_partner_id: Optional[str] = None
    ecosteam_api_key: Optional[str] = None
    ecosteam_auto_ship: Optional[bool] = None
    steam_auto_accept_gifts: Optional[bool] = None


@router.get("/status")
def api_delivery_status():
    """获取多平台自动收发货与2FA确认的运行心跳与状态"""
    return {"ok": True, "status": get_delivery_runtime_status()}


@router.get("/orders")
@router.get("/trades")
def api_delivery_orders(
    limit: int = Query(50, ge=1, le=500),
    platform: Optional[str] = Query(None),
):
    """分页获取收发货与2FA签名交易流水"""
    orders = db_get_trade_orders(limit=limit, platform=platform)
    return {"ok": True, "orders": orders}


@router.get("/settings")
def api_delivery_get_settings():
    """获取当前收发货配置"""
    cfg = load_app_config_validated()
    return {"ok": True, "delivery": cfg.get("delivery", {})}


@router.post("/settings")
def api_delivery_save_settings(body: DeliverySettingsBody):
    """动态保存收发货各平台配置（无需重启服务生效）"""
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = update_app_config_validated({"delivery": patch})
    return {"ok": True, "delivery": updated.get("delivery", {})}


@router.post("/confirm-now")
def api_delivery_confirm_now():
    """立即扫描并签署当前待处理的 Steam 移动端交易确认"""
    count = run_trade_confirmations()
    return {"ok": True, "confirmed_count": count}


@router.post("/trigger-poll")
def api_delivery_trigger_poll(background_tasks: BackgroundTasks):
    """立即手动触发一次全平台收发货与2FA轮询"""
    def _poll_once():
        cfg = load_app_config_validated()
        delivery_cfg = cfg.get("delivery", {})
        process_buff_delivery(delivery_cfg)
        process_uu_delivery(delivery_cfg)
        process_steam_gift_offers(delivery_cfg)
        run_trade_confirmations()

    background_tasks.add_task(_poll_once)
    return {"ok": True, "message": "全平台收发货轮询已在后台触发"}
