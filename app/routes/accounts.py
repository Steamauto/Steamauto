"""Account management routes."""
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel
from app.accounts import (
    add_account,
    delete_account,
    get_account,
    get_current_account,
    list_accounts,
    set_current,
    update_account,
)
from app.services.steam_auth import verify_steam_auto_login
from app.services.account_region import refresh_account_region_currency
from app.state import log
router = APIRouter()
class AccountBody(BaseModel):
    username: str = ""
    password: str = ""
    steam_id: str = ""
    display_name: str = ""
    avatar_url: str = ""
class AccountUpdateBody(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    steam_id: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
@router.get("/api/accounts")
def api_list_accounts():
    accs = list_accounts()
    cid = get_current_account()
    current_id = cid.get("id") if cid else None
    return {"accounts": accs, "current_id": current_id}
@router.post("/api/accounts")
def api_add_account(body: AccountBody):
    acc = add_account(
        username=body.username,
        password=body.password,
        steam_id=body.steam_id,
        display_name=body.display_name,
        avatar_url=body.avatar_url,
    )
    return {"ok": True, "account": acc}
@router.put("/api/accounts/{account_id}")
def api_update_account(account_id: str, body: AccountUpdateBody):
    kwargs = {}
    if body.username is not None:
        kwargs["username"] = body.username
    if body.password is not None and body.password:
        kwargs["password"] = body.password
    if body.steam_id is not None:
        kwargs["steam_id"] = body.steam_id
    if body.display_name is not None:
        kwargs["display_name"] = body.display_name
    if body.avatar_url is not None:
        kwargs["avatar_url"] = body.avatar_url
    acc = update_account(account_id, **kwargs) if kwargs else get_account(account_id)
    if not acc:
        return {"ok": False, "error": "账号不存在"}
    return {"ok": True, "account": acc}
@router.delete("/api/accounts/{account_id}")
def api_delete_account(account_id: str):
    ok = delete_account(account_id)
    return {"ok": ok, "error": None if ok else "删除失败"}
@router.post("/api/accounts/{account_id}/set_current")
def api_set_current_account(account_id: str):
    ok = set_current(account_id)
    return {"ok": ok, "error": None if ok else "账号不存在"}
@router.post("/api/accounts/{account_id}/verify")
def api_verify_account(account_id: str):
    result = verify_steam_auto_login(account_id)
    if result.get("ok"):
        sync_result = refresh_account_region_currency(account_id)
        result["region_sync"] = sync_result
        if sync_result.get("ok"):
            log(
                "account_verify: 结算币种确认成功 "
                f"account_id={account_id} "
                f"currency={sync_result.get('currency_code')} "
                f"derived_region={sync_result.get('region_code')}",
                "debug",
                category="account",
            )
        else:
            log(
                "account_verify: 结算币种确认失败 "
                f"account_id={account_id} "
                f"error={sync_result.get('error') or '未知原因'}",
                "warn",
                category="account",
            )
    return {
        "ok": result.get("ok", False),
        "status": result.get("status", "error"),
        "message": result.get("message", "验证失败"),
        "region_sync": result.get("region_sync"),
    }
