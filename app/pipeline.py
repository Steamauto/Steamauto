from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, tzinfo
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config_loader import get_buff_credentials, load_app_config_validated
from app.config_schema import DEFAULTS, _validate_ranges, merge, validate_and_fill
from app.pipeline_context import PipelineContext
from app.pipeline_steps import (
    TARGET_REACHED,
    SKIP_NO_FAILED,
    SKIP_VERIFICATION_FAILED,
    PurchaseCoolingDown,
    PurchaseOrderCreatedPending,
    PurchaseWriteResultUnknown,
    TIME_WINDOW_CLOSED,
    filter_iflow_rows,
    lock_and_confirm_payment,
    pick_stable_item,
)
from app.services.iflow_client import fetch_iflow_rows
from app.services.analysis_client import StabilityAnalyzer
from app.services.buff_client import create_buff_client_from_config
from app.services.buff_checkout_guard import (
    BuffCheckoutGuardMismatch,
    acknowledge_checkout,
    buff_activity_guard,
    get_unresolved_checkout,
    update_checkout,
)
from app.services.steam_client import SteamClient
from app.state import get_state, append_sale
from app.strategy_engine import apply_strategy_to_config
from buff import (
    BuffAuthExpired,
    BuffRateLimited,
    BuffRequestBlocked,
    BuffVerificationRequired,
)
from steamdt.models import SteamDTQueryParams
from utils.delay import jittered_sleep
from utils.money import USD_TO_CNY_DEFAULT, list_price_display_to_cents
from utils.network_check import get_network_checker
from utils.proxy_manager import get_proxy_manager
from utils.time import (
    now_in_configured_timezone,
    resolve_configured_timezone,
)
from app.sell_pipeline import run_sell_phase_on_inventory_update

DEFAULT_RETRY_INTERVAL_SECONDS = 300
DEFAULT_START_TIME_HOUR = 8
DEFAULT_END_TIME_HOUR = 22
FAILED_GOODS_TTL_SECONDS = 1800


def _resolve_schedule_timezone(system_config: dict) -> tuple[tzinfo | None, str]:
    return resolve_configured_timezone(system_config)


def _now_in_schedule_timezone(schedule_timezone: tzinfo | None) -> datetime:
    return now_in_configured_timezone(schedule_timezone)


def _is_in_time_window(
    start_hour: int,
    end_hour: int,
    current_time: datetime | None = None,
) -> bool:
    hour = (
        current_time
        if current_time is not None
        else datetime.now().astimezone()
    ).hour
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def _fetch_and_filter_deals(ctx: PipelineContext, cfg: dict, retry_interval: int):
    ctx.set_status("running", "FETCHING_DEALS", progress_total=0, progress_done=0, progress_item="")
    ctx.log("正在拉取 SteamDT 数据…", "info", category="steamdt")
    try:
        rows = fetch_iflow_rows(cfg)
    except Exception as e:
        ctx.log(f"SteamDT 拉取失败: {type(e).__name__}: {e}，{retry_interval}秒后重试", "warn", category="steamdt")
        return None, True  # (rows, fetch_failed)
    if not rows:
        ctx.log(f"SteamDT 未返回任何数据，{retry_interval}秒后重试", "warn", category="steamdt")
        return None, False
    ctx.log(f"SteamDT 返回 {len(rows)} 条原始数据", "info", category="steamdt")
    if ctx.verbose:
        iflow_cfg = cfg.get("steamdt") or cfg.get("iflow", {})
        q = SteamDTQueryParams(
            page=int(iflow_cfg.get("page_num", 1)),
            page_size=int(iflow_cfg.get("page_size", 200)),
            min_sell_price=str(iflow_cfg.get("min_price", 2)),
            max_sell_price=int(iflow_cfg.get("max_price", 5000)),
            min_transaction_count=str(iflow_cfg.get("min_volume", 200)),
        )
        ctx.debug(f"[详细流程] SteamDT 请求参数: page={q.page} pageSize={q.page_size} minPrice={q.min_sell_price} maxPrice={q.max_sell_price} minTx={q.min_transaction_count}")
        ctx.debug(f"[详细流程] 原始数据共 {len(rows)} 条，SteamDT 返回顺序（前20条）:")
        for i, r in enumerate(rows[:20]):
            nm = (getattr(r, "name", None) or "")[:42]
            ctx.debug(f"  {i+1:2}. {nm} | sell={getattr(r, 'sell_ratio', '')} buy={getattr(r, 'buy_ratio', '')}")
    filtered = filter_iflow_rows(rows, cfg, log_fn=lambda msg, lvl="info": ctx.log(msg, lvl))
    ctx.log(f"筛选后剩余 {len(filtered)} 条", "info")
    if ctx.verbose and filtered:
        ctx.debug(f"[详细流程] 筛选后共 {len(filtered)} 条，顺序不变（前20条）:")
        for i, item in enumerate(filtered[:20]):
            nm = (item.get("name") or "")[:42]
            ctx.debug(f"  {i+1:2}. {nm} | 比例={item.get('ratio', '')} 最低价={item.get('min_price', '')}")
    return filtered, False


