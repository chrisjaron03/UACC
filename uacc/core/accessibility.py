"""
Accessibility Tree Extractor — Windows UI Automation integration.

Uses `pywinauto` with the UIA backend to walk the full element tree of
the active (or specified) window and produce a structured list of
interactive UI elements with their bounding boxes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Ensure pywin32 DLLs can be loaded (Python 3.8+ on Windows)
try:
    import os
    import sys as _sys
    _dll_dir = os.path.join(_sys.prefix, 'Lib', 'site-packages', 'pywin32_system32')
    if os.path.isdir(_dll_dir):
        os.add_dll_directory(_dll_dir)
except Exception:
    pass


@dataclass
class UIElement:
    """A single UI element extracted from the accessibility tree."""

    id: str
    control_type: str
    name: str
    bounds: Tuple[int, int, int, int]  # (left, top, right, bottom)
    center: Tuple[int, int]
    clickable: bool = False
    editable: bool = False
    expandable: bool = False
    expanded: bool = False
    value: str = ""
    children: List["UIElement"] = field(default_factory=list)

    @property
    def width(self) -> int:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> int:
        return self.bounds[3] - self.bounds[1]

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "type": self.control_type,
            "name": self.name,
            "bounds": list(self.bounds),
            "center": list(self.center),
        }
        if self.clickable:
            d["clickable"] = True
        if self.editable:
            d["editable"] = True
        if self.expandable:
            d["expandable"] = True
            d["expanded"] = self.expanded
        if self.value:
            d["value"] = self.value
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


# ── Element ID counter ───────────────────────────────────────
_element_counter: int = 0


def _next_id() -> str:
    global _element_counter
    _element_counter += 1
    return f"e{_element_counter}"


def _reset_ids() -> None:
    global _element_counter
    _element_counter = 0


# ── Clickable control types ─────────────────────────────────
_CLICKABLE_TYPES = {
    "Button",
    "MenuItem",
    "Hyperlink",
    "ListItem",
    "TabItem",
    "TreeItem",
    "CheckBox",
    "RadioButton",
    "ComboBox",
    "SplitButton",
    "MenuBar",
    "Menu",
    "DataItem",
    "HeaderItem",
    "ToolBar",
}

_EDITABLE_TYPES = {"Edit", "Document", "ComboBox"}

_EXPANDABLE_TYPES = {"TreeItem", "MenuItem", "ComboBox", "SplitButton"}


def _wrap_element(ctrl: Any, depth: int = 0, max_depth: int = 8) -> Optional[UIElement]:
    """Recursively wrap a pywinauto control into a UIElement."""
    try:
        rect = ctrl.rectangle()
        # Skip zero-size / off-screen elements
        if rect.width() <= 0 or rect.height() <= 0:
            return None
        if rect.left < -10000 or rect.top < -10000:
            return None

        # Skip hidden elements to speed up tree traversal dramatically
        try:
            if not ctrl.is_visible():
                return None
        except Exception:
            pass

        control_type = getattr(ctrl, "friendly_class_name", lambda: "Unknown")()
        name = ""
        try:
            name = ctrl.window_text() or ""
        except Exception:
            pass

        bounds = (rect.left, rect.top, rect.right, rect.bottom)
        center = (rect.mid_point().x, rect.mid_point().y)

        elem = UIElement(
            id=_next_id(),
            control_type=control_type,
            name=name.strip(),
            bounds=bounds,
            center=center,
            clickable=control_type in _CLICKABLE_TYPES,
            editable=control_type in _EDITABLE_TYPES,
            expandable=control_type in _EXPANDABLE_TYPES,
        )

        # Expanded state
        if elem.expandable:
            try:
                iface = ctrl.iface_expand_collapse
                if iface:
                    state = iface.CurrentExpandCollapseState
                    elem.expanded = state == 1  # ExpandCollapseState_Expanded
            except Exception:
                pass

        # Value
        if elem.editable:
            try:
                elem.value = ctrl.iface_value.CurrentValue or ""
            except Exception:
                pass

        # Recurse into children (with depth limit)
        if depth < max_depth:
            try:
                ch_list = ctrl.children()
                if len(ch_list) > 150:
                    ch_list = ch_list[:150]
                for child_ctrl in ch_list:
                    child = _wrap_element(child_ctrl, depth + 1, max_depth)
                    if child is not None:
                        elem.children.append(child)
            except Exception:
                pass

        return elem

    except Exception as exc:
        logger.debug("Skipping element: %s", exc)
        return None


def get_ui_tree(window_title: Optional[str] = None, max_depth: int = 8) -> List[UIElement]:
    """Extract the accessibility tree for a window.

    Args:
        window_title: Substring match for the target window title.
                      If None, uses the currently focused window.
        max_depth: Maximum recursion depth into the element tree.

    Returns:
        Flat-ish list of top-level UIElements (each may have children).
    """
    try:
        from pywinauto import Desktop
    except ImportError:
        logger.warning("pywinauto not installed — returning empty tree")
        return []

    _reset_ids()
    desktop = Desktop(backend="uia")

    if window_title:
        try:
            windows = desktop.windows(title_re=f".*{window_title}.*", visible_only=True)
        except Exception:
            windows = []
    else:
        # Get the foreground window
        try:
            import pywinauto
            app = pywinauto.application.Application(backend="uia")
            app.connect(active_only=True)
            windows = [app.active()]
        except Exception:
            windows = desktop.windows(visible_only=True)[:1]

    elements: List[UIElement] = []
    for win in windows:
        elem = _wrap_element(win, depth=0, max_depth=max_depth)
        if elem is not None:
            elements.append(elem)

    logger.info("Extracted %d top-level elements (counter=%d)", len(elements), _element_counter)
    return elements


def flatten_elements(elements: List[UIElement]) -> List[UIElement]:
    """Flatten a nested element tree into a single list (depth-first)."""
    flat: List[UIElement] = []

    def _walk(el: UIElement) -> None:
        flat.append(el)
        for child in el.children:
            _walk(child)

    for el in elements:
        _walk(el)
    return flat


def get_interactive_elements(elements: List[UIElement]) -> List[UIElement]:
    """Return only elements the user can interact with (clickable, editable)."""
    return [
        el
        for el in flatten_elements(elements)
        if el.clickable or el.editable or el.expandable
    ]
