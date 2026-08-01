"""
Accessibility Tree Extractor — Cross-platform UI Automation integration.

Supports:
- Windows: pywinauto with UIA backend
- macOS:  Accessibility API via pyobjc (Quartz/HIServices framework)
- Linux:  AT-SPI2 via dasbus (D-Bus)
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── TTL Cache for accessibility tree ──────────────────────
_TREE_CACHE: dict = {}
_TREE_CACHE_MAX_AGE_MS: float = 800.0  # cache lives 800ms

_TREE_CACHE_ENABLED: bool = True


def invalidate_tree_cache() -> None:
    """Clear the cached accessibility tree so the next call re-scans."""
    _TREE_CACHE.clear()
    logger.debug("Accessibility tree cache invalidated")


def _get_cached_tree(window_title: str | None, max_depth: int) -> list | None:
    """Return cached tree if it's fresh enough, else None."""
    if not _TREE_CACHE_ENABLED:
        return None
    key = (window_title, max_depth)
    entry = _TREE_CACHE.get(key)
    if entry is not None:
        age_ms = (time.monotonic() - entry["ts"]) * 1000
        if age_ms < _TREE_CACHE_MAX_AGE_MS:
            logger.debug("Tree cache hit (%.0f ms old)", age_ms)
            return entry["tree"]
        else:
            logger.debug("Tree cache expired (%.0f ms old)", age_ms)
            del _TREE_CACHE[key]
    return None


def _store_cached_tree(window_title: str | None, max_depth: int, tree: list) -> None:
    if not _TREE_CACHE_ENABLED:
        return
    key = (window_title, max_depth)
    _TREE_CACHE[key] = {"tree": tree, "ts": time.monotonic()}


# ── Shared UIElement data model ────────────────────────────

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
    toggled: Optional[bool] = None  # UIA ToggleState On/Off (Windows only)
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
        if self.toggled is not None:
            d["toggled"] = self.toggled
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


# ── Platform capability detection ──────────────────────────

_HAS_QUARTZ = False
_HAS_DASBUS = False
_HAS_PYWINAUTO = False

try:
    import Quartz as _Q
    _HAS_QUARTZ = True
except ImportError:
    pass

try:
    import dasbus.connection as _dc
    _HAS_DASBUS = True
except ImportError:
    pass

try:
    import pywinauto
    _HAS_PYWINAUTO = True
except ImportError:
    pass


# ── Dispatch ───────────────────────────────────────────────

def get_ui_tree(window_title: Optional[str] = None, max_depth: int = 8, _skip_cache: bool = False) -> List[UIElement]:
    """Extract the accessibility tree for the active (or specified) window.

    Results are cached with a short TTL (``_TREE_CACHE_MAX_AGE_MS``) so
    consecutive calls within a brief window avoid re-scanning the tree.
    Call ``invalidate_tree_cache()`` after any action that mutates the UI.

    Dispatches to the platform-specific implementation based on ``sys.platform``.

    Args:
        window_title: Substring match for the target window title (Windows only;
                      macOS/Linux always use the frontmost window).
        max_depth: Maximum recursion depth into the element tree.
        _skip_cache: If True, bypass the cache and force a fresh scan.

    Returns:
        Flat-ish list of top-level UIElements (each may have children).
    """
    if not _skip_cache:
        cached = _get_cached_tree(window_title, max_depth)
        if cached is not None:
            return cached

    if sys.platform == "win32":
        result = _get_ui_tree_windows(window_title, max_depth)
    elif sys.platform == "darwin":
        result = _get_ui_tree_macos(max_depth)
    else:
        result = _get_ui_tree_linux(max_depth)

    if not _skip_cache:
        _store_cached_tree(window_title, max_depth, result)
    return result


# ═══════════════════════════════════════════════════════════
# Windows — pywinauto UIA
# ═══════════════════════════════════════════════════════════

_CLICKABLE_TYPES = {
    "Button", "MenuItem", "Hyperlink", "ListItem", "TabItem",
    "TreeItem", "CheckBox", "RadioButton", "ComboBox", "SplitButton",
    "MenuBar", "Menu", "DataItem", "HeaderItem", "ToolBar",
}

