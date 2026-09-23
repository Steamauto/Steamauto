import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, List, Optional
from app.database import (
    db_append_purchase,
    db_append_sale,
    db_clear_transactions,
    db_delete_purchase,
    db_delete_purchase_by_id,
    db_delete_sale,
    db_get_purchases,
    db_get_sales,
    db_replace_transactions,
    db_update_purchase,
    db_update_purchase_by_id,
    db_update_sale,
)
_LOG_MAXLEN = 500
class State:
    _lock: threading.Lock
    _confirm: threading.Condition
    _status: str
    _step: str
    _log: Deque[dict]
    _log_seq: int
    _pending_payment: Optional[dict]
    _user_confirmed: Optional[bool]
    _stop_requested: bool
    _plan: List[Any]
    _inventory: List[Any]
    _buff_auth_expired: bool
    _buff_verification_required: bool
    _buff_verification_reason: str
    _progress_total: int
    _progress_done: int
    _progress_item: str
    _next_progress_item: str
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._confirm = threading.Condition()
        self._status = "idle"
        self._step = ""
        self._log = deque(maxlen=_LOG_MAXLEN)
        self._log_seq = 0
        self._pending_payment = None
        self._user_confirmed = None
        self._stop_requested = False
        self._plan = []
        self._inventory = []
        self._buff_auth_expired = False
        self._buff_verification_required = False
        self._buff_verification_reason = ""
        self._progress_total = 0
        self._progress_done = 0
        self._progress_item = ""
        self._next_progress_item = ""

    def append_purchase(self, p: dict) -> None:
        db_append_purchase(p)
    def get_purchases(self) -> list:
        return db_get_purchases()
    def append_sale(self, s: dict) -> None:
        db_append_sale(s)
    def get_sales(self) -> list:
        return db_get_sales()
    def clear_transactions(self) -> None:
        db_clear_transactions()
    def reload_transactions(self) -> None:
        pass  # Replaced by direct SQLite queries
    def replace_transactions(self, purchases: List[dict], sales: List[dict]) -> None:
        db_replace_transactions(purchases, sales)
    def delete_purchase(self, idx: int) -> bool:
        return db_delete_purchase(idx)
    def delete_sale(self, idx: int) -> bool:
        return db_delete_sale(idx)
    def update_purchase(self, idx: int, data: dict) -> bool:
        return db_update_purchase(idx, data)
    def update_purchase_by_id(self, db_id: int, data: dict) -> bool:
        return db_update_purchase_by_id(db_id, data)
    def delete_purchase_by_id(self, db_id: int) -> bool:
        return db_delete_purchase_by_id(db_id)
    def update_sale(self, idx: int, data: dict) -> bool:
        return db_update_sale(idx, data)
    def set_status(self, s: str, step: str = "", progress_total: int = 0, progress_done: int = 0, progress_item: str = "", next_progress_item: str = "") -> None:
        with self._lock:
            self._status = s
            self._step = step
            if progress_total >= 0:
                self._progress_total = progress_total
            if progress_done >= 0:
                self._progress_done = progress_done
            if progress_item is not None:
                self._progress_item = progress_item or ""
            self._next_progress_item = next_progress_item or ""
    def get_status(self) -> dict:
        with self._lock:
            pct = (100 * self._progress_done / self._progress_total) if self._progress_total else 0
            return {
                "status": self._status,
                "step": self._step,
                "buff_auth_expired": self._buff_auth_expired,
                "buff_verification_required": self._buff_verification_required,
                "buff_verification_reason": self._buff_verification_reason,
                "progress_total": self._progress_total,
                "progress_done": self._progress_done,
                "progress_pct": round(pct, 1),
                "progress_item": self._progress_item,
                "next_progress_item": self._next_progress_item,
            }
    def is_steam_background_allowed(self) -> bool:
        # pipeline在跑但处于等待类步骤时，后台任务可以插队跑
        _idle_steps = {"", "TIME_LIMIT_WAIT", "NETWORK_OFFLINE", "STEAM_COOLDOWN", "CHECKOUT_PENDING"}
        with self._lock:
            return self._status != "running" or self._step in _idle_steps
    def set_buff_auth_expired(self, value: bool) -> None:
        with self._lock:
            self._buff_auth_expired = value
            if value:
                self._buff_verification_required = False
                self._buff_verification_reason = ""
    def set_buff_verification_required(self, value: bool, reason: str = "") -> None:
        with self._lock:
            self._buff_verification_required = value
            self._buff_verification_reason = reason if value else ""
            if value:
                self._buff_auth_expired = False
    def log(self, msg: str, level: str = "info", category: str = "", flow_id: str = "") -> None:
        with self._lock:
            self._log_seq += 1
            self._log.append({
                "id": self._log_seq,
                "t": time.time(),
                "level": level,
                "msg": msg,
                "category": category or "",
                "flow_id": flow_id or "",
            })
    def get_log(self, since_idx: int = 0) -> list:
        with self._lock:
            out = list(self._log)
            if since_idx > 0:
                out = [e for e in out if e.get("id", 0) > since_idx]
            return out
    def set_pending_payment(self, p: Optional[dict]) -> None:
        with self._lock:
            self._pending_payment = p
    def get_pending_payment(self) -> Optional[dict]:
        with self._lock:
            return self._pending_payment
    def wait_payment_confirm(self, timeout_seconds: Optional[float] = None) -> bool:
        with self._confirm:
            self._user_confirmed = None
            deadline = (time.time() + timeout_seconds) if timeout_seconds is not None else None
            while True:
                if self._user_confirmed is not None:
                    return self._user_confirmed is True
                if self._stop_requested:
                    return False
                if deadline is not None and time.time() >= deadline:
                    return False
                wait_time = 1.0
                if deadline is not None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return False
                    wait_time = min(1.0, remaining)
                self._confirm.wait(timeout=wait_time)
    def confirm_payment(self, ok: bool) -> None:
        with self._confirm:
            self._user_confirmed = ok
            self._confirm.notify_all()
    def request_stop(self) -> None:
        with self._lock:
            self._stop_requested = True
        with self._confirm:
            self._confirm.notify_all()
    def clear_stop(self) -> None:
        with self._lock:
            self._stop_requested = False
    def is_stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested
    def set_plan(self, plan: list) -> None:
        with self._lock:
            self._plan = list(plan)
    def get_plan(self) -> list:
        with self._lock:
            return list(self._plan)
    def set_inventory(self, items: list) -> None:
        with self._lock:
            self._inventory = list(items)
    def get_inventory(self) -> list:
        with self._lock:
            return list(self._inventory)
    def clear_log(self) -> None:
        with self._lock:
            self._log.clear()
    def replace_log(self, lines: list) -> None:
        with self._lock:
            self._log.clear()
            for e in lines:
                if isinstance(e, dict) and ("msg" in e or "level" in e):
                    self._log.append({
                        "id": e.get("id", 0),
                        "t": e.get("t", time.time()),
                        "level": e.get("level", "info"),
                        "msg": e.get("msg", ""),
                        "category": e.get("category", ""),
                        "flow_id": e.get("flow_id", ""),
                    })
            if self._log:
                self._log_seq = max(e.get("id", 0) for e in self._log)
