import logging
import threading
import time
from typing import Any, Dict, List, Optional

from app.config_loader import load_config
from app.database import db_add_trade_order, db_get_trade_orders
from app.steam_confirm import SteamConfirmer
from utils.delay import jittered_sleep

logger = logging.getLogger("trade_delivery_worker")

_WORKER_THREAD: Optional[threading.Thread] = None
_STOP_EVENT = threading.Event()
_LAST_STATUS: Dict[str, Any] = {
    "running": False,
    "last_check_at": 0.0,
    "buff_status": "idle",
    "uu_status": "idle",
    "steam_status": "idle",
    "last_action": "无",
    "pending_confirms_count": 0,
    "errors": [],
}


def _cookies_str_to_dict(cookie_str: str) -> Dict[str, str]:
    out = {}
    for part in (cookie_str or "").split(";"):
        s = part.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out


def _get_steam_confirmer() -> Optional[SteamConfirmer]:
    try:
        from app.accounts import load_accounts

        accs = load_accounts()
        cfg = load_config().get("app", {})
        steam_confirm_cfg = cfg.get("steam_confirm", {})
        steam_guard_cfg = cfg.get("steam_guard", {})

        steam_acc = next((a for a in accs if a.get("platform") == "steam"), None)
        if not steam_acc:
            return None

        cookies = steam_acc.get("cookies", "")
        steam_id = steam_acc.get("steam_id", "")
        identity_secret = (
            steam_confirm_cfg.get("identity_secret")
            or steam_acc.get("identity_secret")
            or steam_guard_cfg.get("identity_secret")
            or ""
        )
        device_id = steam_confirm_cfg.get("device_id") or f"android:{steam_id}"

        if not identity_secret:
            return None

        return SteamConfirmer(
            identity_secret=identity_secret,
            device_id=device_id,
            steam_id=steam_id,
            cookies=cookies,
        )
    except Exception as e:
        logger.debug(f"构建 SteamConfirmer 失败: {e}")
        return None


def run_trade_confirmations() -> int:
    """自动扫描并批量签署所有挂起的 Steam 2FA 移动端交易确认"""
    confirmer = _get_steam_confirmer()
    if not confirmer:
        return 0

    ok, conf_list, err = confirmer.get_confirmations()
    if not ok or not conf_list:
        return 0

    _LAST_STATUS["pending_confirms_count"] = len(conf_list)
    logger.info(f"检测到 {len(conf_list)} 个待处理的 Steam 移动端交易确认，正在自动签署...")

    ok_accept, count, err_accept = confirmer.accept_all(conf_list)
    if ok_accept:
        logger.info(f"已成功自动签署 {count} 个 Steam 移动端确认！")
        for c in conf_list:
            db_add_trade_order({
                "platform": "steam",
                "trade_offer_id": str(c.get("creator_id") or c.get("id")),
                "action": "confirm_2fa",
                "item_name": "Steam 移动令牌签名",
                "status": "confirmed",
                "message": f"成功签署 confirmation id={c.get('id')}",
            })
        return count
    else:
        logger.warning(f"签署 Steam 移动端确认失败: {err_accept}")
        return 0


def process_buff_delivery(delivery_cfg: dict) -> None:
    """处理网易 BUFF 自动收发货与报价跟踪"""
    if not delivery_cfg.get("buff_auto_ship") and not delivery_cfg.get("buff_auto_accept"):
        return

    try:
        from app.services.buff_client import get_buff_client
        from app.accounts import load_accounts
        from app.receive_flow import fetch_buff_steam_trade, accept_steam_trade_offer

        buff_client = get_buff_client()
        if not buff_client:
            return

        accs = load_accounts()
        steam_acc = next((a for a in accs if a.get("platform") == "steam"), None)
        steam_cookies = _cookies_str_to_dict(steam_acc.get("cookies", "") if steam_acc else "")

        # 1. 自动处理待收货
        if delivery_cfg.get("buff_auto_accept") and steam_cookies:
            ok, pending_trades, _ = fetch_buff_steam_trade(buff_client)
            if ok and pending_trades:
                for trade in pending_trades:
                    offer_id = str(trade.get("tradeofferid"))
                    items = trade.get("items") or []
                    item_names = ", ".join([it.get("name", "") for it in items[:2]])
                    if not offer_id:
                        continue
                    res = accept_steam_trade_offer(offer_id, steam_cookies)
                    if res is True:
                        logger.info(f"[BUFF] 自动接受收货报价成功: offer_id={offer_id}, 饰品: {item_names}")
                        db_add_trade_order({
                            "platform": "buff",
                            "trade_offer_id": offer_id,
                            "action": "receive",
                            "item_name": item_names,
                            "status": "accepted",
                            "message": "已自动接受买入饰品报价",
                        })
                        jittered_sleep(2)

        # 2. 检查是否有需要发货的订单
        # 触发 2FA 移动端确认
        run_trade_confirmations()
        _LAST_STATUS["buff_status"] = "normal"
    except Exception as e:
        _LAST_STATUS["buff_status"] = f"error: {str(e)[:50]}"
        logger.debug(f"[BUFF发货轮询异常]: {e}")