_EDITABLE_TYPES = {"Edit", "Document", "ComboBox"}

_EXPANDABLE_TYPES = {"TreeItem", "MenuItem", "ComboBox", "SplitButton"}

_MAX_TOTAL_ELEMENTS: int = 250
_BROWSER_KEYWORDS = {"chrome", "edge", "firefox", "brave", "opera", "vivaldi", "browser", "msedge"}


def _wrap_element(
    ctrl: Any,
    depth: int = 0,
    max_depth: int = 8,
    is_browser: bool = False,
) -> Optional[UIElement]:
    """Recursively wrap a pywinauto control into a UIElement."""
    global _element_counter
    if _element_counter >= _MAX_TOTAL_ELEMENTS:
        return None

    try:
        rect = ctrl.rectangle()
        if rect.width() <= 0 or rect.height() <= 0:
            return None
        if rect.left < -10000 or rect.top < -10000:
            return None

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

        if elem.expandable:
            try:
                iface = ctrl.iface_expand_collapse
                if iface:
                    state = iface.CurrentExpandCollapseState
                    elem.expanded = state == 1
            except Exception:
                pass

        if elem.editable:
            try:
                elem.value = ctrl.iface_value.CurrentValue or ""
            except Exception:
                pass

        try:
            toggle_state = ctrl.iface_toggle.CurrentToggleState
            elem.toggled = toggle_state == 1  # ToggleState.On
        except Exception:
            pass

        effective_max_depth = min(max_depth, 4) if is_browser else max_depth

        if depth < effective_max_depth:
            try:
                ch_list = ctrl.children()
                max_ch = 40 if is_browser else 150
                if len(ch_list) > max_ch:
                    ch_list = ch_list[:max_ch]
                for child_ctrl in ch_list:
                    if _element_counter >= _MAX_TOTAL_ELEMENTS:
                        break
                    child = _wrap_element(
                        child_ctrl,
                        depth=depth + 1,
                        max_depth=effective_max_depth,
                        is_browser=is_browser,
                    )
                    if child is not None:
                        elem.children.append(child)
            except Exception:
                pass

        return elem

    except Exception as exc:
        logger.debug("Skipping element: %s", exc)
        return None


def _get_ui_tree_windows(window_title: Optional[str] = None, max_depth: int = 8) -> List[UIElement]:
    """Windows: pywinauto UIA accessibility tree."""
    if not _HAS_PYWINAUTO:
        logger.warning("pywinauto not installed — returning empty tree")
        return []

    _reset_ids()
    from pywinauto import Desktop
    desktop = Desktop(backend="uia")

    if window_title:
        try:
            windows = desktop.windows(title_re=f".*{window_title}.*", visible_only=True)
        except Exception:
            windows = []
    else:
        try:
            import pywinauto
            app = pywinauto.application.Application(backend="uia")
            app.connect(active_only=True)
            windows = [app.active()]
        except Exception:
            windows = desktop.windows(visible_only=True)[:1]

    elements: List[UIElement] = []
    for win in windows:
        win_title = ""
        try:
            win_title = win.window_text().lower()
        except Exception:
            pass

        is_browser = any(b in win_title for b in _BROWSER_KEYWORDS)
        elem = _wrap_element(win, depth=0, max_depth=max_depth, is_browser=is_browser)
        if elem is not None:
            elements.append(elem)

    logger.info("Windows tree: %d top-level elements (counter=%d)", len(elements), _element_counter)
    return elements


# ═══════════════════════════════════════════════════════════
# macOS — Accessibility API via pyobjc (Quartz / HIServices)
# ═══════════════════════════════════════════════════════════

_AX_CLICKABLE = {
    "AXButton", "AXCheckBox", "AXRadioButton", "AXComboBox",
    "AXMenuButton", "AXPopUpButton", "AXLink", "AXMenuItem",
    "AXDisclosureTriangle", "AXCell", "AXTabButton", "AXValueIndicator",
    "AXIncrementor", "AXSortButton", "AXToolbarButton",
    "AXHandle", "AXDockItem", "AXRelevanceIndicator",
}

