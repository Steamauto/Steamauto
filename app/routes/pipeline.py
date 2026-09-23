"""Pipeline start/stop routes."""
from fastapi import APIRouter
from pydantic import BaseModel
from app.pipeline import get_pipeline_start_blocker, start_pipeline
from app.state import request_stop, set_status, log
router = APIRouter()
class ConfigBody(BaseModel):
    config: dict
    acknowledge_buff_reconciliation: bool = False
    buff_reconciliation_intent_id: str = ""
@router.post("/api/pipeline/start")
def api_pipeline_start(body: ConfigBody):
    blocker = get_pipeline_start_blocker()
    if (
        blocker.get("code") == "BUFF_RECONCILIATION_REQUIRED"
        and not body.acknowledge_buff_reconciliation
    ):
        return {
            "ok": False,
            "reconciliation_required": True,
            "code": blocker["code"],
            "error": blocker["message"],
            "checkout": blocker.get("checkout") or {},
        }
    if blocker and blocker.get("code") != "BUFF_RECONCILIATION_REQUIRED":
        return {
            "ok": False,
            "code": blocker.get("code"),
            "error": blocker.get("message"),
        }
    if not start_pipeline(
        body.config,
        acknowledge_buff_reconciliation=body.acknowledge_buff_reconciliation,
        buff_reconciliation_intent_id=body.buff_reconciliation_intent_id,
    ):
        blocker = get_pipeline_start_blocker()
        if blocker:
            return {
                "ok": False,
                "reconciliation_required": (
                    blocker.get("code") == "BUFF_RECONCILIATION_REQUIRED"
                ),
                "code": blocker.get("code"),
                "error": blocker.get("message"),
                "checkout": blocker.get("checkout") or {},
            }
        log("买入流水线已在运行，忽略重复启动请求", level="warn", category="pipeline")
        return {"ok": False, "already_running": True, "error": "买入流水线已在运行，请勿重复启动"}
    return {"ok": True}


@router.get("/api/pipeline/buff_checkout_guard")
def api_buff_checkout_guard():
    blocker = get_pipeline_start_blocker()
    return {
        "reconciliation_required": (
            blocker.get("code") == "BUFF_RECONCILIATION_REQUIRED"
        ),
        "checkout": blocker.get("checkout") or {},
    }
@router.post("/api/pipeline/stop")
def api_pipeline_stop():
    request_stop()
    log("接收到停止运行指令，正在终止任务...", level="warn", category="system")
    set_status("stopped", "正在停止并清理...")
    return {"ok": True}
