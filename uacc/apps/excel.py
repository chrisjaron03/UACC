"""
Excel Agent — navigate and control Microsoft Excel.

Knows the standard Excel UI layout, common worksheet operations,
formula bar, and keyboard shortcuts.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from uacc.apps.base import AppAgent
from uacc.core.window_manager import launch_application as _launch_app

logger = logging.getLogger(__name__)


class ExcelAgent(AppAgent):
    """Microsoft Excel — spreadsheets, formulas, charts, and data analysis."""

    def app_name(self) -> str:
        return "Excel"

    def launch(self) -> Dict[str, Any]:
        return _launch_app("excel")

    def get_shortcuts(self) -> Dict[str, List[str]]:
        return {
            "new_workbook": ["ctrl", "n"],
            "save": ["ctrl", "s"],
            "open": ["ctrl", "o"],
            "print": ["ctrl", "p"],
            "undo": ["ctrl", "z"],
            "redo": ["ctrl", "y"],
            "copy": ["ctrl", "c"],
            "cut": ["ctrl", "x"],
            "paste": ["ctrl", "v"],
            "find": ["ctrl", "f"],
            "replace": ["ctrl", "h"],
            "go_to_cell": ["ctrl", "g"],
            "select_all": ["ctrl", "a"],
            "bold": ["ctrl", "b"],
            "italic": ["ctrl", "i"],
            "insert_chart": ["alt", "f1"],
            "create_table": ["ctrl", "t"],
            "filter": ["ctrl", "shift", "l"],
            "insert_row": ["ctrl", "shift", "+"],
            "delete_row": ["ctrl", "-"],
            "fill_down": ["ctrl", "d"],
            "fill_right": ["ctrl", "r"],
            "insert_function": ["shift", "f3"],
            "name_manager": ["ctrl", "f3"],
            "calculate_now": ["f9"],
            "spell_check": ["f7"],
        }

    def get_context_prompt(self) -> str:
        return (
            "You are controlling Microsoft Excel. Common navigation:\n"
            "  - Ctrl+N → New workbook\n"
            "  - The formula bar is above the worksheet\n"
            "  - Sheet tabs are at the bottom\n"
            "  - The ribbon has tabs: Home, Insert, Page Layout, Formulas, Data, Review, View\n"
            "  - Alt+F1 → Insert chart from selected data\n"
            "  - Ctrl+T → Create table from selected range\n"
            "  - Ctrl+Shift+L → Toggle filters\n"
            "  - F2 → Edit active cell\n"
            "  - Use Ctrl+G → Go To for cell navigation"
        )


def register() -> None:
    from uacc.apps.registry import register_agent
    register_agent("excel", ExcelAgent)
    register_agent("microsoft excel", ExcelAgent)