_instance: Optional[State] = None
_instance_lock = threading.Lock()
def get_state() -> State:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = State()
        return _instance
def set_state(state: Optional[State]) -> None:
    global _instance
    with _instance_lock:
        _instance = state
def set_status(s: str, step: str = "", progress_total: int = 0, progress_done: int = 0, progress_item: str = "", next_progress_item: str = "") -> None:
    get_state().set_status(s, step=step, progress_total=progress_total, progress_done=progress_done, progress_item=progress_item, next_progress_item=next_progress_item)
def get_status() -> dict:
    return get_state().get_status()
def is_steam_background_allowed() -> bool:
    return get_state().is_steam_background_allowed()
def set_buff_auth_expired(value: bool) -> None:
    get_state().set_buff_auth_expired(value)
def set_buff_verification_required(value: bool, reason: str = "") -> None:
    get_state().set_buff_verification_required(value, reason=reason)
def log(msg: str, level: str = "info", category: str = "", flow_id: str = "") -> None:
    get_state().log(msg, level, category, flow_id)
def get_log(since_idx: int = 0) -> list:
    return get_state().get_log(since_idx)
def set_pending_payment(p: Optional[dict]) -> None:
    get_state().set_pending_payment(p)
def get_pending_payment() -> Optional[dict]:
    return get_state().get_pending_payment()
def wait_payment_confirm(timeout_seconds: Optional[float] = None) -> bool:
    return get_state().wait_payment_confirm(timeout_seconds=timeout_seconds)
def confirm_payment(ok: bool) -> None:
    get_state().confirm_payment(ok)
def request_stop() -> None:
    get_state().request_stop()
def clear_stop() -> None:
    get_state().clear_stop()
def is_stop_requested() -> bool:
    return get_state().is_stop_requested()
def set_plan(plan: list) -> None:
    get_state().set_plan(plan)
def get_plan() -> list:
    return get_state().get_plan()
def append_purchase(p: dict) -> None:
    get_state().append_purchase(p)
def get_purchases() -> list:
    return get_state().get_purchases()
def append_sale(s: dict) -> None:
    get_state().append_sale(s)
def get_sales() -> list:
    return get_state().get_sales()
def reload_transactions() -> None:
    get_state().reload_transactions()
def clear_transactions() -> None:
    get_state().clear_transactions()
def replace_transactions(purchases: list, sales: list) -> None:
    get_state().replace_transactions(purchases, sales)
def delete_purchase(idx: int) -> bool:
    return get_state().delete_purchase(idx)
def delete_purchase_by_id(db_id: int) -> bool:
    return get_state().delete_purchase_by_id(db_id)
def delete_sale(idx: int) -> bool:
    return get_state().delete_sale(idx)
def delete_sale_by_id(db_id: int) -> bool:
    return get_state().delete_sale_by_id(db_id)
def update_purchase(idx: int, data: dict) -> bool:
    return get_state().update_purchase(idx, data)
def update_purchase_by_id(db_id: int, data: dict) -> bool:
    return get_state().update_purchase_by_id(db_id, data)
def update_sale(idx: int, data: dict) -> bool:
    return get_state().update_sale(idx, data)
def set_inventory(items: list) -> None:
    get_state().set_inventory(items)
def get_inventory() -> list:
    return get_state().get_inventory()
def clear_log() -> None:
    get_state().clear_log()
def replace_log(lines: list) -> None:
    get_state().replace_log(lines)