def _process_deals_for_target(
    ctx: PipelineContext,
    filtered: list,
    cfg: dict,
    target: float,
    current_acc: float,
    total_bought: int,
    steam_client,
    analyzer,
    buyer,
    failed_goods_ids: set,
    skipped_this_round: set,
    stability_failed_this_round: set,
    is_time_allowed=None,
):
    acc = current_acc
    bought = total_bought
    n_filtered = len(filtered)

    def buff_log(msg: str, level: str = "info") -> None:
        ctx.log(msg, level, category="buff")

    def time_window_is_open() -> bool:
        if is_time_allowed is None:
            return True
        try:
            return bool(is_time_allowed())
        except Exception:
            return False

    while acc < target:
        if ctx.is_stop_requested():
            ctx.log("用户请求停止", "warn", category="buff")
            ctx.set_status("stopped", "已停止")
            return acc, bought, True
        if not time_window_is_open():
            ctx.log("购买时间窗已关闭，暂停候选分析与下单", "info", category="pipeline")
            return acc, bought, TIME_WINDOW_CLOSED

        ctx.set_status("running", "CHECKING_STABILITY", progress_total=n_filtered, progress_done=0, progress_item="")
        chosen, new_stability_failed = pick_stable_item(
            filtered, cfg, steam_client, analyzer, ctx.is_stop_requested,
            log_fn=ctx.log,
            exclude_goods_ids=failed_goods_ids | skipped_this_round | stability_failed_this_round,
            buff_client=buyer,
        )
        stability_failed_this_round |= new_stability_failed

        if ctx.is_stop_requested():
            ctx.set_status("stopped", "已停止")
            return acc, bought, True
        if chosen is None:
            break
        if acc >= target:
            break
        if not time_window_is_open():
            ctx.log("候选分析完成时购买时间窗已关闭，本件不下单", "info", category="pipeline")
            return acc, bought, TIME_WINDOW_CLOSED

        ctx.log(f"购买本件: {chosen['name']} goods_id={chosen['goods_id']} 参考价={chosen.get('min_price')}", "info", category="buff")

        def on_entering_payment() -> None:
            ctx.set_status("running", "CHECKOUT_PENDING", progress_item=chosen.get("name", ""))

        try:
            checkout_kwargs = {
                "log_fn": buff_log,
                "on_entering_payment": on_entering_payment,
            }
            if is_time_allowed is not None:
                checkout_kwargs["is_time_allowed"] = time_window_is_open
            paid = lock_and_confirm_payment(
                buyer, chosen, cfg, target, acc,
                ctx.state.set_pending_payment,
                ctx.state.wait_payment_confirm,
                ctx.state.confirm_payment,
                ctx.state.is_stop_requested,
                ctx.state.append_purchase,
                **checkout_kwargs,
            )
        except Exception as exc:
            committed_amount = float(
                getattr(exc, "committed_amount", 0.0) or 0.0
            )
            if committed_amount <= 0:
                raise
            committed_orders = max(
                1,
                int(getattr(exc, "committed_orders", 1) or 1),
            )
            acc = round(acc + committed_amount, 2)
            bought += committed_orders
            ctx.log(
                f"成交记录已落库 本笔={committed_amount:.2f} "
                f"累计={acc:.2f}/{target}；后续 BUFF 写请求已停止",
                "error",
                category="buff",
            )
            if isinstance(exc, PurchaseWriteResultUnknown):
                ctx.set_status("error", "BUFF_POST_COMMIT_WRITE_UNKNOWN")
            elif isinstance(exc, PurchaseOrderCreatedPending):
                ctx.set_status("error", "BUFF_ORDER_CREATED_PENDING")
            elif isinstance(exc, BuffRateLimited):
                ctx.set_status("error", "BUFF_RATE_LIMITED")
            elif isinstance(exc, BuffVerificationRequired):
                ctx.state.set_buff_verification_required(True, str(exc))
                ctx.set_status("error", "BUFF_VERIFICATION_REQUIRED")
            elif isinstance(exc, BuffAuthExpired):
                ctx.state.set_buff_auth_expired(True)
                ctx.set_status("error", "BUFF_AUTH_EXPIRED")
            elif isinstance(exc, BuffRequestBlocked):
                ctx.set_status("error", "BUFF_REQUEST_BLOCKED")
            else:
                ctx.set_status("error", "BUFF_POST_COMMIT_HALT")
            return acc, bought, True

        if ctx.is_stop_requested():
            ctx.set_status("stopped", "已停止")
            return acc, bought, True
        if paid is TIME_WINDOW_CLOSED:
            ctx.log("发送购买请求前时间窗已关闭，本件未下单", "info", category="pipeline")
            return acc, bought, TIME_WINDOW_CLOSED

        if paid is TARGET_REACHED:
            ctx.log("累计已达/超过目标，结束购买", "info", category="buff")
            acc = target
            break
        if paid is SKIP_NO_FAILED:
            gid = chosen.get("goods_id")
            if gid is not None:
                skipped_this_round.add(gid)
            ctx.log("安全采购上限不足，跳过本件", "warn", category="buff")
            continue
        if paid is SKIP_VERIFICATION_FAILED:
            gid = chosen.get("goods_id")
            if gid is not None:
                failed_goods_ids.add(gid)
            ctx.log("二次验证未通过，跳过本件", "warn", category="buff")
            continue
        if paid is None:
            gid = chosen.get("goods_id")
            if gid is not None:
                failed_goods_ids.add(gid)
            ctx.log("锁单/确认未成功，跳过本件", "warn", category="buff")
            continue

        acc += paid
        acc = round(acc, 2)
        bought += 1
        ctx.set_status("running", "CHECKOUT_PENDING", progress_done=bought, progress_item=chosen.get("name", ""))
        ctx.log(f"已确认付款 本笔={paid:.2f} 累计={acc:.2f}/{target}", "info", category="buff")
        if acc >= target:
            break
        ctx.debug("下一件将重新按 SteamDT 顺序试稳定性")
        jittered_sleep(1.0, 0.0)

    return acc, bought, False