def process_uu_delivery(delivery_cfg: dict) -> None:
    """处理悠悠有品自动收发货"""
    if not delivery_cfg.get("uu_auto_ship") and not delivery_cfg.get("uu_auto_accept"):
        return

    token = (delivery_cfg.get("uu_token") or "").strip()
    if not token:
        return

    try:
        import uuyoupinapi
        from app.accounts import load_accounts
        from app.receive_flow import accept_steam_trade_offer

        uu = uuyoupinapi.UUAccount(token)
        accs = load_accounts()
        steam_acc = next((a for a in accs if a.get("platform") == "steam"), None)
        steam_cookies = _cookies_str_to_dict(steam_acc.get("cookies", "") if steam_acc else "")

        deliver_list = uu.get_wait_deliver_list()
        if deliver_list:
            for item in deliver_list:
                offer_id = item.get("offer_id")
                item_name = item.get("item_name") or "悠悠有品饰品"
                price = float(item.get("price") or 0.0)
                if offer_id and steam_cookies:
                    res = accept_steam_trade_offer(str(offer_id), steam_cookies)
                    if res is True:
                        logger.info(f"[悠悠有品] 自动接受发货报价成功: offer_id={offer_id}, {item_name}")
                        db_add_trade_order({
                            "platform": "uu",
                            "trade_offer_id": str(offer_id),
                            "order_id": str(item.get("id") or ""),
                            "action": "ship",
                            "item_name": item_name,
                            "price": price,
                            "status": "accepted",
                            "message": "悠悠有品待发货报价已自动接受",
                        })
                        jittered_sleep(2)

        # 批量确认悠悠有品发货产生的 2FA 移动端凭证
        run_trade_confirmations()
        _LAST_STATUS["uu_status"] = "normal"
    except Exception as e:
        _LAST_STATUS["uu_status"] = f"error: {str(e)[:50]}"
        logger.debug(f"[悠悠有品发货轮询异常]: {e}")


def process_steam_gift_offers(delivery_cfg: dict) -> None:
    """自动接受无需支出库存的 Steam 礼物报价"""
    if not delivery_cfg.get("steam_auto_accept_gifts"):
        return

    try:
        from app.accounts import load_accounts
        from app.receive_flow import accept_steam_trade_offer
        import requests

        accs = load_accounts()
        steam_acc = next((a for a in accs if a.get("platform") == "steam"), None)
        if not steam_acc:
            return

        cookies = _cookies_str_to_dict(steam_acc.get("cookies", ""))
        api_key = steam_acc.get("api_key") or ""
        if not cookies.get("sessionid") or not cookies.get("steamLoginSecure"):
            return

        # 如果配置了 API Key，直接调用官方 GetTradeOffers 接口
        if api_key:
            url = "https://api.steampowered.com/IEconService/GetTradeOffers/v1/"
            params = {
                "key": api_key,
                "get_received_offers": 1,
                "active_only": 1,
            }
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 200:
                data = r.json().get("response", {})
                received_offers = data.get("trade_offers_received") or []
                for offer in received_offers:
                    offer_id = str(offer.get("tradeofferid"))
                    items_to_give = offer.get("items_to_give") or []
                    items_to_receive = offer.get("items_to_receive") or []
                    # 严格礼物判断：己方支出物品为空，且收件至少为 1
                    if len(items_to_give) == 0 and len(items_to_receive) > 0:
                        logger.info(f"[Steam礼物] 检测到免费礼物报价 {offer_id}，正在自动接受...")
                        res = accept_steam_trade_offer(offer_id, cookies)
                        if res is True:
                            db_add_trade_order({
                                "platform": "steam",
                                "trade_offer_id": offer_id,
                                "action": "gift",
                                "item_name": f"礼物报价 ({len(items_to_receive)} 件饰品)",
                                "status": "accepted",
                                "message": "已自动接受 Steam 礼物报价",
                            })
                            jittered_sleep(2)
        _LAST_STATUS["steam_status"] = "normal"
    except Exception as e:
        _LAST_STATUS["steam_status"] = f"error: {str(e)[:50]}"
        logger.debug(f"[Steam礼物报价轮询异常]: {e}")


def _worker_loop():
    logger.info("TradeDeliveryWorker 自动收发货与2FA确认守护线程已启动")
    _LAST_STATUS["running"] = True

    while not _STOP_EVENT.is_set():
        try:
            cfg = load_config().get("app", {})
            delivery_cfg = cfg.get("delivery", {})

            if delivery_cfg.get("enabled", True):
                _LAST_STATUS["last_check_at"] = time.time()
                # 1. BUFF 收发货
                process_buff_delivery(delivery_cfg)
                # 2. 悠悠有品收发货
                process_uu_delivery(delivery_cfg)
                # 3. Steam 礼物报价
                process_steam_gift_offers(delivery_cfg)
                # 4. 全局 2FA 移动端自动确认
                run_trade_confirmations()

            interval = max(5, int(delivery_cfg.get("poll_interval_seconds") or 15))
            _STOP_EVENT.wait(interval)
        except Exception as e:
            logger.error(f"TradeDeliveryWorker 主循环异常: {e}")
            _STOP_EVENT.wait(10)

    _LAST_STATUS["running"] = False
    logger.info("TradeDeliveryWorker 已平稳停止")


def start_trade_delivery_worker():
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(target=_worker_loop, name="TradeDeliveryWorker", daemon=True)
    _WORKER_THREAD.start()


def stop_trade_delivery_worker():
    _STOP_EVENT.set()
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        _WORKER_THREAD.join(timeout=5)


def get_delivery_runtime_status() -> dict:
    status = dict(_LAST_STATUS)
    status["history"] = db_get_trade_orders(limit=10)
    return status