_AX_EDITABLE = {
    "AXTextField", "AXComboBox", "AXTextArea", "AXSearchField",
    "AXTokenField",
}

_AX_EXPANDABLE = {
    "AXDisclosureTriangle", "AXDisclosure", "AXOutline",
    "AXPopUpButton", "AXComboBox", "AXMenuItem", "AXTree", "AXTreeItem",
    "AXSplitGroup",
}


def _ax_get_attr(element, attr: str):
    """Safely get an AX attribute value."""
    try:
        err, value = _Q.AXUIElementCopyAttributeValue(element, attr, None)
        if err == 0:
            return value
    except Exception:
        pass
    return None


def _ax_get_point(element) -> Optional[Tuple[int, int]]:
    """Get AXPosition as (x, y) — handles AXValue->CGPoint bridging."""
    try:
        err, value = _Q.AXUIElementCopyAttributeValue(element, "AXPosition", None)
        if err == 0 and value is not None:
            point = _Q.CGPoint()
            if _Q.AXValueGetValue(value, _Q.kAXValueCGPointType, point):
                return (int(point.x), int(point.y))
    except Exception:
        pass
    return None


def _ax_get_size(element) -> Optional[Tuple[int, int]]:
    """Get AXSize as (w, h) — handles AXValue->CGSize bridging."""
    try:
        err, value = _Q.AXUIElementCopyAttributeValue(element, "AXSize", None)
        if err == 0 and value is not None:
            size = _Q.CGSize()
            if _Q.AXValueGetValue(value, _Q.kAXValueCGSizeType, size):
                return (int(size.width), int(size.height))
    except Exception:
        pass
    return None


def _ax_element_to_ui_element(element, depth: int, max_depth: int) -> Optional[UIElement]:
    """Recursively convert a macOS AXUIElement ref into a UIElement."""
    global _element_counter
    if depth > max_depth or _element_counter >= _MAX_TOTAL_ELEMENTS:
        return None

    role = _ax_get_attr(element, "AXRole") or ""
    title = _ax_get_attr(element, "AXTitle") or ""
    desc = _ax_get_attr(element, "AXDescription") or ""
    value = _ax_get_attr(element, "AXValue") or ""
    name = title or desc

    position = _ax_get_point(element)
    size = _ax_get_size(element)

    if position is None or size is None:
        return None

    x, y = position
    w, h = size

    if w <= 0 or h <= 0:
        return None

    bounds = (x, y, x + w, y + h)
    center = (x + w // 2, y + h // 2)
    elem_id = _next_id()

    clickable = role in _AX_CLICKABLE
    editable = role in _AX_EDITABLE
    expandable = role in _AX_EXPANDABLE

    # Convert AXValue to string if possible
    value_str = ""
    if value is not None:
        try:
            value_str = str(value)
        except Exception:
            pass

    elem = UIElement(
        id=elem_id,
        control_type=role.replace("AX", ""),
        name=name.strip(),
        bounds=bounds,
        center=center,
        clickable=clickable,
        editable=editable,
        expandable=expandable,
        value=value_str,
    )

    children = _ax_get_attr(element, "AXChildren")
    if children:
        for child in children:
            if _element_counter >= _MAX_TOTAL_ELEMENTS:
                break
            child_elem = _ax_element_to_ui_element(child, depth + 1, max_depth)
            if child_elem:
                elem.children.append(child_elem)

    return elem


def _get_ui_tree_macos(max_depth: int = 8) -> List[UIElement]:
    """macOS: Accessibility API via pyobjc Quartz framework."""
    if not _HAS_QUARTZ:
        logger.warning(
            "pyobjc-framework-Quartz not installed — "
            "install it for macOS accessibility tree: "
            "pip install pyobjc-framework-Quartz"
        )
        return []

    import subprocess

    try:
        res = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to return unix id of '
             'first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=5,
        )
        pid_str = res.stdout.strip()
        if not pid_str or not pid_str.isdigit():
            logger.warning("Could not determine frontmost app PID")
            return []
        pid = int(pid_str)
    except Exception as exc:
        logger.warning("Failed to get frontmost app PID via osascript: %s", exc)
        return []

    _reset_ids()
    app_elem = _Q.AXUIElementCreateApplication(pid)

    error, windows = _Q.AXUIElementCopyAttributeValue(app_elem, "AXWindows", None)
    if error != 0 or not windows:
        logger.info("No AX windows found for PID %d", pid)
        return []

    elements: List[UIElement] = []
    for window in windows:
        if _element_counter >= _MAX_TOTAL_ELEMENTS:
            break
        elem = _ax_element_to_ui_element(window, 0, max_depth)
        if elem:
            elements.append(elem)

    logger.info("macOS tree: %d top-level elements (counter=%d)", len(elements), _element_counter)
    return elements


