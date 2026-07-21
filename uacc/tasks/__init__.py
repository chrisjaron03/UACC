"""
UACC Tasks — long-running operations management.

Provides background task execution with:
- Lifecycle tracking (pending → running → completed / failed / cancelled)
- Progress reporting
- Result collection
- Concurrent task limits

Integrates with the MCP server as tool-accessible operations.
"""

from uacc.tasks.manager import Task, TaskManager, TaskStatus

__all__ = ["TaskManager", "Task", "TaskStatus"]
