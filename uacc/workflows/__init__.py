"""
Workflow Store — persistent, named, reusable automation workflows.

Each workflow is a named sequence of MCP tool calls (actions) that can be
saved, listed, retrieved, and replayed. This gives UACC durable "muscle
memory" — agents build up a library of proven UI automation patterns.

Workflows are stored as YAML files under ~/.uacc/workflows/. The store
module handles CRUD, and the MCP server exposes these as tools so any
agent can create, list, inspect, and run workflows without touching Python.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Storage Path ─────────────────────────────────────────────

UACC_DIR = Path.home() / ".uacc"
WORKFLOWS_DIR = UACC_DIR / "workflows"

# ── Data Model ───────────────────────────────────────────────


@dataclass
class WorkflowStep:
    """A single step in a workflow: one MCP tool call."""

    tool: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Workflow:
    """A named, reusable automation workflow."""

    name: str
    description: str = ""
    steps: List[WorkflowStep] = field(default_factory=list)
    created: float = 0.0
    updated: float = 0.0
    run_count: int = 0
    tags: List[str] = field(default_factory=list)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [asdict(s) for s in self.steps],
            "step_count": self.step_count,
            "created": self.created,
            "created_iso": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(self.created)
            ),
            "updated": self.updated,
            "updated_iso": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(self.updated)
            ),
            "run_count": self.run_count,
            "tags": self.tags,
        }


def workflow_step(name: str, **kwargs) -> WorkflowStep:
    """Shorthand: create a WorkflowStep without boilerplate.

    Usage:
        workflow_step("click", x=500, y=300)
        workflow_step("type_text", text="Hello")
        workflow_step("hotkey", keys=["ctrl", "s"])
    """
    return WorkflowStep(tool=name, params=kwargs)


# ── Workflow Store ────────────────────────────────────────────


class WorkflowStore:
    """Persistent storage for UACC workflows.

    Saves/loads workflows as YAML files under ~/.uacc/workflows/.
    Thread-safe for single-process MCP usage.
    """

    def __init__(self, directory: Path | None = None):
        self.directory = directory or WORKFLOWS_DIR
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Workflow] = {}
        self._load_all()

    # ── Public API ────────────────────────────────────────────

    def save(self, workflow: Workflow) -> Path:
        """Persist a workflow to disk and update cache."""
        now = time.time()
        if workflow.created == 0:
            workflow.created = now
        workflow.updated = now

        path = self._path_for(workflow.name)
        data = workflow.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self._cache[workflow.name] = workflow
        logger.info("Saved workflow '%s' (%d steps)", workflow.name, workflow.step_count)
        return path

    def get(self, name: str) -> Optional[Workflow]:
        """Get a workflow by name (case-insensitive)."""
        # Try exact match first
        if name in self._cache:
            return self._cache[name]

        # Case-insensitive fallback
        for key, wf in self._cache.items():
            if key.lower() == name.lower():
                return wf

        return None

    def list(self, tag: str | None = None) -> List[Dict[str, Any]]:
        """List all workflows, optionally filtered by tag."""
        results = []
        for wf in self._cache.values():
            if tag and tag not in wf.tags:
                continue
            results.append(wf.to_dict())

        # Sort by most recently updated first
        results.sort(key=lambda d: d["updated"], reverse=True)
        return results

    def delete(self, name: str) -> bool:
        """Delete a workflow. Returns True if it existed."""
        wf = self.get(name)
        if wf is None:
            return False

        path = self._path_for(wf.name)
        if path.exists():
            path.unlink()

        if wf.name in self._cache:
            del self._cache[wf.name]

        logger.info("Deleted workflow '%s'", wf.name)
        return True

    def record_step(
        self, name: str, tool: str, params: Dict[str, Any]
    ) -> Optional[Workflow]:
        """Append a step to an existing workflow (live recording mode).

        If the workflow doesn't exist, creates it automatically.
        """
        wf = self.get(name)
        if wf is None:
            wf = Workflow(name=name, description="Recorded workflow")
            self._cache[name] = wf

        wf.steps.append(WorkflowStep(tool=tool, params=params))
        self.save(wf)
        return wf

    def increment_run_count(self, name: str) -> None:
        """Bump the run counter after successful execution."""
        wf = self.get(name)
        if wf:
            wf.run_count += 1
            self.save(wf)

    def reset(self) -> int:
        """Delete all workflows. Returns count of deleted workflows."""
        count = len(self._cache)
        self._cache.clear()
        for path in self.directory.glob("*.json"):
            path.unlink()
        return count

    # ── Internal ──────────────────────────────────────────────

    def _path_for(self, name: str) -> Path:
        """Get the filesystem path for a workflow name."""
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        return self.directory / f"{safe_name}.json"

    def _load_all(self) -> None:
        """Scan the workflows directory and load every workflow."""
        if not self.directory.exists():
            self.directory.mkdir(parents=True, exist_ok=True)
            return

        for path in sorted(self.directory.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                steps = [WorkflowStep(**s) for s in data.pop("steps", [])]
                wf = Workflow(
                    name=data.get("name", path.stem),
                    steps=steps,
                    **{k: v for k, v in data.items() if k in Workflow.__dataclass_fields__},
                )
                self._cache[wf.name] = wf
            except Exception as exc:
                logger.warning("Skipping corrupted workflow %s: %s", path.name, exc)

        logger.debug("Loaded %d workflow(s) from %s", len(self._cache), self.directory)


# ── Global Singleton ──────────────────────────────────────────

_store: WorkflowStore | None = None


def get_store() -> WorkflowStore:
    """Get or create the global WorkflowStore singleton."""
    global _store
    if _store is None:
        _store = WorkflowStore()
    return _store
