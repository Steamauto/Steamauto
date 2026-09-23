from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.strategy_engine import (
    StrategyError,
    activate_strategy,
    delete_strategy,
    export_strategy,
    get_strategy_payload,
    import_strategy,
    import_user_module,
    save_strategy,
    simulate_strategy,
)


router = APIRouter()


class StrategyBody(BaseModel):
    strategy: dict


class ActivateBody(BaseModel):
    risk_confirmed: bool = False


class ImportBody(BaseModel):
    strategy: dict


class ModuleImportBody(BaseModel):
    manifest: dict


class SimulateBody(BaseModel):
    strategy_id: str = ""
    strategy: dict = Field(default_factory=dict)


def _error(exc: Exception):
    return {"ok": False, "error": str(exc)}


@router.get("/api/strategies")
def api_strategies():
    return {"ok": True, **get_strategy_payload()}


@router.post("/api/strategies")
def api_save_strategy(body: StrategyBody):
    try:
        return {"ok": True, "strategy": save_strategy(body.strategy)}
    except StrategyError as exc:
        return _error(exc)


@router.delete("/api/strategies/{strategy_id}")
def api_delete_strategy(strategy_id: str):
    try:
        delete_strategy(strategy_id)
        return {"ok": True}
    except StrategyError as exc:
        return _error(exc)


@router.post("/api/strategies/{strategy_id}/activate")
def api_activate_strategy(strategy_id: str, body: ActivateBody):
    try:
        return activate_strategy(strategy_id, risk_confirmed=body.risk_confirmed)
    except StrategyError as exc:
        return _error(exc)


@router.post("/api/strategies/import")
def api_import_strategy(body: ImportBody):
    try:
        return {"ok": True, "strategy": import_strategy(body.strategy)}
    except StrategyError as exc:
        return _error(exc)


@router.get("/api/strategies/{strategy_id}/export")
def api_export_strategy(strategy_id: str):
    try:
        return {"ok": True, "strategy": export_strategy(strategy_id)}
    except StrategyError as exc:
        return _error(exc)


@router.post("/api/strategy-modules/import")
def api_import_strategy_module(body: ModuleImportBody):
    try:
        return {"ok": True, "module": import_user_module(body.manifest)}
    except StrategyError as exc:
        return _error(exc)


@router.post("/api/strategies/simulate")
def api_simulate_strategy(body: SimulateBody):
    try:
        payload = {"strategy_id": body.strategy_id, "strategy": body.strategy}
        return simulate_strategy(payload)
    except StrategyError as exc:
        return _error(exc)