# ═══════════════════════════════════════════════════════════
# Linux — AT-SPI2 via dasbus (D-Bus)
# ═══════════════════════════════════════════════════════════

_ATSPI_ROLE_NAMES = {
    0: "invalid", 1: "accelerator_label", 2: "alert", 3: "animation",
    4: "arrow", 5: "calendar", 6: "canvas", 7: "check_box",
    8: "check_menu_item", 9: "color_chooser", 10: "column_header",
    11: "combo_box", 12: "date_editor", 13: "desktop_icon",
    14: "desktop_frame", 15: "dial", 16: "dialog", 17: "document",
    18: "document_frame", 19: "drawing_area", 20: "file_chooser",
    21: "filler", 22: "font_chooser", 23: "frame", 24: "glass_pane",
    25: "html_container", 26: "icon", 27: "image", 28: "internal_frame",
    29: "label", 30: "layered_pane", 31: "list", 32: "list_item",
    33: "menu", 34: "menu_bar", 35: "menu_item", 36: "option_pane",
    37: "page_tab", 38: "page_tab_list", 39: "panel", 40: "password_text",
    41: "popup_menu", 42: "progress_bar", 43: "push_button",
    44: "radio_button", 45: "radio_menu_item", 46: "root_pane",
    47: "row_header", 48: "scroll_bar", 49: "scroll_pane",
    50: "separator", 51: "slider", 52: "spin_button",
    53: "split_pane", 54: "status_bar", 55: "table", 56: "table_cell",
    57: "table_column_header", 58: "table_row_header",
    59: "tear_off_menu_item", 60: "terminal", 61: "text",
    62: "toggle_button", 63: "tool_bar", 64: "tool_tip", 65: "tree",
    66: "tree_table", 67: "tree_item", 68: "viewport", 69: "window",
    70: "extended", 71: "header", 72: "footer", 73: "paragraph",
    74: "ruler", 75: "application", 76: "autocomplete", 77: "editbar",
    78: "embedded_component", 79: "entry", 80: "chart", 81: "caption",
    82: "document_frame", 83: "heading", 84: "page", 85: "section",
    86: "redundant_object", 87: "form", 88: "image_map", 89: "flash",
    90: "label", 91: "list_box", 92: "list_item", 93: "table_cell",
    94: "document_web", 95: "landmark", 96: "definition",
    97: "comment", 98: "mark", 99: "suggestion", 100: "block_quote",
    101: "subscript", 102: "superscript", 103: "static",
    104: "math_fraction", 105: "math_root", 106: "subscript",
    107: "superscript", 108: "description_list", 109: "description_term",
    110: "description_value", 111: "caption", 112: "paragraph",
    113: "footnote", 114: "content_deletion", 115: "content_insertion",
    116: "mark", 117: "suggestion", 118: "comment",
}

_ATSPI_CLICKABLE = {
    7, 8, 11, 33, 34, 35, 43, 44, 45, 62, 37, 26, 32, 67, 56,
}

_ATSPI_EDITABLE = {
    61, 40, 79, 77, 11,
}

_ATSPI_EXPANDABLE = {
    65, 67, 33, 35, 66, 11,
}

_STATE_EDITABLE_BIT = 7
_STATE_EXPANDABLE_BIT = 9
_STATE_EXPANDED_BIT = 10
_STATE_CHECKED_BIT = 3
_STATE_SELECTED_BIT = 23


