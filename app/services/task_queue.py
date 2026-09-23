"""
Lightweight in-memory task queue with retry support.
Uses ThreadPoolExecutor for concurrency and provides status
tracking, automatic retries with exponential backoff, and
task-result querying.
"""
import enum
import threading
import time
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"
@dataclass
class TaskInfo:
    task_id: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    attempts: int = 0
    max_retries: int = 0
class TaskQueue:
    """
    In-memory task queue backed by ThreadPoolExecutor.
    Features:
    - Submit callables with optional retry logic
    - Query task status by id
    - Automatic exponential-backoff retries
    - Thread-safe
    """
    def __init__(self, max_workers: int = 4, max_history: int = 200) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tq")
        self._tasks: Dict[str, TaskInfo] = {}
        self._lock = threading.Lock()
        self._max_history = max_history
    def submit(
        self,
        fn: Callable,
        *args: Any,
        name: str = "",
        max_retries: int = 0,
        retry_base_delay: float = 2.0,
        **kwargs: Any,
    ) -> str:
        """
        Submit a task. Returns the task_id immediately.
        Parameters
        ----------
        fn : callable
            The function to execute.
        name : str
            Human-readable label for the task.
        max_retries : int
            Number of retries on failure (0 = no retry).
        retry_base_delay : float
            Base delay (seconds) for exponential backoff.
        """
        task_id = uuid.uuid4().hex[:12]
        info = TaskInfo(
            task_id=task_id,
            name=name or fn.__name__,
            max_retries=max_retries,
        )
        with self._lock:
            self._trim_history()
            self._tasks[task_id] = info
        self._executor.submit(
            self._run_with_retries,
            info,
            fn,
            args,
            kwargs,
            retry_base_delay,
        )
        return task_id
    def get_task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            info = self._tasks.get(task_id)
            if info is None:
                return None
            return self._info_to_dict(info)
    def list_tasks(self, limit: int = 50) -> List[dict]:
        with self._lock:
            items = sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)[:limit]
            return [self._info_to_dict(i) for i in items]
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.RETRYING))
    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)
    def _run_with_retries(
        self,
        info: TaskInfo,
        fn: Callable,
        args: tuple,
        kwargs: dict,
        retry_base_delay: float,
    ) -> None:
        while True:
            info.attempts += 1
            info.status = TaskStatus.RUNNING
            info.started_at = time.time()
            try:
                result = fn(*args, **kwargs)
                info.status = TaskStatus.SUCCESS
                info.result = result
                info.finished_at = time.time()
                return
            except Exception as exc:
                tb = traceback.format_exc()
                info.error = f"{exc}\n{tb}"
                if info.attempts <= info.max_retries:
                    info.status = TaskStatus.RETRYING
                    delay = retry_base_delay * (2 ** (info.attempts - 1))
                    delay = min(delay, 300)  
                    time.sleep(delay)
                else:
                    info.status = TaskStatus.FAILED
                    info.finished_at = time.time()
                    return
    def _trim_history(self) -> None:
        if len(self._tasks) < self._max_history:
            return
        finished = [
            (tid, t) for tid, t in self._tasks.items()
            if t.status in (TaskStatus.SUCCESS, TaskStatus.FAILED)
        ]
        finished.sort(key=lambda x: x[1].finished_at or 0)
        to_remove = len(self._tasks) - self._max_history + 20
        for tid, _ in finished[:to_remove]:
            del self._tasks[tid]
    @staticmethod
    def _info_to_dict(info: TaskInfo) -> dict:
        return {
            "task_id": info.task_id,
            "name": info.name,
            "status": info.status.value,
            "result": info.result,
            "error": info.error,
            "created_at": info.created_at,
            "started_at": info.started_at,
            "finished_at": info.finished_at,
            "attempts": info.attempts,
            "max_retries": info.max_retries,
        }
_queue: Optional[TaskQueue] = None
_queue_lock = threading.Lock()
def get_task_queue() -> TaskQueue:
    global _queue
    with _queue_lock:
        if _queue is None:
            # Startup registers several long-running workers; keep spare
            # capacity for one-shot checks and manual tasks.
            _queue = TaskQueue(max_workers=8)
        return _queue