def _run_pipeline(config: dict) -> None:
    state = get_state()
    state.clear_stop()
    supplied_config = config if isinstance(config, dict) else {}
    cfg = apply_strategy_to_config(
        _validate_ranges(validate_and_fill(merge(DEFAULTS, supplied_config))),
        "buy",
    )
    pipeline_cfg = cfg.get("pipeline", {})
    verbose = bool(pipeline_cfg.get("verbose_debug", False))
    ctx = PipelineContext(state, str(uuid.uuid4())[:8], verbose=verbose)

    target = float(pipeline_cfg.get("target_balance", 100))
    exclude = pipeline_cfg.get("exclude_keywords", [])
    cred_buff = get_buff_credentials()
    cookies_buff = cred_buff.get("cookies", "")
    if not cookies_buff:
        ctx.log("未配置 Buff cookies", "error", category="config")
        ctx.set_status("error", "CONFIG_ERROR")
        return

    ctx.log("买入阶段启动", "info")
    max_discount = pipeline_cfg.get("max_discount")
    sort_by = (cfg.get("steamdt") or cfg.get("iflow") or {}).get("sort_by", "sell")
    sort_labels = {"sell": "最优寄售", "buy": "最优求购"}
    sort_desc = sort_labels.get(sort_by, sort_by)
    retry_interval = int(pipeline_cfg.get("retry_interval_seconds", DEFAULT_RETRY_INTERVAL_SECONDS))
    ctx.log(
        f"配置: 目标余额={target}, 排除关键词={exclude}, 最高折扣={max_discount}, "
        f"排序={sort_desc}({sort_by}), 无符合时{retry_interval}秒后重试",
        "info",
    )
    if ctx.verbose:
        ctx.debug("详细调试已开启")

    proxy_manager = get_proxy_manager()
    if proxy_manager.is_proxy_enabled():
        ctx.set_status("running", "PROXY_WARMUP")
        ctx.log("代理池已启用，预热将在后台启动，pipeline 同步开始运行...", "info")
        proxy_warmup_thread = threading.Thread(
            target=proxy_manager.warmup, daemon=True, name="proxy-warmup"
        )
        proxy_warmup_thread.start()
    else:
        ctx.debug("代理池未启用或策略为关闭，跳过预热")

    acc = 0.0
    total_bought = 0
    time_limit_enabled = bool(pipeline_cfg.get("start_time_limit_enabled", False))
    start_time_hour = max(0, min(23, int(pipeline_cfg.get("start_time_hour", DEFAULT_START_TIME_HOUR))))
    end_time_hour = max(0, min(23, int(pipeline_cfg.get("end_time_hour", DEFAULT_END_TIME_HOUR))))
    schedule_timezone, schedule_timezone_label = _resolve_schedule_timezone(
        cfg.get("system") or {}
    )

    def time_window_is_open() -> bool:
        if not time_limit_enabled:
            return True
        return _is_in_time_window(
            start_time_hour,
            end_time_hour,
            _now_in_schedule_timezone(schedule_timezone),
        )

    if time_limit_enabled:
        ctx.log(
            "启动时间限制使用时区 "
            f"{schedule_timezone_label}，允许 {start_time_hour}:00–"
            f"{end_time_hour}:00",
            "info",
            category="pipeline",
        )

    steam_client = SteamClient()
    analyzer = StabilityAnalyzer(usd_to_cny=USD_TO_CNY_DEFAULT)
    buyer = create_buff_client_from_config(cred_buff, cfg)
    failed_goods_ids_ttl: dict = {}

    while True:
        if ctx.is_stop_requested():
            ctx.log("用户请求停止", "warn")
            ctx.set_status("stopped", "已停止")
            return

        schedule_now = _now_in_schedule_timezone(schedule_timezone)
        if time_limit_enabled and not _is_in_time_window(
            start_time_hour,
            end_time_hour,
            schedule_now,
        ):
            current_clock = schedule_now.strftime("%H:%M")
            ctx.set_status(
                "running",
                "TIME_LIMIT_WAIT",
                progress_item=(
                    f"{current_clock} {schedule_timezone_label}; "
                    f"Allowed {start_time_hour}:00-{end_time_hour}:00"
                ),
            )
            ctx.log(
                f"启动时间限制: 当前 {current_clock} "
                f"({schedule_timezone_label}) 不在 "
                f"{start_time_hour}:00–{end_time_hour}:00 内，60 秒后重试",
                "info",
            )
            if ctx.wait_retry(60):
                return
            continue

        try:
            filtered, fetch_failed = _fetch_and_filter_deals(ctx, cfg, retry_interval)
            net = get_network_checker()
            if fetch_failed:
                offline = net.report_failure(
                    log_fn=lambda msg, lvl: ctx.log(msg, lvl, category="network")
                )
                if offline:
                    ctx.set_status("running", "NETWORK_OFFLINE")
                    recovered = net.wait_until_online(
                        is_stop_fn=ctx.is_stop_requested,
                        log_fn=lambda msg, lvl: ctx.log(msg, lvl, category="network"),
                    )
                    if not recovered:
                        ctx.set_status("stopped", "已停止")
                        return
                    continue
            else:
                net.report_success()

            if ctx.is_stop_requested():
                ctx.set_status("stopped", "已停止")
                return
            if not filtered:
                if ctx.wait_retry(retry_interval):
                    return
                continue

            ctx.log("支付方式与 Buff 客户端已就绪", "info", category="buff")
            now_ts = time.time()
            expired_ids = [gid for gid, exp in failed_goods_ids_ttl.items() if now_ts >= exp]
            for gid in expired_ids:
                del failed_goods_ids_ttl[gid]
            if expired_ids:
                ctx.log(f"Unblocked {len(expired_ids)} expired failed goods_id", "info", category="pipeline")
            failed_goods_ids = set(failed_goods_ids_ttl.keys())

            acc, total_bought, stopped = _process_deals_for_target(
                ctx, filtered, cfg, target, acc, total_bought,
                steam_client, analyzer, buyer,
                failed_goods_ids,
                set(),
                set(),
                is_time_allowed=time_window_is_open,
            )
            if stopped is TIME_WINDOW_CLOSED:
                continue
            if stopped:
                return

            expire_ts = time.time() + FAILED_GOODS_TTL_SECONDS
            for gid in failed_goods_ids:
                if gid not in failed_goods_ids_ttl:
                    failed_goods_ids_ttl[gid] = expire_ts

        except PurchaseWriteResultUnknown as e:
            detail = str(e) or "Buff 写请求结果未知，必须先对账"
            refs = " ".join(
                part for part in (
                    f"order_id={e.order_id}" if e.order_id else "",
                    f"batch_id={e.batch_id}" if e.batch_id else "",
                ) if part
            )
            if refs:
                detail = f"{detail} ({refs})"
            update_checkout(
                stage="write_result_unknown",
                reason=detail,
                order_id=e.order_id,
                batch_id=e.batch_id,
                last_error_type=type(e).__name__,
            )
            ctx.log(f"{detail}；已停止全部后续购买请求", "error", category="buff")
            ctx.set_status("error", "BUFF_WRITE_RESULT_UNKNOWN")
            return
        except PurchaseOrderCreatedPending as e:
            detail = str(e) or "Buff 订单已创建但尚未完成"
            refs = " ".join(
                part for part in (
                    f"order_id={e.order_id}" if e.order_id else "",
                    f"batch_id={e.batch_id}" if e.batch_id else "",
                ) if part
            )
            if refs:
                detail = f"{detail} ({refs})"
            update_checkout(
                stage="order_created_pending",
                reason=detail,
                order_id=e.order_id,
                batch_id=e.batch_id,
                last_error_type=type(e).__name__,
            )
            ctx.log(f"{detail}；已停止全部后续购买请求", "error", category="buff")
            ctx.set_status("error", "BUFF_ORDER_CREATED_PENDING")
            return
        except PurchaseCoolingDown as e:
            update_checkout(
                stage="write_result_unknown",
                reason=str(e) or "Buff 处于冷却状态",
                last_error_type=type(e).__name__,
            )
            ctx.log(str(e) or "Buff 处于冷却状态", "error", category="buff")
            ctx.set_status("error", "BUFF_COOLING_DOWN")
            return
        except BuffAuthExpired:
            ctx.state.set_buff_auth_expired(True)
            ctx.log("Buff 登录已过期，请在界面重新登录", "error", category="buff")
            ctx.set_status("error", "BUFF_AUTH_EXPIRED")
            return
        except BuffRateLimited as e:
            ctx.log(
                f"Buff 请求已被限流，账号请求熔断已开启: {e}",
                "error",
                category="buff",
            )
            ctx.set_status("error", "BUFF_RATE_LIMITED")
            return
        except BuffVerificationRequired as e:
            reason = str(e) or "Buff 需要刷新页面或完成人机验证"
            ctx.state.set_buff_verification_required(True, reason)
            ctx.log(f"Buff 需要刷新页面状态或完成人机验证: {reason}", "error", category="buff")
            ctx.set_status("error", "BUFF_VERIFICATION_REQUIRED")
            return
        except BuffRequestBlocked as e:
            # Future request-policy block types must fail closed instead of
            # falling through to another candidate or write request.
            reason = str(e) or "Buff 请求策略已阻止继续访问"
            ctx.log(reason, "error", category="buff")
            ctx.set_status("error", "BUFF_REQUEST_BLOCKED")
            return

        if acc >= target:
            break
        ctx.debug(f"本轮无满足条件饰品，等待 {retry_interval}s 重新拉取")
        if ctx.wait_retry(retry_interval):
            return

    ctx.set_status("running", "STEAM_COOLDOWN")
    ctx.log("买入阶段完成", "info")
    ctx.log(f"本次共成功购买 {total_bought} 单。Steam 交易冷却。", "info")
    ctx.set_status("idle", "")


