"""
Task Manager — background task execution for long-running operations.

Supports:
- Submit tasks that run in background threads
- Poll status and progress
- Cancel running tasks
- List active/completed tasks
- Configurable concurrency limit
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """A single long-running task."""

    id: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    progress_message: str = ""
    result: Any = None
    error: Optional[str] = None
    created_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "progress": self.progress,
            "progress_message": self.progress_message,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class TaskManager:
    """Manages background task execution.

    Usage:
        manager = TaskManager(max_concurrent=3)

        def my_task(progress_cb):
            progress_cb(0.5, "Halfway done")
            return "result"

        task_id = manager.submit("My Task", my_task)
        status = manager.get_status(task_id)
        manager.cancel(task_id)
    """

    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        self._tasks: Dict[str, Task] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._cancel_flags: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        name: str,
        fn: Callable,
        args: Optional[tuple] = None,
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Submit a background task. Returns task ID."""
        task_id = uuid.uuid4().hex[:12]
        task = Task(
            id=task_id,
            name=name,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        with self._lock:
            self._tasks[task_id] = task
            self._cancel_flags[task_id] = threading.Event()

        def _run() -> None:
            with self._lock:
                self._tasks[task_id].status = TaskStatus.RUNNING
                self._tasks[task_id].started_at = datetime.now(timezone.utc).isoformat()

            cancel_flag = self._cancel_flags[task_id]

            def progress_cb(progress: float, message: str = "") -> None:
                with self._lock:
                    self._tasks[task_id].progress = progress
                    self._tasks[task_id].progress_message = message

            try:
                result = fn(
                    *((args or ()) + (cancel_flag,)),
                    **(kwargs or {}),
                )

                if cancel_flag.is_set():
                    with self._lock:
                        self._tasks[task_id].status = TaskStatus.CANCELLED
                        self._tasks[task_id].completed_at = (
                            datetime.now(timezone.utc).isoformat()
                        )
                    return

                with self._lock:
                    self._tasks[task_id].status = TaskStatus.COMPLETED
                    self._tasks[task_id].progress = 1.0
                    self._tasks[task_id].result = result
                    self._tasks[task_id].completed_at = (
                        datetime.now(timezone.utc).isoformat()
                    )
            except Exception as exc:
                with self._lock:
                    self._tasks[task_id].status = TaskStatus.FAILED
                    self._tasks[task_id].error = str(exc)
                    self._tasks[task_id].completed_at = (
                        datetime.now(timezone.utc).isoformat()
                    )
                logger.exception("Task %s failed: %s", task_id, exc)
            finally:
                with self._lock:
                    self._cancel_flags.pop(task_id, None)

        # Check concurrency
        active = sum(
            1 for t in self._tasks.values()
            if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        )
        if active >= self.max_concurrent:
            with self._lock:
                self._tasks[task_id].status = TaskStatus.FAILED
                self._tasks[task_id].error = (
                    f"Max concurrent tasks ({self.max_concurrent}) reached"
                )
                self._tasks[task_id].completed_at = (
                    datetime.now(timezone.utc).isoformat()
                )
            logger.warning("Task %s rejected: max concurrency reached", task_id)
            return task_id

        t = threading.Thread(target=_run, daemon=True, name=f"task-{task_id[:8]}")
        self._threads[task_id] = t
        t.start()
        return task_id

    def get_status(self, task_id: str) -> Optional[Task]:
        """Get task status. Returns None if not found."""
        with self._lock:
            return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        """Cancel a running task. Returns True if cancelled."""
        with self._lock:
            flag = self._cancel_flags.get(task_id)
            task = self._tasks.get(task_id)
            if flag and task and task.status == TaskStatus.RUNNING:
                flag.set()
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now(timezone.utc).isoformat()
                return True
            if task and task.status == TaskStatus.PENDING:
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now(timezone.utc).isoformat()
                return True
        return False

    def list_tasks(
        self,
        status_filter: Optional[TaskStatus] = None,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """List tasks, optionally filtered by status."""
        with self._lock:
            tasks = list(self._tasks.values())
        if status_filter:
            tasks = [t for t in tasks if t.status == status_filter]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [t.to_dict() for t in tasks[:max_results]]

    def wait_for(self, task_id: str, timeout: float = 300.0) -> Optional[Task]:
        """Wait for a task to complete. Returns the final Task object."""
        t = self._threads.get(task_id)
        if t and t.is_alive():
            t.join(timeout=timeout)
        return self.get_status(task_id)

    def cleanup(self, max_age_hours: float = 24.0) -> int:
        """Remove completed/failed/cancelled tasks older than max_age_hours."""
        import time as time_module

        now = time_module.time()
        removed = 0
        with self._lock:
            to_remove = []
            for task_id, task in self._tasks.items():
                if task.status in (
                    TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED
                ) and task.completed_at:
                    try:
                        completed = datetime.fromisoformat(task.completed_at)
                        age = now - completed.timestamp()
                        if age > max_age_hours * 3600:
                            to_remove.append(task_id)
                    except (ValueError, TypeError):
                        continue
            for task_id in to_remove:
                del self._tasks[task_id]
                self._threads.pop(task_id, None)
                removed += 1
        return removed
