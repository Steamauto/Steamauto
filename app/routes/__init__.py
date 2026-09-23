"""
Routes package – wires all sub-routers into the FastAPI app.
"""
from fastapi import FastAPI
from app.routes.status import router as status_router
from app.routes.transactions import router as transactions_router
from app.routes.pipeline import router as pipeline_router
from app.routes.inventory import router as inventory_router
from app.routes.auth import router as auth_router
from app.routes.accounts import router as accounts_router
from app.routes.config import router as config_router
from app.routes.gift import router as gift_router
from app.routes.proxy import router as proxy_router
from app.routes.steam_deals import router as steam_deals_router
from app.routes.strategies import router as strategies_router
from app.routes.static import router as static_router
from app.routes.delivery import router as delivery_router
def register_routes(app: FastAPI) -> None:
    app.include_router(status_router)
    app.include_router(transactions_router)
    app.include_router(pipeline_router)
    app.include_router(inventory_router)
    app.include_router(auth_router)
    app.include_router(accounts_router)
    app.include_router(config_router)
    app.include_router(gift_router)
    app.include_router(proxy_router)
    app.include_router(steam_deals_router)
    app.include_router(strategies_router)
    app.include_router(static_router)
    app.include_router(delivery_router)