def _atspi_has_state(state_set: List[int], bit: int) -> bool:
    word_index = bit // 32
    bit_in_word = bit % 32
    if word_index < len(state_set):
        return bool(state_set[word_index] & (1 << bit_in_word))
    return False


def _atspi_element_to_ui_element(
    bus, obj_path: str, depth: int, max_depth: int,
) -> Optional[UIElement]:
    """Recursively convert an AT-SPI accessible object to UIElement."""
    global _element_counter
    if depth > max_depth or _element_counter >= _MAX_TOTAL_ELEMENTS:
        return None

    try:
        proxy = bus.get_proxy(
            bus_name="org.a11y.atspi.Accessible",
            object_path=obj_path,
            interface_name="org.a11y.atspi.Accessible",
        )
        role_int = proxy.GetRole()
        name = proxy.Name or ""
        state_set = proxy.GetState()
    except Exception:
        return None

    role_name = _ATSPI_ROLE_NAMES.get(role_int, f"role_{role_int}")

    bounds = None
    try:
        comp = bus.get_proxy(
            bus_name="org.a11y.atspi.Component",
            object_path=obj_path,
            interface_name="org.a11y.atspi.Component",
        )
        x, y, w, h = comp.GetExtents(0)
        if w > 0 and h > 0:
            bounds = (x, y, x + w, y + h)
    except Exception:
        pass

    if bounds is None:
        return None

    bx, by, bx2, by2 = bounds
    center = (bx + (bx2 - bx) // 2, by + (by2 - by) // 2)
    elem_id = _next_id()

    clickable = role_int in _ATSPI_CLICKABLE
    editable = role_int in _ATSPI_EDITABLE or _atspi_has_state(state_set, _STATE_EDITABLE_BIT)
    expandable = role_int in _ATSPI_EXPANDABLE or _atspi_has_state(state_set, _STATE_EXPANDABLE_BIT)
    expanded = _atspi_has_state(state_set, _STATE_EXPANDED_BIT)

    elem = UIElement(
        id=elem_id,
        control_type=role_name,
        name=name.strip(),
        bounds=(bx, by, bx2, by2),
        center=center,
        clickable=clickable,
        editable=editable,
        expandable=expandable,
        expanded=expanded,
    )

    try:
        children_paths = proxy.GetChildren()
        if children_paths:
            for child_path in children_paths:
                if _element_counter >= _MAX_TOTAL_ELEMENTS:
                    break
                child_elem = _atspi_element_to_ui_element(bus, child_path, depth + 1, max_depth)
                if child_elem:
                    elem.children.append(child_elem)
    except Exception:
        pass

    return elem


def _get_ui_tree_linux(max_depth: int = 8) -> List[UIElement]:
    """Linux: AT-SPI2 accessibility tree via dasbus (D-Bus)."""
    if not _HAS_DASBUS:
        logger.warning(
            "dasbus not installed — install it for Linux accessibility tree: "
            "pip install dasbus"
        )
        return []

    _reset_ids()

    try:
        bus = _dc.SessionMessageBus()

        registry = bus.get_proxy(
            bus_name="org.a11y.atspi.Registry",
            object_path="/org/a11y/atspi/registry",
            interface_name="org.a11y.atspi.Registry",
        )
        desktop_path = registry.GetDesktop(0)
        if not desktop_path:
            return []

        desktop = bus.get_proxy(
            bus_name="org.a11y.atspi.Accessible",
            object_path=desktop_path,
            interface_name="org.a11y.atspi.Accessible",
        )
        children_paths = desktop.GetChildren()
        if not children_paths:
            return []

        elements: List[UIElement] = []
        for child_path in children_paths:
            if _element_counter >= _MAX_TOTAL_ELEMENTS:
                break
            elem = _atspi_element_to_ui_element(bus, child_path, 0, max_depth)
            if elem:
                elements.append(elem)

        logger.info("Linux tree: %d top-level elements (counter=%d)", len(elements), _element_counter)
        return elements

    except Exception as exc:
        logger.warning("AT-SPI2 accessibility tree extraction failed: %s", exc)
        return []


# ═══════════════════════════════════════════════════════════
# Shared helpers — flatten + filter
# ═══════════════════════════════════════════════════════════

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