_pipeline_thread = None
_pipeline_start_lock = threading.RLock()
_pipeline_maintenance_reason = ""
_shutdown_pending = False


class PipelineMaintenanceBlocked(RuntimeError):
    pass


def is_shutdown_pending() -> bool:
    return bool(_shutdown_pending)


def mark_shutdown_pending() -> None:
    global _shutdown_pending
    _shutdown_pending = True


@contextmanager
def exclusive_pipeline_maintenance(reason: str):
    """Exclude pipeline starts, checkout writes and credential changes.

    Lock order is auth -> BUFF activity -> pipeline lifecycle.  Authentication
    code may call ``start_pipeline`` while already owning the re-entrant auth
    lock, so reversing this order would introduce a deadlock.
    """

    from app.services.buff_auth import (
        buff_credential_replacement_block_reason,
        get_buff_auth_lock,
    )

    global _pipeline_maintenance_reason
    auth_lock = get_buff_auth_lock()
    if not auth_lock.acquire(blocking=False):
        raise PipelineMaintenanceBlocked(
            "BUFF 登录、验证或请求正在进行，暂不能维护配置/数据"
        )
    try:
        with buff_activity_guard():
            with _pipeline_start_lock:
                if _shutdown_pending:
                    raise PipelineMaintenanceBlocked("应用正在重置并等待退出")
                if _pipeline_thread is not None and _pipeline_thread.is_alive():
                    raise PipelineMaintenanceBlocked("买入流水线仍在运行")
                block_reason = buff_credential_replacement_block_reason()
                if block_reason:
                    raise PipelineMaintenanceBlocked(block_reason)
                _pipeline_maintenance_reason = str(reason or "maintenance")
                try:
                    yield
                finally:
                    _pipeline_maintenance_reason = ""
    finally:
        auth_lock.release()


