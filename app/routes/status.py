"""Status, log, plan, and payment-related routes."""
from pathlib import Path
from fastapi import APIRouter
from app.config_loader import load_app_config_validated
from app.state import (
    clear_log,
    confirm_payment,
    get_log,
    get_pending_payment,
    get_plan,
    get_status,
    set_pending_payment,
)
from config import get_buff
from pydantic import BaseModel
from utils.time import (
    now_in_configured_timezone,
    resolve_configured_timezone,
    timestamp_in_configured_timezone,
)
router = APIRouter()
class ConfirmBody(BaseModel):
    ok: bool
@router.get("/api/status")
def api_status():
    st = get_status()
    buff_creds = get_buff()
    st["buff_no_cookie"] = not bool((buff_creds.get("cookies") or "").strip())
    return st

@router.get("/api/log")
def api_log(since: int = 0):
    return {"lines": get_log(since)}
@router.post("/api/log/clear")
def api_log_clear():
    clear_log()
    return {"ok": True}
@router.post("/api/log/export")
def api_log_export():
    lines = get_log(0)
    log_dir = Path("log")
    log_dir.mkdir(exist_ok=True)
    configured_timezone, timezone_label = resolve_configured_timezone(
        (load_app_config_validated().get("system") or {})
    )
    ts = now_in_configured_timezone(configured_timezone).strftime("%Y%m%d_%H%M%S")
    filename = log_dir / f"debug_{ts}.txt"
    def fmt_time(t):
        if t is None:
            return ""
        return timestamp_in_configured_timezone(
            t,
            configured_timezone,
        ).strftime("%Y-%m-%d %H:%M:%S")
    content = "\n".join(
        f"{fmt_time(e.get('t'))} [{e.get('level', 'info')}] {e.get('msg', '')}"
        for e in lines
    ) + f"\n# timezone: {timezone_label}\n"
    filename.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(filename), "lines": len(lines)}
@router.get("/api/plan")
def api_plan():
    return {"plan": get_plan()}
@router.get("/api/pending_payment")
def api_pending_payment():
    return {"pending": get_pending_payment()}
@router.post("/api/confirm_payment")
def api_confirm_payment(body: ConfirmBody):
    confirm_payment(body.ok)
    set_pending_payment(None)
    return {"ok": True}
