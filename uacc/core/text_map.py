"""
Text Map Builder — merge accessibility tree + OCR into a structured
spatial representation that *any* LLM (text-only included) can reason over.

This is the key abstraction that makes non-vision models capable of
controlling a computer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from uacc.core.accessibility import UIElement, flatten_elements
from uacc.core.ocr_engine import OCRResult

logger = logging.getLogger(__name__)


@dataclass
class ScreenElement:
    """Unified element combining accessibility + OCR data."""

    id: str
    element_type: str  # "button", "menu_item", "text_input", "label", etc.
    text: str
    bounds: Tuple[int, int, int, int]  # (left, top, right, bottom)
    center: Tuple[int, int]
    clickable: bool = False
    editable: bool = False
    expandable: bool = False
    expanded: bool = False
    value: str = ""
    source: str = "accessibility"  # "accessibility", "ocr", "merged"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "type": self.element_type,
            "text": self.text,
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
        return d


@dataclass
class ScreenRegion:
    """A spatial region grouping related elements (title bar, sidebar, etc.)."""

    name: str
    bounds: Tuple[int, int, int, int]
    elements: List[ScreenElement] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "region": self.name,
            "bounds": list(self.bounds),
            "elements": [e.to_dict() for e in self.elements],
        }


@dataclass
class TextMap:
    """Complete structured representation of the screen state."""

    screen_width: int
    screen_height: int
    active_window: str
    regions: List[ScreenRegion] = field(default_factory=list)
    all_elements: List[ScreenElement] = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "screen": {
                "resolution": f"{self.screen_width}x{self.screen_height}",
                "active_window": self.active_window,
                "timestamp": self.timestamp,
            },
            "regions": [r.to_dict() for r in self.regions],
            "element_count": len(self.all_elements),
        }

    def to_yaml(self) -> str:
        """Serialize to YAML — the primary format sent to text-only LLMs."""
        return yaml.dump(
            self.to_dict(),
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=120,
        )

    def to_compact_text(self) -> str:
        """Compact text representation for smaller context windows.

        Example output:
            Screen: 1920x1080 | Window: "Visual Studio Code"
            ─── Interactive Elements ───
            [e1] button "File"                    at (22, 15)     clickable
            [e2] button "Edit"                    at (67, 15)     clickable
            [e3] text_input "Search"              at (680, 12)    editable
            [e15] tree_item "📁 src"               at (110, 90)    expandable (expanded)
        """
        lines = [
            f'Screen: {self.screen_width}x{self.screen_height} | Window: "{self.active_window}"',
            "─── Interactive Elements ───",
        ]

        interactive = [
            el for el in self.all_elements if el.clickable or el.editable or el.expandable
        ]

        for el in interactive:
            flags = []
            if el.clickable:
                flags.append("clickable")
            if el.editable:
                flags.append("editable")
            if el.expandable:
                state = "expanded" if el.expanded else "collapsed"
                flags.append(f"expandable ({state})")

            name = el.text[:40] if el.text else "(unnamed)"
            coord = f"at ({el.center[0]}, {el.center[1]})"
            line = f'  [{el.id}] {el.element_type:<14} "{name}"'
            line = f"{line:<55} {coord:<18} {', '.join(flags)}"
            lines.append(line)

        # Also include visible text labels (non-interactive)
        labels = [
            el for el in self.all_elements
            if not el.clickable and not el.editable and not el.expandable and el.text
        ]
        if labels:
            lines.append("")
            lines.append("─── Visible Text ───")
            for el in labels[:50]:  # Cap to keep context small
                lines.append(
                    f'  [{el.id}] "{el.text[:60]}" at ({el.center[0]}, {el.center[1]})'
                )

        return "\n".join(lines)


# ── Type mapping ─────────────────────────────────────────────
_TYPE_MAP = {
    "Button": "button",
    "MenuItem": "menu_item",
    "Menu": "menu",
    "MenuBar": "menu_bar",
    "Edit": "text_input",
    "Document": "text_area",
    "ComboBox": "dropdown",
    "CheckBox": "checkbox",
    "RadioButton": "radio",
    "ListItem": "list_item",
    "TabItem": "tab",
    "TreeItem": "tree_item",
    "Hyperlink": "link",
    "Image": "image",
    "Text": "label",
    "Static": "label",
    "StatusBar": "status_bar",
    "ToolBar": "toolbar",
    "TitleBar": "title_bar",
    "Pane": "pane",
    "Group": "group",
    "Window": "window",
    "Header": "header",
    "HeaderItem": "header_item",
    "DataItem": "data_item",
    "ScrollBar": "scrollbar",
    "Slider": "slider",
    "Spinner": "spinner",
    "ProgressBar": "progress_bar",
    "Table": "table",
    "DataGrid": "data_grid",
}


def _map_type(control_type: str) -> str:
    return _TYPE_MAP.get(control_type, control_type.lower())


# ── Builder ──────────────────────────────────────────────────

def build_text_map(
    screen_width: int,
    screen_height: int,
    ui_elements: List[UIElement],
    ocr_results: Optional[List[OCRResult]] = None,
    active_window: str = "Unknown",
) -> TextMap:
    """Build a unified TextMap from accessibility tree and OCR results.

    Args:
        screen_width, screen_height: Monitor dimensions.
        ui_elements: Elements from the accessibility tree.
        ocr_results: Optional OCR detections to merge in.
        active_window: Title of the focused window.

    Returns:
        A TextMap ready to be serialised and sent to an LLM.
    """
    from datetime import datetime, timezone

    text_map = TextMap(
        screen_width=screen_width,
        screen_height=screen_height,
        active_window=active_window,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # ── 1. Convert accessibility elements ────────────────────
    flat = flatten_elements(ui_elements)
    seen_bounds: set = set()

    for ui_el in flat:
        se = ScreenElement(
            id=ui_el.id,
            element_type=_map_type(ui_el.control_type),
            text=ui_el.name,
            bounds=ui_el.bounds,
            center=ui_el.center,
            clickable=ui_el.clickable,
            editable=ui_el.editable,
            expandable=ui_el.expandable,
            expanded=ui_el.expanded,
            value=ui_el.value,
            source="accessibility",
        )
        text_map.all_elements.append(se)
        seen_bounds.add(ui_el.bounds)

    # ── 2. Merge OCR results (add text not in accessibility tree) ─
    if ocr_results:
        ocr_id_start = len(text_map.all_elements) + 1
        for idx, ocr in enumerate(ocr_results):
            # Check if this OCR detection overlaps with an existing element
            if not _overlaps_any(ocr.bounds, seen_bounds, threshold=0.5):
                se = ScreenElement(
                    id=f"o{ocr_id_start + idx}",
                    element_type="label",
                    text=ocr.text,
                    bounds=ocr.bounds,
                    center=ocr.center,
                    source="ocr",
                )
                text_map.all_elements.append(se)

    # ── 3. Group elements into spatial regions ───────────────
    text_map.regions = _auto_group_regions(
        text_map.all_elements, screen_width, screen_height
    )

    logger.info(
        "Built text map: %d elements, %d regions",
        len(text_map.all_elements),
        len(text_map.regions),
    )
    return text_map


def _overlaps_any(
    bounds: Tuple[int, int, int, int],
    seen: set,
    threshold: float = 0.5,
) -> bool:
    """Check if bounds overlap significantly with any previously seen bounds."""
    bx1, by1, bx2, by2 = bounds
    b_area = max(1, (bx2 - bx1) * (by2 - by1))

    for sx1, sy1, sx2, sy2 in seen:
        ix1 = max(bx1, sx1)
        iy1 = max(by1, sy1)
        ix2 = min(bx2, sx2)
        iy2 = min(by2, sy2)
        if ix1 < ix2 and iy1 < iy2:
            inter_area = (ix2 - ix1) * (iy2 - iy1)
            if inter_area / b_area > threshold:
                return True
    return False


def _auto_group_regions(
    elements: List[ScreenElement],
    screen_w: int,
    screen_h: int,
) -> List[ScreenRegion]:
    """Automatically group elements into spatial regions using simple heuristics.

    Regions: top_bar, left_sidebar, main_content, bottom_bar, right_sidebar.
    """
    TOP_THRESHOLD = int(screen_h * 0.06)       # Top 6% = title/menu bar
    BOTTOM_THRESHOLD = int(screen_h * 0.95)     # Bottom 5% = status bar
    LEFT_THRESHOLD = int(screen_w * 0.18)       # Left 18% = sidebar
    RIGHT_THRESHOLD = int(screen_w * 0.82)      # Right 18% = side panel

    region_map = {
        "top_bar": ScreenRegion("top_bar", (0, 0, screen_w, TOP_THRESHOLD)),
        "left_sidebar": ScreenRegion("left_sidebar", (0, TOP_THRESHOLD, LEFT_THRESHOLD, BOTTOM_THRESHOLD)),
        "main_content": ScreenRegion("main_content", (LEFT_THRESHOLD, TOP_THRESHOLD, RIGHT_THRESHOLD, BOTTOM_THRESHOLD)),
        "right_sidebar": ScreenRegion("right_sidebar", (RIGHT_THRESHOLD, TOP_THRESHOLD, screen_w, BOTTOM_THRESHOLD)),
        "bottom_bar": ScreenRegion("bottom_bar", (0, BOTTOM_THRESHOLD, screen_w, screen_h)),
    }

    for el in elements:
        cx, cy = el.center
        if cy < TOP_THRESHOLD:
            region_map["top_bar"].elements.append(el)
        elif cy > BOTTOM_THRESHOLD:
            region_map["bottom_bar"].elements.append(el)
        elif cx < LEFT_THRESHOLD:
            region_map["left_sidebar"].elements.append(el)
        elif cx > RIGHT_THRESHOLD:
            region_map["right_sidebar"].elements.append(el)
        else:
            region_map["main_content"].elements.append(el)

    # Only return non-empty regions
    return [r for r in region_map.values() if r.elements]
