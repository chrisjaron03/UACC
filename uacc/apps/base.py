"""
Base App Agent — abstract interface for application-specific agents.

Each app agent knows:
  - How to launch the application
  - Standard UI layout and navigation patterns
  - Application-specific keyboard shortcuts
  - How to verify the app state

Usage:
    class OutlookAgent(AppAgent):
        def launch(self): ...
        def navigate_to_inbox(self): ...
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from uacc.actions.executor import ActionExecutor
from uacc.actions.schema import (
    Action,
    ClickAction,
    HotkeyAction,
    TypeAction,
    WaitAction,
)

logger = logging.getLogger(__name__)


class AppAgent(ABC):
    """Base class for application-specific agents.

    Each subclass implements the navigation and interaction patterns
    for a specific desktop application.
    """

    def __init__(self, executor: Any = None):
        self.executor = executor

    @abstractmethod
    def app_name(self) -> str:
        ...

    @abstractmethod
    def launch(self) -> Dict[str, Any]:
        """Launch the application. Returns execution result."""
        ...

    def set_executor(self, executor: Any) -> None:
        self.executor = executor

    def execute(self, action: Action) -> Dict[str, Any]:
        """Execute an action via the shared executor."""
        if self.executor is None:
            return {"success": False, "message": "No executor set"}
        return self.executor.execute(action)

    def wait(self, seconds: float = 1.0) -> Dict[str, Any]:
        return self.execute(WaitAction(seconds=seconds))

    def click(self, x: int, y: int, button: str = "left") -> Dict[str, Any]:
        return self.execute(ClickAction(x=x, y=y, button=button))

    def hotkey(self, keys: List[str]) -> Dict[str, Any]:
        return self.execute(HotkeyAction(keys=keys))

    def type_text(self, text: str) -> Dict[str, Any]:
        return self.execute(TypeAction(text=text))

    def get_shortcuts(self) -> Dict[str, List[str]]:
        """Return app-specific keyboard shortcuts."""
        return {}

    def get_context_prompt(self) -> str:
        """Return a system prompt snippet describing this app's UI."""
        return f"You are interacting with {self.app_name()}."