def is_pipeline_running() -> bool:
    with _pipeline_start_lock:
        return _pipeline_thread is not None and _pipeline_thread.is_alive()


def _run_pipeline_guarded(config: dict) -> None:
    global _pipeline_thread
    try:
        _run_pipeline(config)
    except Exception as exc:
        state = get_state()
        state.set_pending_payment(None)
        if get_unresolved_checkout() is not None:
            update_checkout(
                stage="pipeline_unexpected_error",
                reason=f"{type(exc).__name__}: {exc}",
                last_error_type=type(exc).__name__,
            )
        state.log(
            f"买入流水线发生未处理异常: {type(exc).__name__}: {exc}",
            "error",
            category="pipeline",
        )
        state.set_status("error", "PIPELINE_UNEXPECTED_ERROR")
    finally:
        state = get_state()
        get_status = getattr(state, "get_status", None)
        status = get_status() if callable(get_status) else {}
        if status.get("status") == "running" and status.get("step") == "STARTING":
            state.set_status("idle", "")
        with _pipeline_start_lock:
            _pipeline_thread = None


def get_pipeline_start_blocker() -> dict:
    if _shutdown_pending:
        return {
            "code": "APP_SHUTDOWN_PENDING",
            "message": "应用正在重置数据并等待退出，不能启动流水线",
        }
    if _pipeline_maintenance_reason:
        return {
            "code": "PIPELINE_MAINTENANCE_ACTIVE",
            "message": "配置或数据维护正在进行，暂不能启动流水线",
        }
    from app.services.buff_auth import get_buff_auth_lock

    auth_lock = get_buff_auth_lock()
    auth_available = auth_lock.acquire(blocking=False)
    if not auth_available:
        return {
            "code": "BUFF_AUTH_BUSY",
            "message": "BUFF 登录、验证或凭证更新正在进行，请完成后再启动流水线",
        }
    auth_lock.release()
    guard = get_unresolved_checkout()
    if guard is not None:
        public_checkout = {
            key: guard.get(key)
            for key in (
                "intent_id",
                "kind",
                "stage",
                "goods_id",
                "sell_order_id",
                "order_id",
                "batch_id",
                "quantity",
                "price",
                "completed_order_ids",
                "reason",
                "created_at",
                "updated_at",
            )
            if guard.get(key) not in (None, "", [])
        }
        return {
            "code": "BUFF_RECONCILIATION_REQUIRED",
            "message": "存在未对账的 BUFF checkout，请先检查 BUFF 订单记录",
            "checkout": public_checkout,
        }
    status = get_state().get_status()
    if status.get("buff_auth_expired"):
        return {
            "code": "BUFF_AUTH_EXPIRED",
            "message": "BUFF 登录已失效，请先完成在线验证",
        }
    if status.get("buff_verification_required"):
        return {
            "code": "BUFF_VERIFICATION_REQUIRED",
            "message": status.get("buff_verification_reason")
            or "BUFF 需要先完成安全验证",
        }
    return {}


