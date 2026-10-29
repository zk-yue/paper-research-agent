"""Single-process task lifecycle, atomic persistence and WebSocket delivery."""

import asyncio
import json
import logging
import shutil
import uuid
from collections.abc import Coroutine
from datetime import datetime
from functools import wraps
from threading import Event, RLock
from typing import Any

from backend.schemas import TaskInfo
from backend.task_paths import (
    ensure_task_dir,
    find_task_dir,
    iter_metadata_files,
    legacy_metadata_file,
    task_metadata_file,
)
from storage import atomic_write_json

logger = logging.getLogger(__name__)


def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


class TaskManager:
    """One manager per server process. Start only one Uvicorn worker."""

    def __init__(self):
        self._lock = RLock()
        self.tasks: dict[str, TaskInfo] = {}
        self.cancel_flags: dict[str, Event] = {}
        self.ws_subscribers: dict[str, list[asyncio.Queue]] = {}
        self._subscriber_loops: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._running: dict[str, asyncio.Task] = {}

    @synchronized
    def load_existing_tasks(self):
        """Recover at application startup, never as a module import side effect."""
        for path in iter_metadata_files():
            try:
                task = TaskInfo(**json.loads(path.read_text(encoding="utf-8")))
                if task.id in self.tasks:
                    continue
                if task.status in {"running", "pending"}:
                    task.status = "failed"
                    task.error = "服务器重启，任务中断"
                    task.completed_at = datetime.now().isoformat()
                self._save_task(task)
                self.tasks[task.id] = task
                self.cancel_flags[task.id] = Event()
            except (ValueError, OSError, TypeError):
                logger.warning("Cannot recover task metadata: %s", path, exc_info=True)

    def _save_task(self, task: TaskInfo):
        ensure_task_dir(task.id, task.keywords)
        atomic_write_json(task_metadata_file(task.id, task.keywords), task.model_dump())
        legacy_metadata_file(task.id).unlink(missing_ok=True)

    @synchronized
    def create_task(self, **kwargs) -> TaskInfo:
        task = TaskInfo(
            id=uuid.uuid4().hex[:12],
            status="pending",
            created_at=datetime.now().isoformat(),
            **kwargs,
        )
        self._save_task(task)
        self.tasks[task.id] = task
        self.cancel_flags[task.id] = Event()
        return task

    def start_task(self, task_id: str, coroutine: Coroutine[Any, Any, Any]):
        """Retain background jobs so deletion and shutdown can cancel them."""
        if task_id in self._running:
            coroutine.close()
            raise ValueError("Task is already running.")
        future = asyncio.create_task(coroutine, name=f"research-{task_id}")
        self._running[task_id] = future
        future.add_done_callback(lambda done: self._job_finished(task_id, done))

    def _job_finished(self, task_id: str, future: asyncio.Task):
        self._running.pop(task_id, None)
        if not future.cancelled() and future.exception() is not None:
            logger.error("Background task %s failed: %s", task_id, future.exception())

    async def shutdown(self):
        futures = list(self._running.values())
        for task_id in list(self._running):
            self.cancel_task(task_id)
        await asyncio.gather(*futures, return_exceptions=True)
        for task_id, future in tuple(self._running.items()):
            if future.done():
                self._running.pop(task_id, None)

    @synchronized
    def get_task(self, task_id: str) -> TaskInfo | None:
        return self.tasks.get(task_id)

    @synchronized
    def list_tasks(self) -> list[TaskInfo]:
        return sorted(self.tasks.values(), key=lambda task: task.created_at, reverse=True)

    @synchronized
    def list_summaries(self) -> list[dict]:
        """Keep polling payloads independent of paper abstracts and generated text."""
        summaries = []
        for task in self.list_tasks():
            item = task.model_dump(exclude={"papers", "results"})
            item["paper_count"] = len(task.papers)
            item["review_count"] = len((task.results or {}).get("custom_reviews", []))
            summaries.append(item)
        return summaries

    @synchronized
    def cancel_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if task is None or task.status not in {"running", "pending"}:
            return False
        self.cancel_flags[task_id].set()
        task.status = "cancelled"
        task.completed_at = datetime.now().isoformat()
        self._save_task(task)
        self.broadcast(task_id, {"type": "cancelled"})
        future = self._running.get(task_id)
        if future is not None and not future.done():
            future.get_loop().call_soon_threadsafe(future.cancel)
        return True

    @synchronized
    def delete_task(self, task_id: str) -> bool:
        if task_id not in self.tasks:
            return False
        self.cancel_task(task_id)
        root = find_task_dir(task_id)
        if root is not None:
            shutil.rmtree(root)
        legacy_metadata_file(task_id).unlink(missing_ok=True)
        del self.tasks[task_id]
        self.cancel_flags.pop(task_id, None)
        # Active WebSocket owners clean up their subscriptions in finally.
        return True

    _WS_NOTIFY_FIELDS = frozenset({"status", "completed_at", "papers", "error", "steps", "results"})

    @synchronized
    def update_task(self, task_id: str, **kwargs) -> TaskInfo | None:
        task = self.tasks.get(task_id)
        if task is None:
            return None
        if task.status == "cancelled" and kwargs.get("status") not in (None, "cancelled"):
            return task
        for key, value in kwargs.items():
            if key in TaskInfo.model_fields and key != "id":
                setattr(task, key, value)
        self._save_task(task)
        if self._WS_NOTIFY_FIELDS & kwargs.keys():
            self.broadcast(task_id, {"type": "task_update", "task": task.model_dump()})
        return task

    @synchronized
    def set_step(self, task_id: str, idx: int, status: str, detail: str = ""):
        task = self.tasks.get(task_id)
        if task is None or self.is_cancelled(task_id) or not 0 <= idx < len(task.steps):
            return
        task.steps[idx].status = status
        task.steps[idx].detail = detail
        self._save_task(task)
        self.broadcast(
            task_id, {"type": "step_update", "step_idx": idx, "status": status, "detail": detail}
        )

    @synchronized
    def subscribe_ws(self, task_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.ws_subscribers.setdefault(task_id, []).append(queue)
        self._subscriber_loops[queue] = asyncio.get_running_loop()
        return queue

    @synchronized
    def unsubscribe_ws(self, task_id: str, queue: asyncio.Queue):
        subscribers = self.ws_subscribers.get(task_id, [])
        if queue in subscribers:
            subscribers.remove(queue)
        if not subscribers:
            self.ws_subscribers.pop(task_id, None)
        self._subscriber_loops.pop(queue, None)

    @synchronized
    def broadcast(self, task_id: str, data: dict):
        """Schedule bounded queue writes on each subscriber's owning event loop."""
        for queue in tuple(self.ws_subscribers.get(task_id, [])):
            loop = self._subscriber_loops[queue]
            try:
                loop.call_soon_threadsafe(self._enqueue, queue, data)
            except RuntimeError:
                self.unsubscribe_ws(task_id, queue)

    @staticmethod
    def _enqueue(queue: asyncio.Queue, data: dict):
        if queue.full():
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait({"type": "resync_required"})
        queue.put_nowait(data)

    @synchronized
    def is_cancelled(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        return task is None or task.status == "cancelled" or self.cancel_flags[task_id].is_set()


task_manager = TaskManager()
