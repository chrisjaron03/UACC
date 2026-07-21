"""
File Explorer Agent — navigate the Windows file system UI.

Knows the File Explorer layout: navigation pane, address bar,
file list, context menus, and common operations.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from uacc.apps.base import AppAgent
from uacc.core.window_manager import launch_application as _launch_app

logger = logging.getLogger(__name__)


class FileExplorerAgent(AppAgent):
    """Windows File Explorer — file management and navigation."""

    def app_name(self) -> str:
        return "File Explorer"

    def launch(self) -> Dict[str, Any]:
        return _launch_app("explorer")

    def get_shortcuts(self) -> Dict[str, List[str]]:
        return {
            "open_new_window": ["win", "e"],
            "address_bar": ["alt", "d"],
            "refresh": ["f5"],
            "new_folder": ["ctrl", "shift", "n"],
            "rename": ["f2"],
            "delete": ["delete"],
            "permanent_delete": ["shift", "delete"],
            "copy": ["ctrl", "c"],
            "cut": ["ctrl", "x"],
            "paste": ["ctrl", "v"],
            "select_all": ["ctrl", "a"],
            "properties": ["alt", "enter"],
            "view_details": ["ctrl", "shift", "6"],
            "view_icons": ["ctrl", "shift", "2"],
            "view_list": ["ctrl", "shift", "5"],
            "previous_folder": ["alt", "left"],
            "next_folder": ["alt", "right"],
            "up_one_level": ["alt", "up"],
            "search": ["ctrl", "f"],
            "undo": ["ctrl", "z"],
        }

    def get_context_prompt(self) -> str:
        return (
            "You are controlling Windows File Explorer. Common navigation:\n"
            "  - Win+E → Open File Explorer\n"
            "  - The left panel shows Quick Access, This PC, and drives\n"
            "  - The address bar shows the current path; Alt+D focuses it\n"
            "  - Ctrl+Shift+N → New folder\n"
            "  - F2 → Rename selected item\n"
            "  - Alt+Left → Previous folder\n"
            "  - Delete → Send to Recycle Bin; Shift+Delete → Permanent delete\n"
            "  - Right-click for context menu"
        )


def register() -> None:
    from uacc.apps.registry import register_agent
    register_agent("file explorer", FileExplorerAgent)
    register_agent("explorer", FileExplorerAgent)
    register_agent("windows explorer", FileExplorerAgent)
