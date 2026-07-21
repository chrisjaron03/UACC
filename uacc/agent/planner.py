"""
Hybrid Agent Loop — two-level architecture combining global planning
with local ReAct execution.

Architecture:
  ┌─────────────────────────────────────────────────────┐
  │  Outer Loop: Planner                                 │
  │  ─ Decompose goal into subtask DAG                  │
  │  ─ Track progress, detect completion                │
  │  ─ Replan on failure (partial replan)               │
  └──────────────────────┬──────────────────────────────┘
                         │ dispatches subtasks
  ┌──────────────────────▼──────────────────────────────┐
  │  Inner Loop: Executor (existing ReAct)              │
  │  ─ Observe → Think → Act → Verify per subtask       │
  │  ─ Returns control + result to planner              │
  └─────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SubtaskStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED = auto()
    BLOCKED = auto()


@dataclass
class Subtask:
    """A single unit of work within the overall plan."""

    id: str
    description: str
    status: SubtaskStatus = SubtaskStatus.PENDING
    dependencies: List[str] = field(default_factory=list)
    result: str = ""
    error: str = ""
    attempts: int = 0
    max_attempts: int = 3

    @property
    def ready(self) -> bool:
        if self.status != SubtaskStatus.PENDING:
            return False
        return True


@dataclass
class Plan:
    """Complete task plan consisting of a DAG of subtasks."""

    goal: str
    subtasks: List[Subtask] = field(default_factory=list)
    created_at: float = 0.0

    @property
    def completed(self) -> bool:
        return all(s.status == SubtaskStatus.SUCCESS for s in self.subtasks)

    @property
    def failed(self) -> bool:
        return any(s.status == SubtaskStatus.FAILED for s in self.subtasks)

    @property
    def current_subtask(self) -> Optional[Subtask]:
        for s in self.subtasks:
            if s.status == SubtaskStatus.RUNNING:
                return s
        for s in self.subtasks:
            if s.ready:
                return s
        return None

    def progress_str(self) -> str:
        total = len(self.subtasks)
        done = sum(1 for s in self.subtasks if s.status == SubtaskStatus.SUCCESS)
        failed_count = sum(1 for s in self.subtasks if s.status == SubtaskStatus.FAILED)
        running = sum(1 for s in self.subtasks if s.status == SubtaskStatus.RUNNING)
        return f"[{done}/{total} done, {failed_count} failed, {running} active]"


class Planner:
    """Decomposes goals into subtask DAGs and tracks progress.

    Strategy patterns used:
    - Top-down decomposition: break goal into sequential phases
    - Dependency tracking: identify prerequisites between subtasks
    - Partial replanning: regenerate plan for failed subtasks only
    """

    def __init__(self):
        self.current_plan: Optional[Plan] = None
        self._session_id = str(uuid.uuid4())[:8]

    def create_plan(self, goal: str) -> Plan:
        """Create a structured plan from a natural language goal.

        Uses heuristic decomposition for common task patterns:
        - "open X and do Y" → [launch X, wait, do Y]
        - "find/search X" → [open browser, search, read results]
        - "type X" → [focus field, type text]
        - Complex goals → [observe, plan, execute, verify]

        Args:
            goal: The user's natural language task description.

        Returns:
            A Plan with subtasks ready for execution.
        """
        goal_lower = goal.lower()
        subtasks: List[Subtask] = []

        # Pattern: open/launch → use → verify
        if any(w in goal_lower for w in ["open ", "launch ", "start "]):
            subtasks = [
                Subtask(id="launch", description=f"Launch the application for: {goal}", dependencies=[]),
                Subtask(id="navigate", description=f"Navigate to the right state in the application", dependencies=["launch"]),
                Subtask(id="execute", description=f"Perform the core action: {goal}", dependencies=["navigate"]),
                Subtask(id="verify", description="Verify the result matches expectations", dependencies=["execute"]),
            ]

        # Pattern: search/find → read
        elif any(w in goal_lower for w in ["search ", "find ", "look up ", "research "]):
            subtasks = [
                Subtask(id="open_browser", description="Open a web browser", dependencies=[]),
                Subtask(id="search", description=f"Search for: {goal}", dependencies=["open_browser"]),
                Subtask(id="read_results", description="Read and extract relevant information", dependencies=["search"]),
                Subtask(id="compile", description="Compile findings into a response", dependencies=["read_results"]),
            ]

        # Pattern: write/create → save
        elif any(w in goal_lower for w in ["write ", "create ", "compose ", "draft "]):
            subtasks = [
                Subtask(id="open_app", description="Open the appropriate application", dependencies=[]),
                Subtask(id="create_content", description=f"Create the content: {goal}", dependencies=["open_app"]),
                Subtask(id="save", description="Save the work", dependencies=["create_content"]),
                Subtask(id="verify", description="Verify the content was saved correctly", dependencies=["save"]),
            ]

        # Default: observe → plan → execute → verify
        else:
            subtasks = [
                Subtask(id="observe", description=f"Observe current screen state for: {goal}", dependencies=[]),
                Subtask(id="plan", description=f"Determine the steps needed for: {goal}", dependencies=["observe"]),
                Subtask(id="execute", description=f"Execute the main actions for: {goal}", dependencies=["plan"]),
                Subtask(id="verify", description=f"Verify the result of: {goal}", dependencies=["execute"]),
            ]

        plan = Plan(goal=goal, subtasks=subtasks, created_at=time.time())
        logger.info(
            "Created plan: %s → %d subtasks (session=%s)",
            goal[:60], len(subtasks), self._session_id,
        )

        self.current_plan = plan
        return plan

    def mark_running(self, subtask_id: str) -> None:
        if self.current_plan is None:
            return
        for st in self.current_plan.subtasks:
            if st.id == subtask_id:
                st.status = SubtaskStatus.RUNNING
                st.attempts += 1
                logger.info("Subtask running: %s (attempt %d)", subtask_id, st.attempts)
                return

    def mark_success(self, subtask_id: str, result: str = "") -> None:
        if self.current_plan is None:
            return
        for st in self.current_plan.subtasks:
            if st.id == subtask_id:
                st.status = SubtaskStatus.SUCCESS
                st.result = result
                logger.info("Subtask complete: %s", subtask_id)
                return

    def mark_failed(self, subtask_id: str, error: str = "") -> None:
        if self.current_plan is None:
            return
        for st in self.current_plan.subtasks:
            if st.id == subtask_id:
                st.status = SubtaskStatus.FAILED
                st.error = error
                logger.warning("Subtask failed: %s — %s", subtask_id, error[:100])
                # Block dependent subtasks
                for other in self.current_plan.subtasks:
                    if subtask_id in other.dependencies:
                        other.status = SubtaskStatus.BLOCKED
                        other.error = f"Dependency {subtask_id} failed"
                return

    def get_next(self) -> Optional[Subtask]:
        """Get the next ready subtask for execution."""
        if self.current_plan is None:
            return None
        return self.current_plan.current_subtask

    def get_status(self) -> Dict[str, Any]:
        if self.current_plan is None:
            return {"plan": None}
        return {
            "goal": self.current_plan.goal,
            "subtasks": [
                {
                    "id": s.id,
                    "description": s.description[:60],
                    "status": s.status.name,
                    "attempts": s.attempts,
                    "result": s.result[:80] if s.result else "",
                    "error": s.error[:80] if s.error else "",
                }
                for s in self.current_plan.subtasks
            ],
            "completed": self.current_plan.completed,
            "progress": self.current_plan.progress_str(),
        }
