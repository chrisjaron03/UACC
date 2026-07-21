"""
Outlook Agent — navigate and control Microsoft Outlook.

Knows the standard Outlook UI layout, common mail/calendar
operations, and keyboard shortcuts.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from uacc.apps.base import AppAgent
from uacc.core.window_manager import launch_application as _launch_app

logger = logging.getLogger(__name__)


class OutlookAgent(AppAgent):
    """Microsoft Outlook — email, calendar, contacts, and tasks."""

    def app_name(self) -> str:
        return "Outlook"

    def launch(self) -> Dict[str, Any]:
        return _launch_app("outlook")

    def get_shortcuts(self) -> Dict[str, List[str]]:
        return {
            "new_email": ["ctrl", "n"],
            "send": ["alt", "s"],
            "reply": ["ctrl", "r"],
            "reply_all": ["ctrl", "shift", "r"],
            "forward": ["ctrl", "f"],
            "refresh": ["f9"],
            "go_to_inbox": ["ctrl", "shift", "i"],
            "go_to_calendar": ["ctrl", "shift", "c"],
            "go_to_tasks": ["ctrl", "shift", "k"],
            "delete": ["ctrl", "d"],
            "archive": ["backspace"],
            "flag": ["ctrl", "shift", "g"],
            "find": ["ctrl", "e"],
            "address_book": ["ctrl", "shift", "b"],
            "create_appointment": ["ctrl", "shift", "a"],
            "switch_folder": ["ctrl", "y"],
        }

    def get_context_prompt(self) -> str:
        return (
            "You are controlling Microsoft Outlook. Common navigation:\n"
            "  - Ctrl+Shift+I → Go to Inbox\n"
            "  - Ctrl+N → New email\n"
            "  - Ctrl+R → Reply\n"
            "  - Ctrl+Shift+C → Go to Calendar\n"
            "  - The left panel shows folders (Inbox, Sent Items, etc.)\n"
            "  - The main area shows the selected folder's contents\n"
            "  - The reading pane shows the selected email\n"
            "  - Alt+S sends an email when composing\n"
            "  - Use Ctrl+E to search emails"
        )


def register() -> None:
    from uacc.apps.registry import register_agent
    register_agent("outlook", OutlookAgent)
    register_agent("microsoft outlook", OutlookAgent)