def start_pipeline(
    config: dict,
    *,
    acknowledge_buff_reconciliation: bool = False,
    buff_reconciliation_intent_id: str = "",
) -> bool:
    global _pipeline_thread
    from app.services.buff_auth import get_buff_auth_lock

    auth_lock = get_buff_auth_lock()
    if not auth_lock.acquire(blocking=False):
        return False
    try:
        with buff_activity_guard():
            with _pipeline_start_lock:
                if _shutdown_pending or _pipeline_maintenance_reason:
                    return False
                if _pipeline_thread is not None and _pipeline_thread.is_alive():
                    return False
                guard = get_unresolved_checkout()
                if guard is not None:
                    if not acknowledge_buff_reconciliation:
                        return False
                    try:
                        acknowledge_checkout(
                            buff_reconciliation_intent_id,
                            "user_reconciled_before_pipeline_restart",
                        )
                    except BuffCheckoutGuardMismatch:
                        return False
                if get_pipeline_start_blocker():
                    return False
                # Mark activity while holding the same auth/activity slots used
                # by login and background reads. No request can slip through
                # the acknowledgement -> STARTING transition.
                get_state().set_status("running", "STARTING")
                t = threading.Thread(
                    target=_run_pipeline_guarded,
                    args=(config,),
                    daemon=True,
                    name="buy-pipeline",
                )
                _pipeline_thread = t
                t.start()
                return True
    finally:
        auth_lock.release()
