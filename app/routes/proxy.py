"""代理池配置与测试路由."""
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional
from fastapi import APIRouter
from pydantic import BaseModel
from app.config_loader import load_app_config_validated, save_app_config_validated
router = APIRouter()
class ProxyEntry(BaseModel):
    host: str
    port: int
    username: str = ""
    password: str = ""
class ProxyPoolConfig(BaseModel):
    enabled: bool = False
    strategy: int = 1
    test_url: str = "https://ipv4.webshare.io/"
    timeout_seconds: int = 10
    webshare_api_key: str = ""
    proxies: List[ProxyEntry] = []
class ProxyPoolBody(BaseModel):
    proxy_pool: ProxyPoolConfig
@router.get("/api/proxy/config")
def api_get_proxy_config():
    cfg = load_app_config_validated()
    return {"proxy_pool": cfg.get("proxy_pool", {})}
@router.post("/api/proxy/config")
def api_save_proxy_config(body: ProxyPoolBody):
    cfg = load_app_config_validated()
    new_pool = body.proxy_pool.dict()
    cfg["proxy_pool"] = new_pool
    save_app_config_validated(cfg)
    try:
        from utils.proxy_manager import get_proxy_manager
        pm = get_proxy_manager()
        pm.reload()
        from app.state import log
        log(
            f"[proxy] 配置已保存并重载: enabled={new_pool.get('enabled')} "
            f"strategy={new_pool.get('strategy')} "
            f"代理数={len([p for p in new_pool.get('proxies', []) if p.get('host')])}",
            "info",
            category="proxy",
        )
    except Exception as e:
        pass  
    return {"ok": True}
@router.post("/api/proxy/test")
def api_test_proxies():
    cfg = load_app_config_validated()
    pool_cfg = cfg.get("proxy_pool", {})
    proxies_list = pool_cfg.get("proxies", [])
    test_url = pool_cfg.get("test_url", "https://ipv4.webshare.io/")
    timeout = int(pool_cfg.get("timeout_seconds", 10))
    from utils.proxy_manager import test_one_proxy
    if not proxies_list:
        return {"results": []}
    results = []
    max_workers = min(len(proxies_list), 20)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(test_one_proxy, p, test_url, timeout): p
            for p in proxies_list
        }
        for future in as_completed(future_map):
            try:
                results.append(future.result())
            except Exception as e:
                p = future_map[future]
                results.append({
                    "host": p.get("host", ""),
                    "port": p.get("port", 0),
                    "status": "failed",
                    "ip_detected": None,
                    "latency_ms": 0,
                    "error": str(e),
                })
    return {"results": results}
@router.post("/api/proxy/clear")
def api_clear_proxies():
    """清空代理池列表并保存."""
    cfg = load_app_config_validated()
    pool = cfg.get("proxy_pool", {})
    pool["proxies"] = []
    cfg["proxy_pool"] = pool
    save_app_config_validated(cfg)
    try:
        from utils.proxy_manager import get_proxy_manager
        get_proxy_manager().reload()
    except Exception:
        pass
    return {"ok": True, "message": "代理列表已清空"}
@router.post("/api/proxy/webshare")
def api_fetch_webshare():
    """从 Webshare API 拉取代理列表并追加／覆盖到代理池."""
    cfg = load_app_config_validated()
    pool_cfg = cfg.get("proxy_pool", {})
    api_key = pool_cfg.get("webshare_api_key", "").strip()
    if not api_key:
        return {"ok": False, "message": "未配置 Webshare API Key，请先在代理池设置中填写"}
    fetched = []
    for mode in ("direct", "backbone"):
        page = 1
        while True:
            url = f"https://proxy.webshare.io/api/v2/proxy/list/?mode={mode}&page={page}&page_size=100"
            headers = {"Authorization": f"Token {api_key}"}
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code == 401:
                    return {"ok": False, "message": "API Key 无效或已过期（401 Unauthorized）"}
                if resp.status_code != 200:
                    break  
                data = resp.json()
                results = data.get("results", [])
                if not results:
                    break
                for p in results:
                    fetched.append({
                        "host": p.get("proxy_address", ""),
                        "port": int(p.get("port", 0)),
                        "username": p.get("username", ""),
                        "password": p.get("password", ""),
                    })
                if data.get("next"):
                    page += 1
                else:
                    break
            except Exception as e:
                return {"ok": False, "message": f"请求 Webshare 失败: {str(e)}"}
        if fetched:
            break  
    if not fetched:
        return {"ok": False, "message": "未获取到任何代理，请检查账户或套餐状态"}
    pool_cfg["proxies"] = [p for p in fetched if p["host"]]
    cfg["proxy_pool"] = pool_cfg
    save_app_config_validated(cfg)
    try:
        from utils.proxy_manager import get_proxy_manager
        get_proxy_manager().reload()
    except Exception:
        pass
    return {"ok": True, "count": len(fetched), "message": f"已成功获取并配置 {len(fetched)} 个代理"}
