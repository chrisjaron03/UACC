"""
Chrome Agent — navigate and control Google Chrome browser.

Knows Chrome's UI layout: address bar, tabs, bookmarks bar,
dev tools, and common browsing operations.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from uacc.apps.base import AppAgent
from uacc.core.window_manager import launch_application as _launch_app

logger = logging.getLogger(__name__)


class ChromeAgent(AppAgent):
    """Google Chrome — web browsing, tabs, bookmarks, and developer tools."""

    def app_name(self) -> str:
        return "Chrome"

    def launch(self) -> Dict[str, Any]:
        return _launch_app("chrome")

    def get_shortcuts(self) -> Dict[str, List[str]]:
        return {
            "new_tab": ["ctrl", "t"],
            "close_tab": ["ctrl", "w"],
            "reopen_closed_tab": ["ctrl", "shift", "t"],
            "switch_next_tab": ["ctrl", "tab"],
            "switch_prev_tab": ["ctrl", "shift", "tab"],
            "switch_tab_1": ["ctrl", "1"],
            "switch_tab_2": ["ctrl", "2"],
            "switch_tab_last": ["ctrl", "9"],
            "focus_address_bar": ["ctrl", "l"],
            "new_window": ["ctrl", "n"],
            "incognito_window": ["ctrl", "shift", "n"],
            "reload": ["ctrl", "r"],
            "hard_reload": ["ctrl", "shift", "r"],
            "zoom_in": ["ctrl", "+"],
            "zoom_out": ["ctrl", "-"],
            "zoom_reset": ["ctrl", "0"],
            "find": ["ctrl", "f"],
            "find_next": ["ctrl", "g"],
            "save_page": ["ctrl", "s"],
            "print": ["ctrl", "p"],
            "history": ["ctrl", "h"],
            "bookmarks": ["ctrl", "shift", "o"],
            "bookmark_this": ["ctrl", "d"],
            "downloads": ["ctrl", "j"],
            "clear_browsing_data": ["ctrl", "shift", "del"],
            "dev_tools": ["ctrl", "shift", "i"],
            "view_source": ["ctrl", "u"],
            "open_file": ["ctrl", "o"],
            "fullscreen": ["f11"],
            "back": ["alt", "left"],
            "forward": ["alt", "right"],
        }

    def get_context_prompt(self) -> str:
        return (
            "You are controlling Google Chrome. Common navigation:\n"
            "  - Ctrl+L → Focus the address bar (type a URL or search)\n"
            "  - Ctrl+T → New tab\n"
            "  - Ctrl+W → Close current tab\n"
            "  - Ctrl+Shift+T → Reopen last closed tab\n"
            "  - Ctrl+Tab → Switch to next tab\n"
            "  - Ctrl+Shift+I → Open Developer Tools\n"
            "  - F11 → Toggle fullscreen\n"
            "  - Ctrl+D → Bookmark current page\n"
            "  - Ctrl+H → View history\n"
            "  - Ctrl+J → View downloads\n"
            "  - Alt+Left/Right → Back/Forward navigation"
        )


def register() -> None:
    from uacc.apps.registry import register_agent
    register_agent("chrome", ChromeAgent)
    register_agent("google chrome", ChromeAgent)
