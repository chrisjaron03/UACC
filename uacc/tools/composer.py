"""
DAG-based Tool Composer — compose multiple tool calls into
higher-level operations with parallel execution.

Based on ToolWeave principles: independent subtasks run in parallel,
dependent subtasks wait for their prerequisites.

Usage:
    compose = ToolComposer()
    compose.add_node("click_save", "click", {"x": 100, "y": 200})
    compose.add_node("type_name", "type", {"text": "hello"}, depends_on=["click_save"])
    compose.run()
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class NodeStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ToolNode:
    """A single node in the tool composition DAG."""

    id: str
    tool: str
    params: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    timeout: float = 30.0


@dataclass
class Composition:
    """A DAG of tool nodes with execution metadata."""

    name: str
    nodes: Dict[str, ToolNode] = field(default_factory=dict)
    created_at: float = 0.0
    completed_at: float = 0.0

    @property
    def is_complete(self) -> bool:
        return all(
            n.status in (NodeStatus.SUCCESS, NodeStatus.SKIPPED, NodeStatus.FAILED)
            for n in self.nodes.values()
        )

    @property
    def is_success(self) -> bool:
        return all(
            n.status in (NodeStatus.SUCCESS, NodeStatus.SKIPPED)
            for n in self.nodes.values()
        )

    def summary(self) -> str:
        lines = [f"Composition: {self.name}"]
        for node_id, node in self.nodes.items():
            arrow = "✓" if node.status == NodeStatus.SUCCESS else "✗"
            lines.append(f"  {arrow} {node_id}: {node.tool}(...) → {node.status.value}")
        return "\n".join(lines)


class ToolComposer:
    """Executes a DAG of composed tool calls.

    Independent nodes run in parallel (via threads). Dependent nodes
    wait for their prerequisites to complete before executing.
    """

    def __init__(self, executor: Optional[Any] = None):
        self._executor = executor
        self._compositions: List[Composition] = []

    def set_executor(self, executor: Any) -> None:
        self._executor = executor

    def create_composition(self, name: str) -> Composition:
        comp = Composition(name=name, created_at=time.time())
        self._compositions.append(comp)
        return comp

    def add_node(
        self,
        comp: Composition,
        node_id: str,
        tool: str,
        params: Optional[Dict[str, Any]] = None,
        depends_on: Optional[List[str]] = None,
        timeout: float = 30.0,
    ) -> ToolNode:
        node = ToolNode(
            id=node_id,
            tool=tool,
            params=params or {},
            depends_on=depends_on or [],
            timeout=timeout,
        )
        comp.nodes[node_id] = node
        return node

    def run(
        self,
        comp: Composition,
        executor: Optional[Any] = None,
    ) -> Composition:
        """Execute the composition DAG.

        Resolves dependencies, runs independent nodes in parallel,
        and returns the completed composition.
        """
        ex = executor or self._executor
        if ex is None:
            raise ValueError("No executor provided. Call set_executor() or pass executor=.")

        comp.created_at = time.time()
        remaining: Set[str] = set(comp.nodes.keys())
        completed: Set[str] = set()
        errors: Dict[str, str] = {}

        while remaining:
            batch: List[ToolNode] = []
            for node_id in remaining:
                node = comp.nodes[node_id]
                if node.status == NodeStatus.FAILED:
                    continue
                if all(dep in completed for dep in node.depends_on):
                    batch.append(node)

            if not batch:
                # Deadlock or all remaining nodes have failed dependencies
                for node_id in remaining:
                    node = comp.nodes[node_id]
                    failed_deps = [d for d in node.depends_on if d in errors]
                    if failed_deps:
                        node.status = NodeStatus.SKIPPED
                        node.error = f"Dependency failed: {failed_deps[0]}"
                        remaining.discard(node_id)
                        completed.add(node_id)
                if not batch:
                    break  # Nothing left to run

            # Run batch in parallel
            threads: List[threading.Thread] = []
            results: Dict[str, Any] = {}
            lock = threading.Lock()

            def run_node(node: ToolNode) -> None:
                try:
                    node.status = NodeStatus.RUNNING
                    # Look up the tool function from executor or fallback registry
                    tool_fn = self._resolve_tool(node.tool, ex)
                    if tool_fn is None:
                        raise ValueError(f"Unknown tool: {node.tool}")

                    result = tool_fn(**node.params)
                    with lock:
                        node.status = NodeStatus.SUCCESS
                        node.result = result
                        results[node.id] = result
                        remaining.discard(node.id)
                        completed.add(node.id)
                except Exception as exc:
                    with lock:
                        node.status = NodeStatus.FAILED
                        node.error = str(exc)
                        errors[node.id] = str(exc)
                        remaining.discard(node.id)

            for node in batch:
                t = threading.Thread(target=run_node, args=(node,), daemon=True)
                t.start()
                threads.append(t)

            for t in threads:
                t.join(timeout=10.0)

            time.sleep(0.05)

        comp.completed_at = time.time()
        return comp

    def _resolve_tool(self, tool: str, executor: Any) -> Optional[Callable]:
        """Resolve a tool name to a callable.

        Tries, in order:
          1. Method on the executor object
          2. uacc.actions.executor.ActionExecutor known methods
          3. Common tool name mapping
        """
        if tool in ("click", "type", "hotkey", "scroll", "wait", "screenshot", "press_key"):
            method_name = {
                "press_key": "press_key",
            }.get(tool, tool)

            if hasattr(executor, method_name):
                return getattr(executor, method_name)
            if hasattr(executor, "execute_action"):
                # Many executors have a generic execute_action
                return lambda **kw: executor.execute_action(tool, **kw)

        # Try hyphenated/snake variants
        attr_name = tool.replace("-", "_")
        if hasattr(executor, attr_name):
            return getattr(executor, attr_name)

        return None

    def compose(
        self,
        steps: List[Dict[str, Any]],
        name: str = "auto_composition",
    ) -> Composition:
        """Convenience method: build and run a composition from a list of steps.

        Each step dict:
          {
            "id": "step1",
            "tool": "click",
            "params": {"x": 100, "y": 200},
            "depends_on": ["step0"],  # optional
            "timeout": 30.0,          # optional
          }
        """
        comp = self.create_composition(name)
        for step in steps:
            self.add_node(
                comp,
                node_id=step["id"],
                tool=step["tool"],
                params=step.get("params"),
                depends_on=step.get("depends_on"),
                timeout=step.get("timeout", 30.0),
            )
        return self.run(comp)
