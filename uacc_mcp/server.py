"""
UACC MCP Server — expose Universal AI Computer Control as MCP tools.

This server lets any MCP-compatible AI agent (Claude Desktop, Cursor,
Cline, etc.) control a computer with pixel-precise UI interactions.

Tools:
    screenshot       — Capture the screen (full or region)
    get_screen_info  — Get structured text map of all UI elements
    click            — Click at exact screen coordinates
    type_text        — Type text via keyboard
    hotkey           — Press key combinations (e.g. Ctrl+S)
    scroll           — Scroll at a position
    drag             — Drag from point A to point B
    hover            — Move mouse to a position and wait
    find_element     — Search for a UI element by name or type

Resources:
    uacc://screen/text-map  — Live text map of current screen
    uacc://config           — Current UACC configuration

Usage:
    # stdio transport (Claude Desktop, Cursor)
    uacc-mcp

    # SSE transport (web clients)
    uacc-mcp --transport sse --port 8765

    # MCP Inspector (development)
    mcp dev uacc_mcp/server.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time

from mcp.server.fastmcp import FastMCP
import mcp.types as t

from uacc.actions.executor import ActionExecutor
from uacc.actions.schema import (
    ClickAction,
    DragAction,
    HotkeyAction,
    HoverAction,
    MouseButton,
    ScrollAction,
    ScrollDirection,
    TypeAction,
)
from uacc.config import config
from uacc.core.accessibility import get_ui_tree
from uacc.core.cdp_bridge import CDPBridge
from uacc.core.clipboard import read_clipboard as _clipboard_read, write_clipboard as _clipboard_write
from uacc.core.grid_encoder import grid_cell_to_pixel, overlay_grid, overlay_markers, build_marker_legend
from uacc.core.ocr_engine import extract_text as _ocr_extract
from uacc.core.scene_graph import build_scene_graph
from uacc.core.element_finder import (
    click_element_by_name,
    get_mouse_position as _get_mouse_position,
    wait_for_element as _wait_for_element,
)
from uacc.core.screen_capture import (
    capture_full,
    capture_region,
    get_screen_size,
    list_monitors as _list_monitors,
)
from uacc.core.text_map import build_text_map
from uacc.core.window_manager import (
    focus_window as _focus_window,
    get_active_window as _get_active_window,
    launch_application as _launch_app,
    list_windows as _list_windows,
    minimize_maximize_window as _min_max_window,
    move_window as _move_window,
    open_url as _open_url,
    resize_window as _resize_window,
)

from uacc import __version__ as uacc_version
from uacc.actions.artistic_painter import ArtisticPainter
from uacc.memory.tools import (
    get_app_action_history as _get_app_action_history,
    memory_summary as _memory_summary,
    query_knowledge as _query_knowledge,
    recall_related_apps as _recall_related_apps,
    record_strategy_performance as _record_strategy,
    remember_action as _remember_action,
)
from uacc.safety.mouse_sentinel import MouseSentinel
from uacc.planning import GoalDecomposer
from uacc.tasks import TaskManager, TaskStatus
from uacc.tools import ToolRegistry, ToolDef
from uacc.workflows import get_store, Workflow, WorkflowStep, workflow_step

from uacc_mcp.utils import (
    format_error,
    get_image_media_type,
    get_session,
    image_to_base64,
)

logger = logging.getLogger(__name__)

# ── MCP Server Instance ─────────────────────────────────────

mcp = FastMCP(
    "uacc",
    instructions=(
        "Universal AI Computer Control — let any AI agent control a "
        "computer with pixel-precise UI interactions. Capture screenshots, "
        "read UI elements, click, type, scroll, drag, and more."
    ),
)

# ── Shared Executor ──────────────────────────────────────────

_executor: ActionExecutor | None = None
_sentinel: MouseSentinel | None = None


def _get_executor() -> ActionExecutor:
    """Lazily create the shared ActionExecutor."""
    global _executor
    if _executor is None:
        _executor = ActionExecutor(
            human_mimicry=config.uacc.human_mimicry,
            safe_mode=config.uacc.safe_mode,
            sentinel=_get_sentinel(),
        )
    return _executor


def _get_sentinel() -> MouseSentinel:
    """Lazily create and start the shared MouseSentinel."""
    global _sentinel
    if _sentinel is None:
        _sentinel = MouseSentinel()
        _sentinel.start()
    return _sentinel


_SENTINEL_CHECK = None


def _check_sentinel() -> str | None:
    """Check if user override has been triggered. Returns error JSON if killed."""
    if _get_sentinel().check_killed():
        return json.dumps({
            "success": False,
            "error": "User override: mouse moved away — call acknowledge_user_override() to resume",
            "killed": True,
            "recovery": "Call acknowledge_user_override() to reset the kill flag and resume automation.",
        })
    return None


def _reset_sentinel_anchor() -> None:
    """Reset the sentinel kill flag and anchor the expected cursor position to
    the current cursor location.

    Painting sessions are long-running automations that legitimately move the
    mouse. The sentinel is a long-lived singleton, so its expected position can
    go stale between tool calls — a stale anchor makes the sentinel false-kill
    the drawing before the first stroke. Anchoring to the live cursor keeps the
    sentinel armed for genuine user pull-aways while letting painting start
    cleanly.
    """
    sentinel = _get_sentinel()
    sentinel.acknowledge_override()
    try:
        import pyautogui
        cur = pyautogui.position()
        sentinel.set_expected_position(int(cur.x), int(cur.y))
    except Exception:
        pass


def _bring_paint_to_front() -> None:
    """Ensure the Paint window is visible and focused after painting.

    Other windows (IDE, browser) can sit on top of Paint while strokes are
    drawn, hiding the finished drawing. Focus is restored via Win32 calls
    only — no mouse movement, so the sentinel stays clean.
    """
    try:
        from uacc.core.window_manager import minimize_maximize_window, focus_window
        minimize_maximize_window("Paint", "maximize")
        focus_window("Paint")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#  TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def screenshot(
    region_x: int | None = None,
    region_y: int | None = None,
    width: int | None = None,
    height: int | None = None,
    monitor_index: int = 1,
    image_format: str = "PNG",
    quality: int = 80,
    save_path: str | None = None,
    overlay: str | None = None,
) -> list[t.TextContent | t.ImageContent]:
    """Capture a screenshot of the screen.

    Returns JSON metadata + a base64-encoded inline image (two MCP content blocks).
    If save_path is set, writes to disk and returns JSON only (no inline image).

    See also:
      - take_snapshot() — saves a named screenshot in memory for later comparison (no image returned).
      - get_screen_info() — get structured text map of UI elements (no image, just data).

    Args:
        region_x: Left edge of the capture region (omit for full screen).
        region_y: Top edge of the capture region (omit for full screen).
        width: Width of the capture region in pixels.
        height: Height of the capture region in pixels.
        monitor_index: Which monitor to capture (1-based, 1 = primary). Use list_monitors to see available monitors.
        image_format: Image format — "PNG" (lossless) or "JPEG" (smaller).
        quality: JPEG quality 1-100 (ignored for PNG).
        save_path: Optional local file path (e.g. "C:\\temp\\screen.png") to save the image.
                     When set, no inline image is returned — only JSON metadata.
        overlay: Visual overlay helper. Values:
                   "grid" — A1-Z27 coordinate grid overlay.
                   "markers" — numbered Set-of-Mark badges on detected UI elements (+ legend in metadata).
                   "grid_fine" / "grid_coarse" — finer/coarser grid variants.

    Returns:
        List of MCP content blocks: [TextContent (JSON metadata), ImageContent (base64 image)].
        When save_path is set: [TextContent (JSON metadata)] only.
    """
    try:
        if region_x is not None and region_y is not None and width and height:
            img = capture_region(region_x, region_y, width, height)
            region_info = f"{width}×{height} at ({region_x}, {region_y})"
        else:
            img = capture_full(monitor_index=monitor_index)
            screen_w, screen_h = img.size
            region_info = f"full screen {screen_w}×{screen_h} (monitor {monitor_index})"

        legend_text = ""
        if overlay:
            if overlay.lower() in ("grid", "grid_medium", "medium"):
                img = overlay_grid(img, mode="medium")
                legend_text = "Grid overlay active: Columns A-Z/AA-BB, Rows 1-27. Use grid_cell_to_pixel or paint_preset."
            elif overlay.lower() in ("grid_fine", "fine"):
                img = overlay_grid(img, mode="fine")
                legend_text = "Fine grid overlay active."
            elif overlay.lower() in ("grid_coarse", "coarse"):
                img = overlay_grid(img, mode="coarse")
                legend_text = "Coarse grid overlay active."
            elif overlay.lower() in ("markers", "som", "badges", "som_markers"):
                ui_elements = get_ui_tree()
                img = overlay_markers(img, ui_elements)
                legend_text = build_marker_legend(ui_elements)

        session = get_session()

        if save_path:
            import os
            # Ensure parent directories exist
            dir_name = os.path.dirname(os.path.abspath(save_path))
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            img.save(save_path)
            session.log_action("screenshot", {"region": region_info, "save_path": save_path, "overlay": overlay}, {"success": True})
            res_dict = {
                "success": True,
                "saved_to": save_path,
                "width": img.size[0],
                "height": img.size[1],
                "region": region_info,
            }
            if legend_text:
                res_dict["legend"] = legend_text
            return [t.TextContent(type="text", text=json.dumps(res_dict))]

        b64 = image_to_base64(img, fmt=image_format, quality=quality)
        media_type = get_image_media_type(image_format)
        session.log_action("screenshot", {"region": region_info, "overlay": overlay}, {"success": True})

        res_dict = {
            "success": True,
            "width": img.size[0],
            "height": img.size[1],
            "region": region_info,
        }
        if legend_text:
            res_dict["legend"] = legend_text

        return [
            t.TextContent(type="text", text=json.dumps(res_dict)),
            t.ImageContent(type="image", data=b64, mimeType=media_type)
        ]

    except Exception as exc:
        return [t.TextContent(type="text", text=json.dumps({"success": False, "error": format_error(exc, "Screenshot capture failed")}))]



# ═══════════════════════════════════════════════════════════════
#  SHARED SCREEN SCANNER (used by get_screen_info + find_element)
# ═══════════════════════════════════════════════════════════════

def _scan_screen(
    include_ocr: bool = False,
) -> tuple:
    """Perform a full screen scan: accessibility tree + optional OCR.

    Returns (screen_w, screen_h, text_map, active_window).
    """
    screen_w, screen_h = get_screen_size()
    ui_elements = get_ui_tree()

    active_window = ""
    if ui_elements and ui_elements[0].name:
        active_window = ui_elements[0].name

    # Optionally run OCR to catch text the accessibility tree misses
    ocr_results = None
    if include_ocr:
        try:
            from uacc.core.ocr_engine import extract_text
            from uacc.core.screen_capture import capture_full as _cap
            img = _cap()
            ocr_results = extract_text(img)
            logger.info("OCR returned %d text regions", len(ocr_results))
        except ImportError:
            logger.debug("easyocr not installed — skipping OCR")
        except Exception as ocr_exc:
            logger.warning("OCR failed: %s", ocr_exc)

    text_map = build_text_map(
        screen_width=screen_w,
        screen_height=screen_h,
        ui_elements=ui_elements,
        ocr_results=ocr_results,
        active_window=active_window,
    )
    return screen_w, screen_h, text_map, active_window


@mcp.tool()
def list_monitors() -> str:
    """List all connected monitors with their dimensions and positions.

    Useful for multi-monitor setups — use the monitor index with
    the screenshot tool to capture from a specific monitor.

    Returns:
        JSON with list of monitors (index, position, size).
    """
    try:
        monitors = _list_monitors()
        return json.dumps({
            "success": True,
            "count": len(monitors),
            "monitors": monitors,
        })
    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "List monitors failed")})


@mcp.tool()
def get_screen_info(include_labels: bool = False, include_ocr: bool = False) -> str:
    """Analyse the current screen and return a structured text map of all UI elements.

    PRIMARY tool for understanding the screen before acting. Call this BEFORE
    clicking or typing. Returns structured data — not an image.
    Use screenshot() when you need a visual image.

    The text map lists every interactive element with its type, label, screen
    coordinates, and interactivity flags. Elements are numbered for cross-referencing
    with screenshot(overlay="markers").

    When to use alternatives:
      - screenshot(overlay="grid") — coordinate grid for visual positioning.
      - screenshot(overlay="markers") — numbered badges on elements.
      - get_screen_info(include_ocr=True) — when accessibility tree misses text
        (games, canvas apps, video editors).
      - get_screen_info_enhanced(mode="hybrid") — when a11y tree + vision both needed.
      - detect_elements_visual() — pure computer vision (games, remote desktop).
      - vlm_locate_element(target) — find a specific element visually.

    Args:
        include_labels: If True, include labels and static text (non-interactive).
                         If False, only interactive elements (buttons, inputs, etc.).
        include_ocr: If True, also run OCR on a screenshot to detect text that
                      falls through accessibility tree gaps (images, canvas, rendered text).
                      Slower (~200-500ms) but catches more text.

    Returns:
        JSON with screen dimensions, active window, element count, and text map.
    """
    try:
        screen_w, screen_h, text_map, active_window = _scan_screen(
            include_ocr=include_ocr,
        )

        # Cache elements for find_element
        session = get_session()
        session.screen_size = (screen_w, screen_h)
        element_dicts = [el.to_dict() for el in text_map.all_elements]
        # Attach name for caching (to_dict uses 'text' key from ScreenElement)
        for el, el_obj in zip(element_dicts, text_map.all_elements):
            el["text"] = el_obj.text
            el["element_type"] = el_obj.element_type
        session.cache_elements(element_dicts)

        compact = text_map.to_compact_text()
        interactive_count = sum(
            1 for el in text_map.all_elements
            if el.clickable or el.editable or el.expandable
        )

        from uacc.core.window_manager import is_security_dialog_open
        security_msg = is_security_dialog_open()

        result = {
            "success": True,
            "screen_width": screen_w,
            "screen_height": screen_h,
            "active_window": active_window,
            "total_elements": len(text_map.all_elements),
            "interactive_elements": interactive_count,
            "text_map": compact,
        }

        if security_msg:
            result["security_dialog_detected"] = True
            result["security_dialog_message"] = security_msg

        if interactive_count <= 5 and not include_ocr:
            result["canvas_or_custom_ui_hint"] = (
                "Low interactive element count detected. The active application may be using HTML5 canvas, WebGL, or custom unlabeled controls. "
                "Consider calling get_screen_info(include_ocr=True), screenshot(overlay='grid'), "
                "detect_elements_visual, or vlm_locate_element for visual grounding."
            )

        if include_labels:
            result["full_yaml"] = text_map.to_yaml()

        session.log_action(
            "get_screen_info",
            {"include_labels": include_labels},
            {"success": True, "elements": len(text_map.all_elements)},
        )

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Screen info failed")})


@mcp.tool()
def click(
    x: int = 0,
    y: int = 0,
    target: str | None = None,
    button: str = "left",
    count: int = 1,
    modifiers: list[str] | None = None,
    reasoning: str = "",
) -> str:
    """Click at exact pixel coordinates or by target element name.

    Use for coordinate-based clicks from get_screen_info / find_element results.
    When you know the element's name/label, prefer click_element() (simpler).
    For unreliable targets that need multi-strategy fallback, use smart_click().

    NOTE: If target is provided and x=0, y=0, automatically delegates to
    smart_click() for self-healing element location.

    Args:
        x: X coordinate (screen-absolute pixels, 0=left edge). Optional if target is set.
        y: Y coordinate (screen-absolute pixels, 0=top edge). Optional if target is set.
        target: Element name/label to find and click (case-insensitive fuzzy match).
                When x=0 and y=0, triggers smart_click fallback.
        button: Mouse button — "left", "right", or "middle".
        count: Click count — 1 for single click, 2 for double click.
        modifiers: Modifier keys to hold — e.g. ["ctrl"], ["shift", "ctrl"].
        reasoning: Why you're clicking (logged for debugging).

    Returns:
        JSON with success, coordinates, button, and verified_active_window.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        if target:
            from uacc.core.element_finder import click_element_by_name
            res = click_element_by_name(name=target, button=button, count=count, modifiers=modifiers)
            if res.get("success"):
                _get_sentinel().set_expected_position(res.get("click_x", x), res.get("click_y", y))
                session = get_session()
                session.log_action("click", {"target": target, "x": res.get("click_x"), "y": res.get("click_y"), "button": button, "count": count, "reasoning": reasoning}, res)
                return json.dumps(res)
            elif x == 0 and y == 0:
                # If target was given and x,y are 0, fall back to smart_click self-healing
                return smart_click(target=target, button=button, reasoning=reasoning or f"Click fallback for '{target}'")

        action = ClickAction(
            x=x,
            y=y,
            button=MouseButton(button),
            count=count,
            modifiers=modifiers or [],
            reasoning=reasoning,
        )

        executor = _get_executor()
        result = executor.execute(action)

        if result.get("success"):
            _get_sentinel().set_expected_position(x, y)

        session = get_session()
        session.log_action(
            "click",
            {"x": x, "y": y, "button": button, "count": count, "reasoning": reasoning},
            result,
        )

        # Post-action verification: capture current active window
        from uacc.core.window_manager import get_active_window as _get_act_win
        act_win = _get_act_win()
        verified_active_window = act_win.title if act_win else ""

        invalidate_tree_cache()

        return json.dumps({
            "success": result["success"],
            "message": result["message"],
            "coordinates": {"x": x, "y": y},
            "button": button,
            "count": count,
            "verified_active_window": verified_active_window,
        })

    except Exception as exc:
        target_info = f" target='{target}'" if target else ""
        logger.error("Click failed at (%d, %d)%s: %s", x, y, target_info, exc, exc_info=False)
        return json.dumps({"success": False, "error": format_error(exc, f"Click failed at ({x},{y}){target_info}")})


@mcp.tool()
def type_text(
    text: str,
    human_like: bool = False,
    reasoning: str = "",
) -> str:
    """Type text via the keyboard.

    Types at the current cursor/focus position. Use click() first to
    focus an input field if needed.

    Args:
        text: The text to type. Supports special characters and newlines.
        human_like: If True, type with variable delays to mimic human typing.
        reasoning: Why you're typing this (for logging).

    Returns:
        JSON with success status and character count.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        action = TypeAction(
            text=text,
            delay_ms=0,
            reasoning=reasoning,
        )

        # Override human_mimicry for this specific call if requested
        executor = _get_executor()
        original_mimicry = executor.human_mimicry
        if human_like:
            executor.human_mimicry = True

        result = executor.execute(action)

        executor.human_mimicry = original_mimicry

        session = get_session()
        session.log_action(
            "type_text",
            {"text_length": len(text), "human_like": human_like, "reasoning": reasoning},
            result,
        )

        invalidate_tree_cache()

        return json.dumps({
            "success": result["success"],
            "message": result["message"],
            "characters_typed": len(text),
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, f"Type text failed (first 50 chars: {text[:50]!r})")})


@mcp.tool()
def hotkey(
    keys: list[str],
    reasoning: str = "",
) -> str:
    """Press a keyboard shortcut combination.

    Args:
        keys: List of keys to press simultaneously.
              Examples: ["ctrl", "s"], ["alt", "f4"], ["ctrl", "shift", "p"].
              Common keys: ctrl, alt, shift, enter, tab, escape, backspace,
              delete, up, down, left, right, home, end, pageup, pagedown,
              f1-f12, space, a-z, 0-9.
        reasoning: Why you're pressing this hotkey (for logging).

    Returns:
        JSON with success status and the key combination pressed.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        action = HotkeyAction(
            keys=keys,
            reasoning=reasoning,
        )

        executor = _get_executor()
        result = executor.execute(action)

        combo = "+".join(keys)
        session = get_session()
        session.log_action("hotkey", {"keys": keys, "reasoning": reasoning}, result)

        invalidate_tree_cache()

        return json.dumps({
            "success": result["success"],
            "message": result["message"],
            "combination": combo,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, f"Hotkey failed: {'+'.join(keys)}")})


@mcp.tool()
def scroll(
    x: int,
    y: int,
    direction: str = "down",
    amount: int = 3,
    reasoning: str = "",
) -> str:
    """Scroll at a specific screen position.

    Moves the mouse to (x, y) and performs a scroll gesture there.

    Args:
        x: X coordinate to position the mouse at before scrolling.
        y: Y coordinate to position the mouse at before scrolling.
        direction: Scroll direction — "up", "down", "left", or "right".
        amount: Number of scroll increments (typically 1-10).
        reasoning: Why you're scrolling (for logging).

    Returns:
        JSON with success, position, direction, and amount.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        action = ScrollAction(
            x=x,
            y=y,
            direction=ScrollDirection(direction),
            amount=amount,
            reasoning=reasoning,
        )

        executor = _get_executor()
        result = executor.execute(action)

        if result.get("success"):
            _get_sentinel().set_expected_position(x, y)

        session = get_session()
        session.log_action(
            "scroll",
            {"x": x, "y": y, "direction": direction, "amount": amount, "reasoning": reasoning},
            result,
        )

        invalidate_tree_cache()

        return json.dumps({
            "success": result["success"],
            "message": result["message"],
            "coordinates": {"x": x, "y": y},
            "direction": direction,
            "amount": amount,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, f"Scroll failed at ({x},{y}) direction={direction}")})


@mcp.tool()
def drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    button: str = "left",
    duration_ms: int = 500,
    reasoning: str = "",
) -> str:
    """Drag from one screen position to another (click-hold-move-release).

    Args:
        start_x: Starting X coordinate.
        start_y: Starting Y coordinate.
        end_x: Ending X coordinate.
        end_y: Ending Y coordinate.
        button: Mouse button — "left", "right", or "middle".
        duration_ms: Duration of the drag in milliseconds.
        reasoning: Why you're dragging (for logging).

    Returns:
        JSON with success status and drag details.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        action = DragAction(
            start_x=start_x,
            start_y=start_y,
            end_x=end_x,
            end_y=end_y,
            button=MouseButton(button),
            duration_ms=duration_ms,
            reasoning=reasoning,
        )

        executor = _get_executor()
        result = executor.execute(action)

        if result.get("success"):
            _get_sentinel().set_expected_position(end_x, end_y)

        session = get_session()
        session.log_action(
            "drag",
            {
                "start": {"x": start_x, "y": start_y},
                "end": {"x": end_x, "y": end_y},
                "reasoning": reasoning,
            },
            result,
        )

        # Post-action verification: capture current active window
        from uacc.core.window_manager import get_active_window as _get_act_win
        act_win = _get_act_win()
        verified_active_window = act_win.title if act_win else ""

        invalidate_tree_cache()

        return json.dumps({
            "success": result["success"],
            "message": result["message"],
            "start": {"x": start_x, "y": start_y},
            "end": {"x": end_x, "y": end_y},
            "verified_active_window": verified_active_window,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, f"Drag failed from ({start_x},{start_y}) to ({end_x},{end_y})")})


@mcp.tool()
def hover(
    x: int,
    y: int,
    duration_ms: int = 500,
    reasoning: str = "",
) -> str:
    """Move the mouse to a position and pause (triggers tooltips, hover menus).

    Moves the mouse to (x, y) and waits for duration_ms. Does NOT click or
    press the mouse button — it's a cursor-only hover.

    Args:
        x: X coordinate to hover at.
        y: Y coordinate to hover at.
        duration_ms: How long to pause at the position (ms). Default 500.
        reasoning: Why you're hovering (for logging).

    Returns:
        JSON with success, coordinates, and duration.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        action = HoverAction(
            x=x,
            y=y,
            duration_ms=duration_ms,
            reasoning=reasoning,
        )

        executor = _get_executor()
        result = executor.execute(action)

        if result.get("success"):
            _get_sentinel().set_expected_position(x, y)

        session = get_session()
        session.log_action(
            "hover",
            {"x": x, "y": y, "duration_ms": duration_ms, "reasoning": reasoning},
            result,
        )

        return json.dumps({
            "success": result["success"],
            "message": result["message"],
            "coordinates": {"x": x, "y": y},
            "duration_ms": duration_ms,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Hover failed")})


@mcp.tool()
def find_element(
    name: str | None = None,
    element_type: str | None = None,
    refresh: bool = True,
) -> str:
    """Find UI elements on the screen by name and/or type.

    Scans the accessibility tree and returns matching elements with
    coordinates. Use the returned coordinates with click(x, y).

    When to use alternatives:
      - vlm_locate_element(target) — find by visual description (VLM-based).
      - uacc_where_is(target) — multi-strategy (a11y → OCR → scene graph → CDP).
      - detect_elements_visual() — pure CV detection for games/canvas.

    Args:
        name: Text to search for in element labels (case-insensitive substring match).
              Example: "File", "Save", "OK", "Cancel".
        element_type: Element type to filter by.
              Common types: "button", "menu_item", "text_input", "checkbox",
              "tab", "link", "dropdown", "list_item", "tree_item", "label".
        refresh: If True, re-scan the screen first. If False, use cached data.

    Returns:
        JSON with matches count, matching elements (with bounds, center, type), and coordinates.
    """
    try:
        session = get_session()

        if refresh:
            screen_w, screen_h, text_map, _ = _scan_screen()

            element_dicts = [el.to_dict() for el in text_map.all_elements]
            for el_dict, el_obj in zip(element_dicts, text_map.all_elements):
                el_dict["text"] = el_obj.text
                el_dict["element_type"] = el_obj.element_type
            session.cache_elements(element_dicts)
            session.screen_size = (screen_w, screen_h)

        matches = session.find_elements(name=name, element_type=element_type)

        results = []
        for el in matches:
            results.append({
                "id": el.element_id,
                "name": el.name,
                "type": el.element_type,
                "center": {"x": el.center[0], "y": el.center[1]},
                "bounds": {
                    "left": el.bounds[0],
                    "top": el.bounds[1],
                    "right": el.bounds[2],
                    "bottom": el.bounds[3],
                },
                "clickable": el.clickable,
                "editable": el.editable,
                "expandable": el.expandable,
            })

        session.log_action(
            "find_element",
            {"name": name, "element_type": element_type, "refresh": refresh},
            {"success": True, "matches": len(results)},
        )

        return json.dumps({
            "success": True,
            "matches": len(results),
            "elements": results,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Find element failed")})


# ═══════════════════════════════════════════════════════════════
#  NEW TOOLS — Window Management, Clipboard, Smart Targeting
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def get_active_window() -> str:
    """Get information about the currently focused window.

    Returns the window title, bounds, process name, and state
    (maximized/minimized). Use this to understand context before
    performing UI actions.

    Returns:
        JSON with active window information.
    """
    try:
        info = _get_active_window()
        if info is None:
            return json.dumps({"success": False, "message": "Could not determine active window"})

        session = get_session()
        session.log_action("get_active_window", {}, {"success": True, "title": info.title})

        return json.dumps({"success": True, **info.to_dict()})

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Get active window failed")})


@mcp.tool()
def list_windows(include_hidden: bool = False) -> str:
    """List all open windows with their titles, bounds, and process info.

    Use this to find windows before focusing, resizing, or interacting
    with them.

    Args:
        include_hidden: If True, include non-visible windows.

    Returns:
        JSON with list of all open windows.
    """
    try:
        windows = _list_windows(include_hidden=include_hidden)

        session = get_session()
        session.log_action("list_windows", {"include_hidden": include_hidden}, {"success": True, "count": len(windows)})

        return json.dumps({
            "success": True,
            "count": len(windows),
            "windows": [w.to_dict() for w in windows],
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "List windows failed")})


@mcp.tool()
def focus_window(title: str) -> str:
    """Bring a window to the foreground by title.

    Uses case-insensitive substring matching. For example,
    focus_window("notepad") will focus any window with "notepad"
    in its title.

    Args:
        title: Substring to match against window titles.

    Returns:
        JSON with success status and matched window title.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        result = _focus_window(title)

        session = get_session()
        session.log_action("focus_window", {"title": title}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Focus window failed")})


@mcp.tool()
def resize_window(title: str, width: int, height: int) -> str:
    """Resize a window to specific dimensions.

    Args:
        title: Substring to match against window titles.
        width: New width in pixels.
        height: New height in pixels.

    Returns:
        JSON with success status.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        result = _resize_window(title, width, height)

        session = get_session()
        session.log_action("resize_window", {"title": title, "width": width, "height": height}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Resize window failed")})


@mcp.tool()
def move_window(title: str, x: int, y: int) -> str:
    """Move a window to a new position on screen.

    Args:
        title: Substring to match against window titles.
        x: New left edge position in pixels.
        y: New top edge position in pixels.

    Returns:
        JSON with success status.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        result = _move_window(title, x, y)

        session = get_session()
        session.log_action("move_window", {"title": title, "x": x, "y": y}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Move window failed")})


@mcp.tool()
def minimize_maximize(title: str, state: str = "maximize") -> str:
    """Minimize, maximize, or restore a window.

    Supports all three states despite the tool name (historical).

    Args:
        title: Substring to match against window titles.
        state: Target window state — "minimize", "maximize", or "restore".

    Returns:
        JSON with success status.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        result = _min_max_window(title, state)

        session = get_session()
        session.log_action("minimize_maximize", {"title": title, "action": state}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Minimize/maximize failed")})


@mcp.tool()
def launch_app(
    app: str,
    arguments: str = "",
    wait_ms: int = 2000,
) -> str:
    """Launch an application by name or path.

    Supports common names: "notepad", "chrome", "calc", "code",
    "explorer", "paint", "terminal" — or a full path to any executable.
    After launching, use wait_for_element() to confirm the window appeared.

    Args:
        app: Application name (e.g. "notepad", "chrome") or full path to executable.
        arguments: Optional command-line arguments to pass.
        wait_ms: Time to wait after launch for the process to start (ms).

    Returns:
        JSON with success status and process info.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        result = _launch_app(app, arguments, wait_ms)

        session = get_session()
        session.log_action("launch_app", {"app": app, "args": arguments}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, f"Launch app failed: {app}")})


@mcp.tool()
def open_url(url: str, profile_name: str | None = None) -> str:
    """Open a URL in the default web browser.

    Args:
        url: The URL to open. Will auto-prepend https:// if no scheme.
        profile_name: Optional profile name to open the URL with (e.g. 'Chris').

    Returns:
        JSON with success status.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        result = _open_url(url, profile_name=profile_name)

        session = get_session()
        session.log_action("open_url", {"url": url, "profile_name": profile_name}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Open URL failed")})


@mcp.tool()
def execute_actions(actions: list[dict]) -> list[t.TextContent | t.ImageContent]:
    """Execute multiple UI actions sequentially in a single tool call.

    More efficient than individual tool calls when doing a known sequence —
    fewer round-trips, single verification. Execution stops at the first failure.

    Tradeoff vs individual tool calls:
      - Individual calls give you per-step error recovery (fix + retry).
      - execute_actions is best for sequences where any failure should abort
        (e.g. "click File → click Save → type filename").
      - For simple 1-2 step tasks, individual tools are clearer.

    Args:
        actions: A list of action dicts. Each must have an "action" key.
            Supported actions:
            - {"action": "click", "x": 100, "y": 200, "button": "left", "count": 1}
            - {"action": "type", "text": "hello", "delay_ms": 0}
            - {"action": "hotkey", "keys": ["ctrl", "s"]}
            - {"action": "wait", "duration_ms": 1000}
            - {"action": "scroll", "x": 100, "y": 200, "direction": "down", "amount": 3}
            - {"action": "drag", "start_x": 100, "start_y": 100, "end_x": 200, "end_y": 200}
            - {"action": "hover", "x": 100, "y": 200, "duration_ms": 500}
            - {"action": "clipboard", "mode": "write", "text": "hello"}
            - {"action": "clipboard", "mode": "read"}
            - {"action": "focus_window", "title": "Chrome"}
            - {"action": "launch", "name_or_path": "notepad"}
            - {"action": "screenshot"}

    Returns:
        List: [TextContent (JSON metadata with per-step results), ImageContent (final screenshot)].
    """
    killed = _check_sentinel()
    if killed:
        return [t.TextContent(type="text", text=killed)]
    
    from uacc.actions.schema import parse_action
    
    executor = _get_executor()
    results = []
    session = get_session()
    last_mouse_pos: tuple[int, int] | None = None
    
    def get_final_result(success: bool, error: str | None = None, executed: int = 0):
        try:
            img = capture_full()
            b64 = image_to_base64(img, fmt="JPEG", quality=80)
            media_type = get_image_media_type("JPEG")
            img_content = t.ImageContent(type="image", data=b64, mimeType=media_type)
        except Exception as e:
            img_content = t.TextContent(type="text", text=f"Failed to capture final screenshot: {e}")
            
        metadata = {
            "success": success,
            "results": results,
            "actions_executed": executed,
        }
        if error:
            metadata["error"] = error
            
        return [
            t.TextContent(type="text", text=json.dumps(metadata)),
            img_content
        ]

    for idx, act_dict in enumerate(actions):
        try:
            # Map tool name aliases if present to match backend Action schema definitions
            if act_dict.get("action") == "launch_app":
                act_dict["action"] = "launch"
            elif act_dict.get("action") == "clipboard_write":
                act_dict["action"] = "clipboard"
                act_dict["mode"] = "write"
            elif act_dict.get("action") == "clipboard_read":
                act_dict["action"] = "clipboard"
                act_dict["mode"] = "read"
                
            action_obj = parse_action(act_dict)
        except Exception as exc:
            err_msg = f"Failed to parse action at index {idx}: {exc}"
            logger.error(err_msg)
            return get_final_result(success=False, error=err_msg, executed=idx)
        
        res = executor.execute(action_obj)
        results.append(res)
        session.log_action(f"batch_{action_obj.action}", act_dict, res)

        # Track last mouse position for sentinel
        action_name = action_obj.action
        if res.get("success"):
            if action_name in ("click", "hover", "scroll"):
                last_mouse_pos = (act_dict.get("x", 0), act_dict.get("y", 0))
            elif action_name == "drag":
                last_mouse_pos = (act_dict.get("end_x", 0), act_dict.get("end_y", 0))
            elif action_name == "click_element":
                last_mouse_pos = (act_dict.get("click_x", 0), act_dict.get("click_y", 0))
        
        if not res.get("success", False):
            err_msg = f"Action at index {idx} ({action_obj.action}) failed: {res.get('message', '')}"
            return get_final_result(success=False, error=err_msg, executed=idx + 1)
            
    if last_mouse_pos is not None:
        _get_sentinel().set_expected_position(*last_mouse_pos)
    return get_final_result(success=True, executed=len(actions))


@mcp.tool()
def clipboard_read() -> str:
    """Read the current clipboard text content.

    Useful for extracting text that was copied to clipboard,
    either by the agent (via Ctrl+C) or by the user.

    Returns:
        JSON with clipboard text content.
    """
    try:
        result = _clipboard_read()

        session = get_session()
        session.log_action("clipboard_read", {}, {"success": result["success"], "length": result.get("length", 0)})

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Clipboard read failed")})


@mcp.tool()
def clipboard_write(text: str) -> str:
    """Write text to the clipboard.

    The text can then be pasted into any application using
    Ctrl+V or the hotkey tool.

    Args:
        text: The text to place on the clipboard.

    Returns:
        JSON with success status.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        result = _clipboard_write(text)

        session = get_session()
        session.log_action("clipboard_write", {"length": len(text)}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Clipboard write failed")})


@mcp.tool()
def get_mouse_position() -> str:
    """Get the current mouse cursor position.

    Returns:
        JSON with x, y coordinates of the mouse cursor.
    """
    try:
        result = _get_mouse_position()

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Get mouse position failed")})


@mcp.tool()
def wait_for_element(
    name: str,
    element_type: str | None = None,
    timeout_ms: int = 10000,
    poll_interval_ms: int = 500,
) -> str:
    """Wait until a UI element appears on screen.

    Polls the screen repeatedly until an element matching the name
    (and optionally type) appears. This is CRITICAL for reliability —
    use it after any action that triggers a UI change.

    Examples:
    - After launching an app: wait_for_element("Untitled - Notepad")
    - After clicking a menu: wait_for_element("Save As", element_type="menu_item")
    - After navigating: wait_for_element("Submit", element_type="button")

    Args:
        name: Text to search for in element labels (fuzzy match).
        element_type: Optional type filter (button, menu_item, text_input, etc.).
        timeout_ms: Maximum time to wait (default 10 seconds).
        poll_interval_ms: Time between screen scans (default 500ms).

    Returns:
        JSON with found element info or timeout message.
    """
    try:
        result = _wait_for_element(
            name=name,
            element_type=element_type,
            timeout_ms=timeout_ms,
            poll_interval_ms=poll_interval_ms,
        )

        session = get_session()
        session.log_action(
            "wait_for_element",
            {"name": name, "element_type": element_type, "timeout_ms": timeout_ms},
            {"success": True, "found": result.get("found", False)},
        )

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Wait for element failed")})


@mcp.tool()
def click_element(
    name: str,
    element_type: str | None = None,
    button: str = "left",
    reasoning: str = "",
) -> str:
    """Find a UI element by visible label and click it.

    PREFERRED click method for named elements — uses fuzzy text matching
    on the accessibility tree. For example, click_element("Save") finds the
    "Save" button wherever it is on screen.

    When to use alternatives:
      - click(x, y) — when you have exact pixel coordinates (from find_element results).
      - click(target="name") — like click_element but falls back to smart_click if no match.
      - smart_click(target) — multi-strategy (a11y → OCR → VLM → vision) for difficult targets.

    Args:
        name: Text to search for in element labels (case-insensitive fuzzy substring match).
              Examples: "File", "Save", "OK", "Submit", "Cancel", "Close".
        element_type: Optional type filter (button, menu_item, text_input, checkbox, etc.).
        button: Mouse button — "left", "right", or "middle".
        reasoning: Why you're clicking this element (logged for debugging).

    Returns:
        JSON with success, matched element info, and coordinates.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        find_result = click_element_by_name(
            name=name,
            element_type=element_type,
            button=button,
        )

        if not find_result["success"]:
            return json.dumps(find_result)

        # Execute the actual click
        click_x = find_result["click_x"]
        click_y = find_result["click_y"]

        action = ClickAction(
            x=click_x,
            y=click_y,
            button=MouseButton(button),
            count=1,
            reasoning=reasoning or f"Clicking element '{name}'",
        )

        executor = _get_executor()
        exec_result = executor.execute(action)

        if exec_result.get("success"):
            _get_sentinel().set_expected_position(click_x, click_y)
            invalidate_tree_cache()

        session = get_session()
        session.log_action(
            "click_element",
            {"name": name, "element_type": element_type, "reasoning": reasoning},
            {"success": exec_result["success"], "x": click_x, "y": click_y},
        )

        return json.dumps({
            "success": exec_result["success"],
            "message": f"Clicked '{find_result['element']['name']}' at ({click_x}, {click_y})",
            "element": find_result["element"],
            "coordinates": {"x": click_x, "y": click_y},
            "alternatives": find_result.get("alternatives", []),
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Click element failed")})


@mcp.tool()
def get_action_history(count: int = 20) -> str:
    """Get the recent action history log.

    Returns the last N actions performed through the MCP server,
    useful for debugging and understanding what has been done.

    Args:
        count: Number of recent actions to return (default 20).

    Returns:
        JSON with list of recent actions.
    """
    try:
        session = get_session()
        actions = session.get_recent_actions(count)

        return json.dumps({
            "success": True,
            "count": len(actions),
            "actions": actions,
        }, default=str)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Get action history failed")})


@mcp.tool()
def paint_preset(preset_name: str) -> str:
    """Paint a preset design on screen inside MS Paint.

    Launches Paint, matches the screen, and draws beautiful geometric
    preset designs using vector brush strokes.

    Args:
        preset_name: The design to draw ("rose", "galaxy", "mountains", "house", "peacock").

    Returns:
        JSON with success status and drawing stroke details.
    """
    try:
        # Reset stale sentinel anchor so a leftover expected position from a
        # previous tool call can't false-kill the drawing before it starts.
        _reset_sentinel_anchor()

        # 1. Launch / focus Paint and maximize
        _launch_app("mspaint", wait_ms=1000)
        from uacc.core.window_manager import minimize_maximize_window
        minimize_maximize_window("Paint", "maximize")
        time.sleep(0.5)

        # 2. Get screen dimensions to find canvas center inside white drawing area
        screen_w, screen_h = get_screen_size()
        cx = screen_w // 2
        cy = max(300, int(screen_h * 0.55))  # Shifted down to clear top ribbon

        # 3. Instantiate painter and draw
        painter = ArtisticPainter(executor=_get_executor(), sentinel=_get_sentinel())
        try:
            result = painter.draw_preset(preset_name, (cx, cy))
        finally:
            painter.cleanup()
            # Bring Paint to the front so the finished drawing is actually visible.
            _bring_paint_to_front()

        session = get_session()
        session.log_action("paint_preset", {"preset": preset_name}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Paint preset failed")})


@mcp.tool()
def paint_image(image_path: str, max_strokes: int = 500) -> str:
    """Sketch the outline of an image file on screen inside MS Paint.

    Launches Paint, loads the image from disk, extracts its outline
    contours using edge detection, and draws the sketch using UACC's
    brush stroke coordinates.

    Args:
        image_path: Absolute path to the source image file to sketch.
        max_strokes: Maximum brush strokes to draw (default 500).

    Returns:
        JSON with success status and drawing stroke details.
    """
    try:
        # Reset stale sentinel anchor so a leftover expected position from a
        # previous tool call can't false-kill the drawing before it starts.
        _reset_sentinel_anchor()

        # 1. Launch / focus Paint and maximize window
        _launch_app("mspaint", wait_ms=1000)
        from uacc.core.window_manager import minimize_maximize_window
        minimize_maximize_window("Paint", "maximize")
        time.sleep(0.5)

        # 2. Determine canvas coordinates safely inside Paint window bounds
        from uacc.core.window_manager import list_windows
        paint_win = None
        for w in list_windows():
            if "paint" in w.title.lower():
                paint_win = w
                break

        if paint_win and paint_win.bounds:
            l, t, r, b = paint_win.bounds
            win_w, win_h = max(100, r - l), max(100, b - t)
            left = l + max(140, int(win_w * 0.08))
            top = t + max(240, int(win_h * 0.24))
            right = l + min(win_w - 80, int(win_w * 0.94))
            bottom = t + min(win_h - 80, int(win_h * 0.90))
        else:
            screen_w, screen_h = get_screen_size()
            left = max(140, int(screen_w * 0.08))
            top = max(240, int(screen_h * 0.24))
            right = min(screen_w - 80, int(screen_w * 0.94))
            bottom = min(screen_h - 80, int(screen_h * 0.90))

        canvas_bounds = (left, top, right, bottom)

        # 3. Paint image outlines with optimized execution
        from uacc.actions.artistic_painter import ArtisticPainter as FastPainter
        painter = FastPainter(executor=_get_executor(), sentinel=_get_sentinel())
        try:
            result = painter.draw_image(image_path, canvas_bounds, max_strokes=max_strokes)
        finally:
            painter.cleanup()
            # Bring Paint to the front so the finished drawing is actually visible.
            _bring_paint_to_front()

        session = get_session()
        session.log_action("paint_image", {"image_path": image_path, "max_strokes": max_strokes}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Paint image failed")})


@mcp.tool()
def fetch_image(query: str, output_path: str = "", source: str = "auto") -> str:
    """Fetch or generate a reference image for UACC drawing tasks.

    Automatically retrieves or generates a reference image for a given topic,
    character name, landmark, or URL:
    - Generic scenes / concepts ("scenery", "house"): Uses Pollinations AI.
    - Specific subjects / figures / characters ("spiderman", "statue of liberty"): Uses Web Search.
    - Direct URLs ("https://..."): Downloads directly.

    Args:
        query: Subject, character name, scene description, or image URL.
        output_path: Optional file path to save image (defaults to ~/.uacc/images/).
        source: Retrieval strategy ('auto', 'pollinations', 'web', 'url').

    Returns:
        JSON with success status, image_path, width, height, and source used.
    """
    try:
        from uacc.tools.fetch_image import fetch_reference_image
        res = fetch_reference_image(query, output_path=output_path if output_path else None, source=source)
        session = get_session()
        session.log_action("fetch_image", {"query": query, "output_path": output_path, "source": source}, res)
        return json.dumps(res)
    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Fetch image failed")})


# ═══════════════════════════════════════════════════════════════
#  RESOURCES
# ═══════════════════════════════════════════════════════════════


@mcp.resource("uacc://screen/text-map")
def screen_text_map() -> str:
    """Live text map of the current screen state.

    Returns the structured text representation of all UI elements
    currently visible on the screen, including their types, labels,
    coordinates, and interactivity flags.
    """
    try:
        screen_w, screen_h = get_screen_size()
        ui_elements = get_ui_tree()

        active_window = ""
        if ui_elements and ui_elements[0].name:
            active_window = ui_elements[0].name

        text_map = build_text_map(
            screen_width=screen_w,
            screen_height=screen_h,
            ui_elements=ui_elements,
            active_window=active_window,
        )

        return text_map.to_compact_text()

    except Exception as exc:
        return f"Error reading screen: {exc}"


@mcp.resource("uacc://config")
def uacc_config() -> str:
    """Current UACC configuration.

    Returns the active configuration including mode, grid settings,
    safety mode, and action parameters.
    """
    return json.dumps(
        {
            "mode": config.uacc.mode,
            "grid_mode": config.uacc.grid_mode,
            "safe_mode": config.uacc.safe_mode,
            "max_iterations": config.uacc.max_iterations,
            "human_mimicry": config.uacc.human_mimicry,
            "action_delay_ms": config.uacc.action_delay_ms,
            "screenshot_quality": config.uacc.screenshot_quality,
        },
        indent=2,
    )


@mcp.resource("uacc://screen/active-window")
def active_window_resource() -> str:
    """Information about the currently focused window.

    Returns the window title, bounds, process name, and state.
    """
    try:
        info = _get_active_window()
        if info:
            return json.dumps(info.to_dict(), indent=2)
        return json.dumps({"error": "Could not determine active window"})
    except Exception as exc:
        return f"Error: {exc}"


@mcp.resource("uacc://history/actions")
def action_history_resource() -> str:
    """Recent action history log.

    Returns the last 50 actions performed through the MCP server,
    including tool name, parameters, results, and timestamps.
    """
    try:
        session = get_session()
        actions = session.get_recent_actions(50)
        return json.dumps({"actions": actions, "count": len(actions)}, indent=2, default=str)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.resource("uacc://system/monitors")
def monitors_resource() -> str:
    """Information about connected monitors.

    Returns the dimensions and positions of all monitors,
    useful for multi-monitor setups.
    """
    try:
        import mss
        sct = mss.mss()
        monitors = []
        for i, mon in enumerate(sct.monitors):
            monitors.append({
                "index": i,
                "left": mon["left"],
                "top": mon["top"],
                "width": mon["width"],
                "height": mon["height"],
                "is_primary": i == 1,
                "is_virtual": i == 0,
            })
        return json.dumps({"monitors": monitors, "count": len(monitors) - 1}, indent=2)
    except Exception as exc:
        return f"Error: {exc}"


# ═══════════════════════════════════════════════════════════════
#  PROMPTS
# ═══════════════════════════════════════════════════════════════


@mcp.prompt()
def computer_control_guide() -> str:
    """Best practices for controlling a computer with UACC.

    Returns a guide that teaches AI agents the optimal workflow
    for reliable desktop automation.
    """
    return """# UACC — Computer Control Best Practices

## Recommended Workflow

1. **Understand context first**
   - Call `get_active_window` to see what app is focused
   - Call `get_screen_info` to see all interactive elements
   - Call `list_windows` if you need to switch between apps

2. **Use smart targeting over raw coordinates**
   - Prefer `click_element(name="Save")` over `click(x=500, y=300)`
   - Use `find_element(name="Submit", element_type="button")` to locate elements
   - Smart targeting uses fuzzy matching — exact text isn't required

3. **Wait for UI changes**
   - After launching an app: `wait_for_element("window title")`
   - After clicking a menu: `wait_for_element("menu item name")`
   - After navigation: `wait_for_element("expected element")`
   - This is the #1 most important practice for reliability

4. **Use keyboard shortcuts when possible**
   - `hotkey(["ctrl", "s"])` is faster and more reliable than clicking Save
   - `hotkey(["ctrl", "c"])` then `clipboard_read()` to extract text
   - `hotkey(["ctrl", "v"])` after `clipboard_write(text)` to paste

5. **Manage windows efficiently**
   - `focus_window("app name")` to switch between apps
   - `launch_app("notepad")` to start applications
   - `open_url("https://example.com")` for web navigation

6. **Verify your actions**
   - Take a `screenshot` after important actions to verify the result
   - Check `get_action_history()` if you're unsure what happened

## Available Tools (25 total)

### Screen Understanding
- `screenshot` — Capture the screen
- `get_screen_info` — Get structured text map of all UI elements
- `find_element` — Search for UI elements by name/type
- `get_mouse_position` — Get current cursor position

### Mouse & Keyboard
- `click` — Click at exact coordinates
- `click_element` — Click by element name (smart targeting)
- `type_text` — Type text via keyboard
- `hotkey` — Press key combinations
- `scroll` — Scroll at a position
- `drag` — Drag from point A to B
- `hover` — Move mouse and wait

### Window Management
- `get_active_window` — Get focused window info
- `list_windows` — List all open windows
- `focus_window` — Bring a window to front
- `resize_window` — Resize a window
- `move_window` — Move a window
- `minimize_maximize` — Min/max/restore a window

### Applications
- `launch_app` — Launch an application
- `open_url` — Open URL in browser

### Clipboard
- `clipboard_read` — Read clipboard text
- `clipboard_write` — Write text to clipboard

### Reliability
- `wait_for_element` — Poll until element appears (CRITICAL)
- `get_action_history` — Review recent actions

### Art & Painting
- `paint_preset` — Paint preset designs in MS Paint
- `paint_image` — Sketch outline of any image in MS Paint
- `fetch_image` — Fetch or generate reference image for drawing

"""


# ═══════════════════════════════════════════════════════════════
#  WORKFLOW MEMORY — Persistent, reusable automation sequences
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def create_workflow(
    name: str,
    description: str = "",
    steps: list[dict] | None = None,
    tags: list[str] | None = None,
) -> str:
    """Create or overwrite a reusable automation workflow.

    Workflows are persistent named sequences of MCP tool calls that
    can be replayed with `run_workflow`. Any agent can save its
    successful multi-step automation as a workflow, building up a
    library of proven UI patterns.

    Args:
        name: Unique name for the workflow (e.g. \"open_notepad_type_hello\").
        description: Human-readable description of what this workflow does.
        steps: List of step dicts, each with \"tool\" and \"params\" keys.
               Example: [{\"tool\": \"launch_app\", \"params\": {\"app\": \"notepad\"}}]
        tags: Optional tags for categorising workflows.

    Returns:
        JSON with success status and workflow details.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        parsed_steps = []
        for s in (steps or []):
            parsed_steps.append(WorkflowStep(
                tool=s.get("tool", ""),
                params=s.get("params", {}),
            ))

        wf = Workflow(
            name=name,
            description=description,
            steps=parsed_steps,
            tags=tags or [],
        )

        store = get_store()
        path = store.save(wf)

        return json.dumps({
            "success": True,
            "workflow": wf.to_dict(),
            "path": str(path),
            "message": f"Workflow '{name}' created with {wf.step_count} step(s)",
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Create workflow failed")})


@mcp.tool()
def list_workflows(tag: str | None = None) -> str:
    """List all saved automation workflows.

    Args:
        tag: Optional tag to filter by (e.g. \"office\", \"notepad\", \"browser\").

    Returns:
        JSON with list of workflows (name, description, step count, run count).
    """
    try:
        store = get_store()
        workflows = store.list(tag=tag)

        return json.dumps({
            "success": True,
            "count": len(workflows),
            "workflows": workflows,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "List workflows failed")})


@mcp.tool()
def get_workflow(name: str) -> str:
    """Get the full details and steps of a saved workflow.

    Args:
        name: Name of the workflow to retrieve.

    Returns:
        JSON with workflow metadata and all step definitions.
    """
    try:
        store = get_store()
        wf = store.get(name)

        if wf is None:
            return json.dumps({
                "success": False,
                "error": f"Workflow '{name}' not found",
            })

        return json.dumps({
            "success": True,
            "workflow": wf.to_dict(),
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Get workflow failed")})


@mcp.tool()
def delete_workflow(name: str) -> str:
    """Delete a saved workflow.

    Args:
        name: Name of the workflow to delete.

    Returns:
        JSON with success status.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        store = get_store()
        existed = store.delete(name)

        return json.dumps({
            "success": existed,
            "message": f"Workflow '{name}' deleted" if existed else f"Workflow '{name}' not found",
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Delete workflow failed")})


@mcp.tool()
def run_workflow(name: str) -> str:
    """Execute a saved workflow step by step.

    Replays every step in the workflow sequentially, calling the
    corresponding MCP tool with its saved parameters. After execution,
    the workflow's run counter is incremented.

    Args:
        name: Name of the workflow to execute.

    Returns:
        JSON with execution results for every step.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        store = get_store()
        wf = store.get(name)

        if wf is None:
            return json.dumps({
                "success": False,
                "error": f"Workflow '{name}' not found",
            })

        results = []
        all_succeeded = True

        for i, step in enumerate(wf.steps):
            tool_name = step.tool
            params = step.params

            # Look up the MCP tool function from the ToolRegistry
            tool_def = ToolRegistry.get(tool_name)
            tool_fn = tool_def.handler if tool_def else None

            if tool_fn is None:
                all_succeeded = False
                results.append({
                    "step": i + 1,
                    "tool": tool_name,
                    "error": f"Unknown tool: {tool_name}",
                })
                continue

            try:
                raw = tool_fn(**params)
                # Most tools return JSON strings; parse to check success
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                step_ok = parsed.get("success", False)
                if not step_ok:
                    all_succeeded = False
                results.append({
                    "step": i + 1,
                    "tool": tool_name,
                    "success": step_ok,
                    "result": parsed,
                })
            except Exception as exc:
                all_succeeded = False
                results.append({
                    "step": i + 1,
                    "tool": tool_name,
                    "error": str(exc),
                })

        if all_succeeded:
            store.increment_run_count(name)

        return json.dumps({
            "success": all_succeeded,
            "workflow": name,
            "total_steps": len(wf.steps),
            "steps_succeeded": sum(1 for r in results if r.get("success")),
            "steps_failed": sum(1 for r in results if "error" in r or not r.get("success")),
            "results": results,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Run workflow failed")})


# ═══════════════════════════════════════════════════════════════
#  CROSS-SESSION MEMORY TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def remember_action(
    app_name: str,
    action_name: str,
    element_label: str = "",
    result: str = "success",
    reasoning: str = "",
) -> str:
    """Record a successful action in the cross-session knowledge graph.

    The graph persists across agent sessions under ``~/.uacc/semantic_graph.json``,
    enabling UACC to remember UI patterns from previous runs.

    Args:
        app_name: Application the action was performed in (e.g. "Notepad", "Chrome").
        action_name: The action performed (e.g. "click", "type", "hotkey").
        element_label: Text label of the target UI element (e.g. "Save", "Close").
        result: "success" or "failure".
        reasoning: Why this action was performed (for future recall context).

    Returns:
        JSON summary of what was recorded.
    """
    killed = _check_sentinel()
    if killed:
        return killed
    return _remember_action(app_name, action_name, element_label, result, reasoning)


@mcp.tool()
def query_knowledge(app_name: str) -> str:
    """Query what UACC knows about an application from past sessions.

    Returns known UI patterns, elements, action types, and the last-seen
    timestamp for the given application. Helps the agent understand what
    to expect when interacting with a familiar app.

    Args:
        app_name: Application name to look up (e.g. "Notepad", "Chrome").

    Returns:
        JSON with known patterns, elements, and action history.
    """
    return _query_knowledge(app_name)


@mcp.tool()
def recall_related_apps(app_name: str) -> str:
    """Find applications related to the given app via the knowledge graph.

    Uses SIMILAR_TO relationships and shared UI element patterns
    to discover related apps. Useful when the agent needs to apply
    knowledge from one app to a similar one.

    Args:
        app_name: Application name to find related apps for.

    Returns:
        JSON with a list of related applications.
    """
    return _recall_related_apps(app_name)


@mcp.tool()
def memory_summary() -> str:
    """Get statistics about the cross-session knowledge graph.

    Shows how many apps, elements, and relationships UACC has
    learned across all sessions.

    Returns:
        JSON with entity and relation counts.
    """
    return _memory_summary()


@mcp.tool()
def app_action_history(app_name: str, limit: int = 10) -> str:
    """Get the action history and reasoning for a specific application.

    Args:
        app_name: Application name (e.g. "Notepad", "Chrome").
        limit: Maximum number of history entries to return (default 10).

    Returns:
        JSON with recent actions, reasoning, and timestamps.
    """
    return _get_app_action_history(app_name, limit)


# ═══════════════════════════════════════════════════════════════
#  TOOL REGISTRY
# ═══════════════════════════════════════════════════════════════

_TOOL_REGISTRY = {}


def _populate_tool_registry() -> None:
    known_tools = [
        "screenshot", "get_screen_info", "list_monitors",
        "click", "type_text", "hotkey",
        "scroll", "drag", "hover", "find_element", "get_active_window",
        "list_windows", "focus_window", "resize_window", "move_window",
        "minimize_maximize", "launch_app", "open_url", "execute_actions",
        "clipboard_read", "clipboard_write", "get_mouse_position",
        "wait_for_element", "click_element", "get_action_history",
        "paint_preset", "paint_image", "create_workflow", "list_workflows",
        "get_workflow", "delete_workflow", "run_workflow",
        "start_task", "get_task_status", "cancel_task", "list_tasks",
        # Supreme tools
        "take_snapshot", "compare_snapshots", "get_screen_diff", "verify_action",
        "detect_elements_visual", "get_screen_info_enhanced",
        "smart_click", "smart_type",
        "find_element_relative", "find_element_near",
        "get_system_info", "list_processes",
        # VLM tools
        "vlm_analyze", "vlm_locate_element",
        # BAP — Blind Agent Protocol tools
        "uacc_query", "uacc_where_is", "uacc_expect",
        # Browser DOM Bridge (CDP)
        "browser_query", "browser_get_page_info", "browser_execute_js",
        "browser_wait_for", "browser_click", "browser_type", "browser_navigate",
        # Planning & Safety
        "uacc_planner", "acknowledge_user_override",
    ]
    # Memory tools
    supreme_tools = [
        "remember_action", "query_knowledge", "recall_related_apps",
        "memory_summary", "app_action_history",
    ]
    known_tools.extend(supreme_tools)

    for name in known_tools:
        fn = globals().get(name)
        if fn is None:
            logger.warning("Tool '%s' not found in module globals", name)
            continue
        _TOOL_REGISTRY[name] = fn
        ToolRegistry.register(ToolDef(
            name=name,
            description=getattr(fn, "__doc__", "") or "",
            handler=fn,
        ))

    logger.info("Tool registry populated: %d tools", len(_TOOL_REGISTRY))



# ═══════════════════════════════════════════════════════════════
#  TASK MANAGER (long-running operations)
# ═══════════════════════════════════════════════════════════════

_task_manager: TaskManager | None = None


def _get_task_manager() -> TaskManager:
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager(max_concurrent=5)
    return _task_manager


@mcp.tool()
def start_task(
    name: str,
    *,
    action: str = "click",
    params: str = "{}",
    iterations: int = 1,
) -> str:
    """Start a background task that performs a repetitive UI action.

    Non-blocking: the task runs in a background thread so you can continue
    working while it executes. Poll progress with get_task_status, cancel
    with cancel_task, or list all tasks with list_tasks.

    Use cases:
      - Clicking through a series of dialogs (iterations=N)
      - Repeating a hotkey sequence
      - Performing a long scroll operation
      - Any multi-step action where you don't need to wait for each step

    Args:
        name: Human-readable name for the task (e.g. "Click 50 Save buttons").
        action: The tool action to repeat (click, type_text, hotkey, scroll, etc.).
        params: JSON string of parameters for the action (e.g. '{"x": 500, "y": 300}').
        iterations: How many times to repeat the action (default: 1).

    Returns:
        JSON with task_id for status polling and cancellation.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        mgr = _get_task_manager()
        parsed_params = json.loads(params)
        executor = _get_executor()
        from uacc.actions.schema import parse_action as _parse_action

        def _run_action(cancel_flag: threading.Event) -> dict:
            for i in range(iterations):
                if cancel_flag.is_set():
                    return {"cancelled": True, "completed": i}
                # Build a proper Action from the action name + params
                action_dict = {"action": action, **parsed_params}
                action_obj = _parse_action(action_dict)
                result = executor.execute(action_obj)
                if not result.get("success", False):
                    return {
                        "completed": i,
                        "error": result.get("message", f"Action '{action}' failed at iteration {i+1}"),
                    }
            return {"completed": iterations}

        task_id = mgr.submit(name, _run_action)
        return json.dumps({"success": True, "task_id": task_id, "name": name})

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Start task failed")})


@mcp.tool()
def get_task_status(task_id: str) -> str:
    """Poll the current status of a background task started with start_task.

    Call this repeatedly to monitor progress. Returns the current state
    (pending/running/completed/failed/cancelled), progress percentage,
    and result data if the task has finished.

    Args:
        task_id: The task ID returned by start_task.

    Returns:
        JSON with status (pending/running/completed/failed/cancelled),
        progress (0.0–1.0), progress_message, and result/error.
    """
    try:
        mgr = _get_task_manager()
        task = mgr.get_status(task_id)
        if task is None:
            return json.dumps({"success": False, "error": f"Task '{task_id}' not found"})
        return json.dumps({"success": True, "task": task.to_dict()})
    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Get task status failed")})


@mcp.tool()
def cancel_task(task_id: str) -> str:
    """Cancel a running or pending background task.

    Gracefully stops the background thread and marks the task
    as cancelled. Partial results are preserved.

    Args:
        task_id: The task ID returned by start_task.

    Returns:
        JSON with cancellation status and message.
    """
    try:
        mgr = _get_task_manager()
        cancelled = mgr.cancel(task_id)
        return json.dumps({
            "success": True,
            "cancelled": cancelled,
            "message": f"Task '{task_id}' cancelled" if cancelled else f"Task '{task_id}' not running",
        })
    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Cancel task failed")})


@mcp.tool()
def list_tasks(status_filter: str = "") -> str:
    """List all background tasks, optionally filtered by status.

    Args:
        status_filter: Optional filter: "pending", "running", "completed", "failed", "cancelled".

    Returns:
        JSON array of task summaries.
    """
    try:
        mgr = _get_task_manager()
        status_enum = TaskStatus(status_filter) if status_filter else None
        tasks = mgr.list_tasks(status_filter=status_enum)
        return json.dumps({"success": True, "tasks": tasks, "count": len(tasks)})
    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "List tasks failed")})


@mcp.tool()
def uacc_planner(
    task_description: str,
    target_app: str = "",
    speed_mode: str = "fast",
) -> str:
    """UACC Planner — LLM-powered goal decomposition and tool sequencing.

    Analyzes a natural language goal and produces a structured execution plan:
    - Breaks the goal into atomic steps
    - Assigns the optimal UACC tool to each step
    - Includes verification checkpoints (in ``thorough`` mode)
    - Uses the cross-session knowledge graph for app-specific context
    - Falls back to heuristic decomposition when no LLM is configured

    Call this MANDATORY tool BEFORE any UACC interaction sequence.

    Args:
        task_description: The goal or action the agent wants to perform on screen (e.g. "Open Notepad and type Hello World", "Click the Save button in Chrome").
        target_app: Optional target application name for context (e.g. "paint", "chrome", "notepad").
        speed_mode: Planning mode — "fast" (direct execution, minimal verification) or "thorough" (with UI verification steps between every action).

    Returns:
        JSON object containing the decomposed plan with steps, tool params, reasoning, and estimated duration.
    """
    try:
        decomposer = GoalDecomposer()
        plan = decomposer.decompose(
            task_description=task_description,
            target_app=target_app,
            speed_mode=speed_mode,
        )
        return json.dumps({"success": True, "plan": plan}, indent=2)
    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Planner failed")})


# ═══════════════════════════════════════════════════════════════
#  SCREEN DIFF & ACTION VERIFICATION
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def take_snapshot(name: str) -> str:
    """Save a named screenshot in memory for later comparison.

    Stores the screenshot internally — does NOT return an image (use screenshot()
    when you need a visual). Call BEFORE an action, then use compare_snapshots()
    or verify_action() AFTER to detect changes.

    Args:
        name: Descriptive name for this snapshot (e.g. "before_click",
              "after_save", "initial_state"). Must be unique per session.

    Returns:
        JSON with success, snapshot name, and screen dimensions.
    """
    try:
        img = capture_full()
        session = get_session()
        session.snapshots[name] = img
        session.log_action("take_snapshot", {"name": name}, {"success": True})

        snapshots = list(session.snapshots.keys())
        return json.dumps({
            "success": True,
            "name": name,
            "width": img.size[0],
            "height": img.size[1],
            "total_snapshots": len(snapshots),
            "available_snapshots": snapshots[-10:],  # only last 10 to keep responses small
        })
    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Take snapshot failed")})


@mcp.tool()
def compare_snapshots(
    before_name: str,
    after_name: str = "",
    sensitivity: float = 0.5,
) -> str:
    """Compare two named snapshots to detect what changed on screen.

    If after_name is omitted, captures the current screen as the "after" state.
    Returns both pixel-level and semantic (text/element) differences.

    Args:
        before_name: Name of the "before" snapshot (taken with take_snapshot).
        after_name: Name of the "after" snapshot. If empty, captures current screen.
        sensitivity: Change threshold as percentage (0.0 = any pixel, 100.0 = total).

    Returns:
        JSON with changed status, changed_percentage, changed_regions, and semantic_diff.
    """
    try:
        from uacc.core.screen_diff import compute_diff

        session = get_session()
        before_img = session.snapshots.get(before_name)
        if before_img is None:
            return json.dumps({
                "success": False,
                "error": f"Snapshot '{before_name}' not found. Available: {list(session.snapshots.keys())}",
            })

        if after_name:
            after_img = session.snapshots.get(after_name)
            if after_img is None:
                return json.dumps({
                    "success": False,
                    "error": f"Snapshot '{after_name}' not found. Available: {list(session.snapshots.keys())}",
                })
        else:
            after_img = capture_full()

        # Get text maps for semantic diff
        before_text = None
        after_text = None
        before_title = ""
        after_title = ""
        try:
            _, _, before_tm, before_title = _scan_screen()
            before_text = before_tm.to_compact_text()
        except Exception:
            pass
        try:
            _, _, after_tm, after_title = _scan_screen()
            after_text = after_tm.to_compact_text()
        except Exception:
            pass

        diff_result = compute_diff(
            before_img, after_img,
            before_text_map=before_text,
            after_text_map=after_text,
            before_window_title=before_title,
            after_window_title=after_title,
        )

        regions_json = []
        for r in diff_result.regions[:10]:
            regions_json.append({
                "bounds": {"left": r.bounds[0], "top": r.bounds[1], "right": r.bounds[2], "bottom": r.bounds[3]},
                "size": f"{r.width}×{r.height}",
                "pixel_count": r.pixel_count,
                "intensity": r.change_intensity,
            })

        semantic_json = {}
        if diff_result.semantic:
            s = diff_result.semantic
            semantic_json = {
                "changed": s.changed,
                "window_title_changed": s.window_title_changed,
                "window_before": s.window_title_before,
                "window_after": s.window_title_after,
                "text_added": s.text_added[:10],
                "text_removed": s.text_removed[:10],
                "element_count_changed": s.element_count_changed,
                "elements_before": s.element_count_before,
                "elements_after": s.element_count_after,
                "summary": s.summary,
            }

        session.log_action("compare_snapshots", {
            "before": before_name, "after": after_name or "(current)",
        }, {"success": True, "changed": diff_result.changed})

        return json.dumps({
            "success": True,
            "changed": diff_result.changed,
            "changed_percentage": diff_result.changed_percentage,
            "total_pixels_changed": diff_result.total_pixels_changed,
            "changed_regions": regions_json,
            "semantic_diff": semantic_json,
            "summary": diff_result.summary,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Compare snapshots failed")})


@mcp.tool()
def get_screen_diff(
    sensitivity: float = 0.5,
) -> list[t.TextContent | t.ImageContent]:
    """Capture a screenshot, compare to the last snapshot, and return a visual diff.

    Automatically compares the current screen against the most recent
    snapshot taken with take_snapshot(). Returns both the diff analysis
    and a visual overlay image highlighting changed regions.

    Args:
        sensitivity: Minimum percentage of pixels that must differ to count as changed.

    Returns:
        JSON diff analysis and an annotated image with changed regions highlighted in red.
    """
    try:
        from uacc.core.screen_diff import compute_diff, create_diff_visualization

        session = get_session()
        if not session.snapshots:
            return [t.TextContent(type="text", text=json.dumps({
                "success": False,
                "error": "No snapshots taken yet. Call take_snapshot() first.",
            }))]

        # Use most recent snapshot as "before"
        last_name = list(session.snapshots.keys())[-1]
        before_img = session.snapshots[last_name]
        after_img = capture_full()

        diff_result = compute_diff(before_img, after_img)

        # Create visual overlay
        viz_img = create_diff_visualization(before_img, after_img, diff_result)
        b64 = image_to_base64(viz_img, fmt="JPEG", quality=85)
        media_type = get_image_media_type("JPEG")

        regions_json = []
        for r in diff_result.regions[:10]:
            regions_json.append({
                "bounds": {"left": r.bounds[0], "top": r.bounds[1], "right": r.bounds[2], "bottom": r.bounds[3]},
                "size": f"{r.width}×{r.height}",
                "intensity": r.change_intensity,
            })

        session.log_action("get_screen_diff", {"snapshot": last_name}, {
            "success": True, "changed": diff_result.changed,
        })

        return [
            t.TextContent(type="text", text=json.dumps({
                "success": True,
                "compared_against": last_name,
                "changed": diff_result.changed,
                "changed_percentage": diff_result.changed_percentage,
                "changed_regions": regions_json,
                "summary": diff_result.summary,
            })),
            t.ImageContent(type="image", data=b64, mimeType=media_type),
        ]

    except Exception as exc:
        return [t.TextContent(type="text", text=json.dumps({
            "success": False, "error": format_error(exc, "Screen diff failed"),
        }))]


@mcp.tool()
def verify_action(
    expected_change: str = "",
    expected_text: str = "",
    timeout_ms: int = 2000,
) -> str:
    """Verify that the last action had the expected effect on screen.

    Compares the current screen against the most recent snapshot to check
    if the expected change occurred. Call take_snapshot() BEFORE your action,
    then verify_action() AFTER.

    Args:
        expected_change: Type of change expected — "any", "window_changed",
                        "text_appeared", "elements_changed", "dialog_opened".
        expected_text: Specific text that should now be visible on screen.
        timeout_ms: How long to wait for the change to appear (default 2s).

    Returns:
        JSON with verified status, what changed, and confidence assessment.
    """
    try:
        from uacc.core.screen_diff import compute_diff

        session = get_session()
        if not session.snapshots:
            return json.dumps({
                "success": False,
                "error": "No snapshots taken. Call take_snapshot() before your action, then verify_action() after.",
            })

        last_name = list(session.snapshots.keys())[-1]
        before_img = session.snapshots[last_name]

        # Poll until change detected or timeout
        import time as _time
        deadline = _time.time() + timeout_ms / 1000
        verified = False
        diff_result = None
        after_text = ""
        after_title = ""

        while _time.time() < deadline:
            after_img = capture_full()

            # Get current screen state for semantic comparison
            try:
                _, _, after_tm, after_title = _scan_screen()
                after_text = after_tm.to_compact_text()
            except Exception:
                pass

            diff_result = compute_diff(before_img, after_img, after_text_map=after_text, after_window_title=after_title)

            # Check based on expected_change type
            if expected_change == "any" and diff_result.changed:
                verified = True
                break
            elif expected_change == "window_changed" and diff_result.semantic and diff_result.semantic.window_title_changed:
                verified = True
                break
            elif expected_change == "text_appeared" and expected_text:
                if expected_text.lower() in after_text.lower():
                    verified = True
                    break
            elif expected_change == "elements_changed" and diff_result.semantic and diff_result.semantic.element_count_changed:
                verified = True
                break
            elif expected_change == "dialog_opened" and diff_result.semantic:
                sem = diff_result.semantic
                if sem.element_count_changed and sem.element_count_after > sem.element_count_before:
                    verified = True
                    break
            elif not expected_change and diff_result.changed:
                verified = True
                break

            _time.sleep(0.25)

        # Build result
        result = {
            "success": True,
            "verified": verified,
            "compared_against_snapshot": last_name,
            "expected_change": expected_change or "any",
        }

        if diff_result:
            result["screen_changed"] = diff_result.changed
            result["changed_percentage"] = diff_result.changed_percentage
            result["summary"] = diff_result.summary
            if diff_result.semantic:
                result["semantic_summary"] = diff_result.semantic.summary

        if expected_text:
            result["expected_text_found"] = expected_text.lower() in after_text.lower()

        if not verified:
            result["recommendation"] = (
                "Action may not have had the expected effect. "
                "Consider retrying or using a different approach."
            )

        session.log_action("verify_action", {
            "expected_change": expected_change, "expected_text": expected_text,
        }, {"success": True, "verified": verified})

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Verify action failed")})


# ═══════════════════════════════════════════════════════════════
#  VISION DETECTOR — OmniParser-style fallback for apps without a11y
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def detect_elements_visual(
    region_x: int | None = None,
    region_y: int | None = None,
    width: int | None = None,
    height: int | None = None,
    min_confidence: float = 0.3,
) -> str:
    """Detect UI elements using computer vision (OCR + edge detection).

    FALLBACK when get_screen_info returns few elements — for games, remote
    desktop, canvas-based web apps, or broken accessibility trees.

    Combines OCR text detection with contour analysis to find buttons,
    inputs, and labels the accessibility tree misses.

    Note: get_screen_info_enhanced(mode="auto") tries the accessibility tree
    first and falls back to vision automatically. Use this tool directly when
    you know the app has no useful a11y tree.

    Args:
        region_x: Optional left edge of scan region (full screen if omitted).
        region_y: Optional top edge of scan region.
        width: Width of scan region.
        height: Height of scan region.
        min_confidence: Minimum detection confidence (0.0–1.0).

    Returns:
        JSON with detected elements, their types, labels, and coordinates.
    """
    try:
        from uacc.core.vision_detector import full_vision_detect

        if region_x is not None and region_y is not None and width and height:
            img = capture_region(region_x, region_y, width, height)
        else:
            img = capture_full()

        elements = full_vision_detect(img)

        # Filter by confidence (approximate from source)
        results = []
        for el in elements:
            entry = {
                "id": el.id,
                "type": el.element_type,
                "text": el.text,
                "center": {"x": el.center[0], "y": el.center[1]},
                "bounds": {
                    "left": el.bounds[0], "top": el.bounds[1],
                    "right": el.bounds[2], "bottom": el.bounds[3],
                },
                "clickable": el.clickable,
                "editable": el.editable,
                "source": getattr(el, "source", "vision"),
            }
            # Offset coordinates if scanning a region
            if region_x is not None and region_y is not None:
                entry["center"]["x"] += region_x
                entry["center"]["y"] += region_y
                entry["bounds"]["left"] += region_x
                entry["bounds"]["top"] += region_y
                entry["bounds"]["right"] += region_x
                entry["bounds"]["bottom"] += region_y
            results.append(entry)

        session = get_session()
        session.log_action("detect_elements_visual", {
            "region": f"({region_x},{region_y},{width},{height})" if region_x is not None else "full",
        }, {"success": True, "elements": len(results)})

        return json.dumps({
            "success": True,
            "method": "vision (OCR + contour detection)",
            "element_count": len(results),
            "elements": results,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Visual detection failed")})


@mcp.tool()
def get_screen_info_enhanced(
    mode: str = "auto",
    include_ocr: bool = False,
) -> str:
    """Screen analysis that auto-selects the best detection method for the app.

    Slower than get_screen_info() but catches elements that the accessibility tree
    misses (games, canvas-based apps, remote desktop, video editors).

    Modes (pick the cheapest that works):
    - "accessibility" — fastest, most reliable. Use when the target app has a normal UI.
    - "auto" (default) — tries a11y first; falls back to vision if < 5 elements found.
    - "vision" — OCR + edge detection. Use for games, canvas, remote desktop.
    - "hybrid" — merges a11y + vision results for maximum coverage.
    - "vlm" — Vision Language Model. Slowest, API cost. Use only for complex visual understanding.

    See also:
      - get_screen_info() — faster but a11y-only (for normal apps).
      - detect_elements_visual() — pure vision, no a11y.

    Args:
        mode: Detection mode — "accessibility", "auto", "vision", "hybrid", or "vlm".
        include_ocr: If True, also run OCR in accessibility mode (slower but more text).

    Returns:
        JSON with screen elements, method used, and element count.
    """
    try:
        from uacc.core.vision_detector import full_vision_detect

        session = get_session()
        screen_w, screen_h = get_screen_size()
        all_elements = []
        method_used = mode

        # Accessibility tree scan
        a11y_elements = []
        if mode in ("auto", "accessibility", "hybrid"):
            try:
                _, _, text_map, active_window = _scan_screen(include_ocr=include_ocr)
                a11y_elements = text_map.all_elements
            except Exception as exc:
                logger.warning("Accessibility scan failed: %s", exc)

        # VLM scan
        vlm_elements = []
        if mode == "vlm":
            try:
                from uacc.core.vlm_engine import get_vlm_engine
                engine = get_vlm_engine()
                if engine.is_available():
                    img = capture_full()
                    vlm_raw = engine.detect_elements(img)
                    from uacc.core.text_map import ScreenElement
                    for i, ve in enumerate(vlm_raw):
                        clickable = ve.element_type in ("button", "link", "tab", "menu_item", "checkbox", "radio", "icon", "dropdown", "combobox", "list_item")
                        editable = ve.element_type in ("text_input", "input", "combobox", "slider")
                        vlm_elements.append(ScreenElement(
                            id=f"vlm_{i}", element_type=ve.element_type,
                            text=ve.text, bounds=ve.bounds,
                            center=ve.center, clickable=clickable,
                            editable=editable, source="vlm",
                        ))
                    method_used = "vlm"
                else:
                    method_used = "vlm (not configured)"
            except Exception as exc:
                logger.warning("VLM scan failed: %s", exc)
                method_used = "vlm (failed)"

        # Vision scan (fallback for auto/hybrid, exclusive for vision mode)
        vision_elements = []
        if mode == "vision" or mode == "hybrid" or (mode == "auto" and len(a11y_elements) < 5):
            try:
                img = capture_full()
                vision_elements = full_vision_detect(img)
                if mode == "auto":
                    method_used = "auto→vision (accessibility returned < 5 elements)"
            except Exception as exc:
                logger.warning("Vision scan failed: %s", exc)

        # Merge results
        if mode == "vlm":
            all_elements = vlm_elements
        elif mode == "hybrid" or (mode == "auto" and vision_elements):
            # Deduplicate: if an a11y element overlaps a vision element, keep the a11y one
            a11y_bounds = set()
            for el in a11y_elements:
                a11y_bounds.add(el.bounds)
            for vel in vision_elements:
                if vel.bounds not in a11y_bounds:
                    a11y_elements.append(vel)
            all_elements = a11y_elements
            if mode == "hybrid":
                method_used = f"hybrid (a11y: {len(a11y_elements) - len(vision_elements)}, vision: {len(vision_elements)})"
        elif mode == "vision":
            all_elements = vision_elements
        else:
            all_elements = a11y_elements

        # Format results
        results = []
        for el in all_elements:
            results.append({
                "id": el.id,
                "type": el.element_type,
                "text": el.text,
                "center": {"x": el.center[0], "y": el.center[1]},
                "bounds": {
                    "left": el.bounds[0], "top": el.bounds[1],
                    "right": el.bounds[2], "bottom": el.bounds[3],
                },
                "clickable": el.clickable,
                "editable": el.editable,
                "source": getattr(el, "source", "accessibility"),
            })

        interactive_count = sum(1 for el in all_elements if el.clickable or el.editable)

        session.log_action("get_screen_info_enhanced", {
            "mode": mode,
        }, {"success": True, "method": method_used, "elements": len(results)})

        return json.dumps({
            "success": True,
            "method": method_used,
            "screen_width": screen_w,
            "screen_height": screen_h,
            "total_elements": len(results),
            "interactive_elements": interactive_count,
            "elements": results,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Enhanced screen info failed")})


# ═══════════════════════════════════════════════════════════════
#  VISION-LANGUAGE MODEL TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def vlm_analyze(
    context: str = "",
    save_path: str | None = None,
) -> str:
    """Analyse the current screen using a Vision Language Model.

    Provides rich, structured understanding of the screen layout, visible
    applications, interactive elements, dialogs, and text content. Uses the
    configured VLM provider (OpenAI Vision, Anthropic Claude, or local).

    VLM analysis is SLOW (~1-3 s) and may cost API credits — use sparingly.
    Prefer ``get_screen_info`` or ``detect_elements_visual`` for routine tasks.

    Args:
        context: Optional task context string to help the VLM focus its analysis.
        save_path: Optional path to save the analysed screenshot.

    Returns:
        JSON with layout description, detected app, interactive elements, and text content.
    """
    try:
        from uacc.core.vlm_engine import get_vlm_engine

        engine = get_vlm_engine()
        if not engine.is_available():
            return json.dumps({
                "success": False,
                "error": "No VLM provider configured. Set UACC_VLM_* env vars or LLM API keys.",
            })

        img = capture_full()

        if save_path:
            import os
            dir_name = os.path.dirname(os.path.abspath(save_path))
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            img.save(save_path)

        analysis = engine.analyze_screenshot(img, context=context)
        if analysis is None:
            return json.dumps({"success": False, "error": "VLM analysis returned no result"})

        session = get_session()
        session.log_action("vlm_analyze", {"context": context}, {"success": True})

        return json.dumps({
            "success": True,
            "provider": engine._provider.value if hasattr(engine, '_provider') else "unknown",
            "model": engine._model if hasattr(engine, '_model') else "",
            "summary": analysis.summary,
            "layout": analysis.layout_description,
            "detected_app": analysis.detected_app,
            "interactive_count": analysis.interactive_count,
            "detected_text": analysis.detected_text,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "VLM analysis failed")})


@mcp.tool()
def vlm_locate_element(
    target: str,
) -> str:
    """Locate a specific UI element on screen using a Vision Language Model.

    Use this when standard element detection methods fail — the VLM can
    understand natural-language descriptions of elements and find them
    visually, even when they lack accessibility labels or rendered text.

    VLM location is SLOW (~1-3 s) and may cost API credits — use sparingly.
    Prefer ``find_element`` or ``smart_click`` for routine element location.

    Args:
        target: Natural language description of the element to find
                (e.g. "the red Submit button", "search input field", "profile icon").

    Returns:
        JSON with found status, element type, confidence, and screen coordinates.
    """
    try:
        from uacc.core.vlm_engine import get_vlm_engine

        engine = get_vlm_engine()
        if not engine.is_available():
            return json.dumps({
                "success": False,
                "error": "No VLM provider configured. Set UACC_VLM_* env vars or LLM API keys.",
            })

        img = capture_full()
        element = engine.locate_element(img, target)

        session = get_session()
        session.log_action("vlm_locate_element", {"target": target}, {
            "success": element is not None,
        })

        if element is None:
            return json.dumps({
                "success": True,
                "found": False,
                "target": target,
                "reason": "Element not visible on screen or could not be located",
            })

        return json.dumps({
            "success": True,
            "found": True,
            "target": target,
            "element_type": element.element_type,
            "confidence": element.confidence,
            "center": {"x": element.center[0], "y": element.center[1]},
            "bounds": {
                "left": element.bounds[0],
                "top": element.bounds[1],
                "right": element.bounds[2],
                "bottom": element.bounds[3],
            },
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "VLM locate element failed")})


# ═══════════════════════════════════════════════════════════════
#  SELF-HEALING SMART ACTIONS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def smart_click(
    target: str,
    element_type: str | None = None,
    button: str = "left",
    verify: bool = True,
    max_retries: int = 2,
    reasoning: str = "",
) -> str:
    """Self-healing click — tries multiple strategies to find and click an element.

    Best for unreliable targets (canvas apps, games, custom UI). Strategies run
    in adaptive order (learns which works best per app):
    1. Accessibility tree fuzzy match (fastest, ~200ms)
    2. OCR text search (catches rendered text a11y misses, ~500ms)
    3. VLM visual search (understands layout/icons, ~1-5s + API cost)
    4. Vision contour + OCR detection (custom UI/games, ~500ms)

    For simpler cases where you know the element name, prefer click_element().
    For exact coordinates, use click(x, y).

    Args:
        target: Text label or description of the element (case-insensitive fuzzy match).
        element_type: Optional type filter (button, menu_item, text_input, etc.).
        button: Mouse button — "left", "right", or "middle".
        verify: If True, captures before/after screenshots to confirm the click had an effect.
        max_retries: Maximum retry attempts across strategies (default 2).
        reasoning: Why you're clicking (for logging).

    Returns:
        JSON with success, method_used, attempts, coordinates, and verification.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        session = get_session()
        executor = _get_executor()
        attempts = []
        click_x, click_y = 0, 0
        method_used = ""

        # Determine current app for adaptive strategy tracking
        current_app = ""
        try:
            from uacc.core.window_manager import get_active_window as _get_win
            win = _get_win()
            if win:
                current_app = win.process_name or win.title
        except Exception:
            pass

        # Check if semantic graph knows a preferred strategy ordering for this app
        strategy_order = ["accessibility", "ocr", "vlm", "vision"]
        if current_app:
            try:
                from uacc.memory.semantic_graph import SemanticGraph
                sg = SemanticGraph()
                patterns = sg.get_app_patterns(current_app)
                if patterns:
                    # Sort strategies by success weight descending
                    strategy_scores = {s: 1.0 for s in strategy_order}
                    for rel_type, elements in patterns.get("patterns", {}).items():
                        if rel_type in ("opens", "selects"):
                            for el in elements:
                                # Check if we've recorded this element-strategy combo
                                el_id = f"{current_app.lower().replace(' ', '_')}__strategy_"
                                for s in strategy_order:
                                    sid = el_id + s
                                    sg_rels = sg.query(sid)
                                    for r in sg_rels:
                                        if r.properties.get("success"):
                                            strategy_scores[s] = strategy_scores.get(s, 1.0) + r.weight * 0.5
                                        elif r.properties.get("success") is False:
                                            strategy_scores[s] = strategy_scores.get(s, 1.0) - r.weight * 0.3
                    # Reorder by score descending (best first)
                    strategy_order = sorted(strategy_order, key=lambda s: -strategy_scores.get(s, 1.0))
            except Exception:
                pass

        # Take "before" snapshot for verification + shared fallback screenshot
        before_img = None
        fallback_img = None
        if verify:
            before_img = capture_full()
            fallback_img = before_img  # reuse for fallback strategies
        else:
            fallback_img = capture_full()  # still capture once for fallbacks

        for attempt in range(max_retries):
            strategy_name = strategy_order[attempt % len(strategy_order)]

            if strategy_name == "accessibility":
                try:
                    strat_start = time.time()
                    find_result = click_element_by_name(
                        name=target,
                        element_type=element_type,
                        button=button,
                    )
                    strat_duration = int((time.time() - strat_start) * 1000)
                    if find_result["success"]:
                        click_x = find_result["click_x"]
                        click_y = find_result["click_y"]
                        method_used = "accessibility"

                        action = ClickAction(
                            x=click_x, y=click_y,
                            button=MouseButton(button), count=1,
                            reasoning=reasoning or f"Smart click '{target}'",
                        )
                        exec_result = executor.execute(action)
                        if exec_result["success"]:
                            attempts.append({"strategy": "accessibility", "success": True})
                            if current_app:
                                _record_strategy(current_app, "accessibility", target, True, strat_duration)
                            break
                    attempts.append({"strategy": "accessibility", "success": False, "reason": find_result.get("message", "Not found")})
                    if current_app:
                        _record_strategy(current_app, "accessibility", target, False, strat_duration)
                except Exception as e:
                    attempts.append({"strategy": "accessibility", "error": str(e)})

            elif strategy_name == "ocr":
                try:
                    strat_start = time.time()
                    from uacc.core.ocr_engine import extract_text
                    ocr_results = extract_text(fallback_img)
                    target_lower = target.lower()

                    best_match = None
                    best_score = 0
                    for ocr in ocr_results:
                        text = ocr.text.strip().lower()
                        if target_lower in text or text in target_lower:
                            score = len(target_lower) / max(len(text), 1)
                            if score > best_score:
                                best_score = score
                                best_match = ocr

                    strat_duration = int((time.time() - strat_start) * 1000)
                    if best_match:
                        click_x = (best_match.bounds[0] + best_match.bounds[2]) // 2
                        click_y = (best_match.bounds[1] + best_match.bounds[3]) // 2
                        method_used = "ocr"

                        action = ClickAction(
                            x=click_x, y=click_y,
                            button=MouseButton(button), count=1,
                            reasoning=reasoning or f"Smart click (OCR) '{target}'",
                        )
                        exec_result = executor.execute(action)
                        if exec_result["success"]:
                            attempts.append({"strategy": "ocr", "success": True, "matched_text": best_match.text})
                            if current_app:
                                _record_strategy(current_app, "ocr", target, True, strat_duration)
                            break
                    attempts.append({"strategy": "ocr", "success": False, "reason": "No OCR match found"})
                    if current_app:
                        _record_strategy(current_app, "ocr", target, False, strat_duration)
                except Exception as e:
                    attempts.append({"strategy": "ocr", "error": str(e)})

            elif strategy_name == "vlm":
                try:
                    strat_start = time.time()
                    from uacc.core.vlm_engine import get_vlm_engine
                    vlm = get_vlm_engine()
                    vlm_element = vlm.locate_element(fallback_img, target)

                    strat_duration = int((time.time() - strat_start) * 1000)
                    if vlm_element:
                        click_x = vlm_element.center[0]
                        click_y = vlm_element.center[1]
                        method_used = "vlm"

                        action = ClickAction(
                            x=click_x, y=click_y,
                            button=MouseButton(button), count=1,
                            reasoning=reasoning or f"Smart click (VLM) '{target}'",
                        )
                        exec_result = executor.execute(action)
                        if exec_result["success"]:
                            attempts.append({"strategy": "vlm", "success": True, "matched_text": vlm_element.text})
                            if current_app:
                                _record_strategy(current_app, "vlm", target, True, strat_duration)
                            break
                    attempts.append({"strategy": "vlm", "success": False, "reason": "No VLM match found"})
                    if current_app:
                        _record_strategy(current_app, "vlm", target, False, strat_duration)
                except Exception as e:
                    attempts.append({"strategy": "vlm", "error": str(e)})

            elif strategy_name == "vision":
                try:
                    strat_start = time.time()
                    from uacc.core.vision_detector import full_vision_detect
                    vision_elements = full_vision_detect(fallback_img)

                    target_lower = target.lower()
                    best_el = None
                    best_score = 0
                    for el in vision_elements:
                        if el.text and target_lower in el.text.lower():
                            score = len(target_lower) / max(len(el.text), 1)
                            if score > best_score:
                                best_score = score
                                best_el = el

                    strat_duration = int((time.time() - strat_start) * 1000)
                    if best_el:
                        click_x = best_el.center[0]
                        click_y = best_el.center[1]
                        method_used = "vision"

                        action = ClickAction(
                            x=click_x, y=click_y,
                            button=MouseButton(button), count=1,
                            reasoning=reasoning or f"Smart click (vision) '{target}'",
                        )
                        exec_result = executor.execute(action)
                        if exec_result["success"]:
                            attempts.append({"strategy": "vision", "success": True, "matched_text": best_el.text})
                            if current_app:
                                _record_strategy(current_app, "vision", target, True, strat_duration)
                            break
                    attempts.append({"strategy": "vision", "success": False, "reason": "No vision match found"})
                    if current_app:
                        _record_strategy(current_app, "vision", target, False, strat_duration)
                except Exception as e:
                    attempts.append({"strategy": "vision", "error": str(e)})

        success = any(a.get("success") for a in attempts)
        if success and click_x and click_y:
            _get_sentinel().set_expected_position(click_x, click_y)

        # Verification
        verification = None
        if verify and success and before_img:
            time.sleep(0.3)  # Brief pause for UI to update
            from uacc.core.screen_diff import has_changed
            after_img = capture_full()
            screen_changed = has_changed(before_img, after_img)
            verification = {
                "screen_changed": screen_changed,
                "confidence": "high" if screen_changed else "low",
            }
            if not screen_changed:
                verification["warning"] = "Click executed but no visible screen change detected"

        session.log_action("smart_click", {
            "target": target, "method": method_used, "verify": verify,
        }, {"success": success, "attempts": len(attempts)})

        return json.dumps({
            "success": success,
            "target": target,
            "method_used": method_used,
            "coordinates": {"x": click_x, "y": click_y} if success else None,
            "attempts": attempts,
            "total_retries": len(attempts),
            "verification": verification,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Smart click failed")})


@mcp.tool()
def smart_type(
    text: str,
    target_field: str = "",
    clear_first: bool = False,
    verify: bool = True,
    reasoning: str = "",
) -> str:
    """Self-healing type — optionally finds and focuses an input field first, then types.

    If target_field is provided, uses smart_click to find and focus the field
    before typing. Optionally clears existing content first.

    Args:
        text: The text to type.
        target_field: Name of the input field to find and click first (optional).
                     If empty, types at the current cursor position.
        clear_first: If True, select all (Ctrl+A) and delete before typing.
        verify: If True, verify the text was typed by reading clipboard.
        reasoning: Why you're typing this (for logging).

    Returns:
        JSON with success status, field targeting result, and verification.
    """
    try:
        killed = _check_sentinel()
        if killed:
            return killed

        session = get_session()
        executor = _get_executor()
        field_result = None

        # Step 1: Find and focus the target field
        if target_field:
            smart_click_raw = smart_click(
                target=target_field,
                element_type="text_input",
                verify=False,
            )
            field_result = json.loads(smart_click_raw)
            if not field_result["success"]:
                # Retry without type filter
                smart_click_raw = smart_click(
                    target=target_field,
                    verify=False,
                )
                field_result = json.loads(smart_click_raw)

            if not field_result["success"]:
                return json.dumps({
                    "success": False,
                    "error": f"Could not find input field '{target_field}'",
                    "field_search": field_result,
                })
            time.sleep(0.2)

        # Step 2: Clear existing content if requested
        if clear_first:
            executor.execute(HotkeyAction(keys=["ctrl", "a"]))
            time.sleep(0.1)
            executor.execute(HotkeyAction(keys=["delete"]))
            time.sleep(0.1)

        # Step 3: Type the text
        action = TypeAction(text=text, delay_ms=0, reasoning=reasoning)
        type_result = executor.execute(action)

        # Step 4: Verify text was entered
        verification = None
        if verify and type_result["success"]:
            time.sleep(0.2)
            # Select all and copy to verify
            executor.execute(HotkeyAction(keys=["ctrl", "a"]))
            time.sleep(0.1)
            executor.execute(HotkeyAction(keys=["ctrl", "c"]))
            time.sleep(0.1)
            clip_result = _clipboard_read()
            if clip_result["success"]:
                clipboard_text = clip_result.get("text", "")
                text_found = text in clipboard_text
                verification = {
                    "text_verified": text_found,
                    "clipboard_content": clipboard_text[:200],
                }

        session.log_action("smart_type", {
            "text_length": len(text),
            "target_field": target_field,
            "clear_first": clear_first,
        }, {"success": type_result["success"]})

        return json.dumps({
            "success": type_result["success"],
            "characters_typed": len(text),
            "field_targeting": field_result,
            "clear_first": clear_first,
            "verification": verification,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Smart type failed")})


# ═══════════════════════════════════════════════════════════════
#  BAP — BLIND AGENT PROTOCOL (vision-free screen understanding)
# ═══════════════════════════════════════════════════════════════


_CDP_BRIDGE: CDPBridge | None = None


def _get_cdp_bridge() -> CDPBridge:
    global _CDP_BRIDGE
    if _CDP_BRIDGE is None:
        _CDP_BRIDGE = CDPBridge()
    return _CDP_BRIDGE


@mcp.tool()
def uacc_query(mode: str = "full") -> str:
    """Get a unified snapshot of the entire screen state in one call.

    Combines active window info, UI element tree, OCR text, clipboard state,
    cursor position, and scene graph into a single structured response.
    Designed for vision-less LLMs that need full context without multiple round-trips.

    Args:
        mode: "full" for everything, "fast" for just window + elements + scene graph,
              "minimal" for just window title and element count.

    Returns:
        JSON with screen state: windows, active_window, elements, scene_graph,
        clipboard, cursor, monitors, dom_context (if CDP available).
    """
    try:
        from uacc.core.screen_capture import capture_full, get_screen_size, list_monitors as _list_mon
        from uacc.core.window_manager import get_active_window as _get_win, list_windows as _list_win

        result: dict = {}

        active_window = _get_win()
        result["active_window"] = active_window

        if mode == "minimal":
            title = (active_window or {}).get("title", "unknown")
            elem_count = 0
            try:
                info = json.loads(get_screen_info(include_labels=False))
                elem_count = info.get("element_count", 0)
            except Exception:
                pass
            return json.dumps({
                "success": True,
                "active_window": active_window,
                "element_count": elem_count,
                "scene_graph": f"Window: {title} | {elem_count} elements",
            })

        if mode in ("full", "fast"):
            from uacc.core.accessibility import get_ui_tree, invalidate_tree_cache
            try:
                elements_raw = get_ui_tree()
                elements = [el.to_dict() if hasattr(el, 'to_dict') else el for el in (elements_raw or [])]
            except Exception:
                elements = []
            result["elements"] = elements

            screen_w, screen_h = get_screen_size()

            ocr_texts = []
            try:
                img = capture_full()
                ocr_results = _ocr_extract(img, mode="web")
                ocr_texts = [
                    {"text": r.text, "bounds": list(r.bounds), "center": list(r.center), "confidence": r.confidence}
                    for r in ocr_results
                ]
            except Exception:
                pass

            scene_graph = build_scene_graph(
                screen_w, screen_h, elements,
                active_window=active_window,
                ocr_results=ocr_texts,
            )
            result["scene_graph"] = scene_graph

            try:
                monitors = _list_mon()
                if isinstance(monitors, list):
                    result["monitors"] = monitors
            except Exception:
                pass

            clipboard_data = _clipboard_read()
            result["clipboard"] = clipboard_data

            try:
                from uacc.core.element_finder import get_mouse_position as _get_mouse
                result["cursor"] = _get_mouse()
            except Exception:
                pass

            if active_window:
                title = (active_window.get("title") or "").lower()
                process = (active_window.get("process") or "").lower()
                if any(kw in title or kw in process for kw in ("chrome", "edge", "msedge")):
                    bridge = _get_cdp_bridge()
                    if bridge.ensure_cdp():
                        try:
                            pages = bridge.list_pages()
                            if pages:
                                result["dom_context"] = {
                                    "tabs": [p.to_dict() for p in pages[:10]],
                                    "connected": True,
                                }
                        except Exception:
                            pass

            result["success"] = True
            return json.dumps(result)

        return json.dumps({"success": False, "error": f"Unknown mode: {mode}"})

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "uacc_query failed")})


@mcp.tool()
def uacc_where_is(target: str, element_type: str = "") -> str:
    """Find a UI element by description and return its precise screen coordinates.

    Searches accessibility tree → OCR text → scene graph → CDP DOM (if available).
    Returns location, confidence, and spatial context so the LLM can verify
    intent before clicking.

    Args:
        target: Description of the element to find (e.g. "Send button",
                "search input field", "Inbox").
        element_type: Optional type filter (button, text_input, link, etc.).

    Returns:
        JSON with found status, coordinates, bounds, method, confidence,
        and spatial context.
    """
    try:
        from uacc.core.accessibility import get_ui_tree
        from uacc.core.element_finder import find_element as _find_el
        from uacc.core.screen_capture import capture_full, get_screen_size

        target_lower = target.lower()

        # 1. Try accessibility tree first (fastest, most reliable)
        try:
            elements_raw = get_ui_tree()
            elements = [el.to_dict() if hasattr(el, 'to_dict') else el for el in (elements_raw or [])]
            for el in elements:
                name = (el.get("name") or el.get("text") or "").lower()
                el_type = (el.get("type") or el.get("control_type") or "").lower()
                if target_lower in name:
                    if not element_type or element_type.lower() in el_type:
                        cx, cy = el.get("center", (0, 0))
                        if isinstance(cx, dict):
                            cx, cy = cx.get("x", 0), cy.get("y", 0)
                        return json.dumps({
                            "found": True,
                            "x": cx,
                            "y": cy,
                            "bounds": el.get("bounds"),
                            "method": "accessibility_tree",
                            "confidence": 0.9,
                            "spatial_context": f"Found '{el.get('name', el.get('text', ''))}' ({el.get('type', '?')})",
                        })
        except Exception:
            pass

        # 2. Try OCR text search (catches web rendered text)
        try:
            img = capture_full()
            ocr_results = _ocr_extract(img, mode="web", confidence_threshold=0.2)
            for r in ocr_results:
                if target_lower in r.text.lower():
                    return json.dumps({
                        "found": True,
                        "x": r.center[0],
                        "y": r.center[1],
                        "bounds": list(r.bounds),
                        "method": "ocr",
                        "confidence": r.confidence,
                        "spatial_context": f"OCR found '{r.text}'",
                    })
        except Exception:
            pass

        # 3. Try find_element (UACC's built-in fuzzy matching)
        try:
            found = _find_el(name=target)
            if found:
                cx, cy = found.get("center_x", 0), found.get("center_y", 0)
                if cx or cy:
                    return json.dumps({
                        "found": True,
                        "x": cx,
                        "y": cy,
                        "method": "find_element",
                        "confidence": 0.7,
                        "spatial_context": f"Found via fuzzy match: '{target}'",
                    })
        except Exception:
            pass

        # 4. Try CDP DOM query if browser is available
        bridge = _get_cdp_bridge()
        if bridge.ensure_cdp():
            try:
                bridge.connect()
                selector_map = {
                    "button": "button, input[type='submit'], input[type='button'], [role='button']",
                    "input": "input, textarea",
                    "link": "a",
                    "search": "input[type='search'], [role='search'] input",
                }
                sel = selector_map.get(target_lower, f"*[aria-label*='{target}'], *:has-text('{target}')")
                dom_el = bridge.query_selector(sel)
                if dom_el and dom_el.bounds:
                    bx, by = dom_el.bounds[0], dom_el.bounds[1]
                    bw, bh = dom_el.bounds[2], dom_el.bounds[3]
                    return json.dumps({
                        "found": True,
                        "x": int(bx + bw / 2),
                        "y": int(by + bh / 2),
                        "bounds": {"x": int(bx), "y": int(by), "width": int(bw), "height": int(bh)},
                        "method": "cdp_dom",
                        "confidence": 0.85,
                        "spatial_context": f"DOM element <{dom_el.tag}>: '{dom_el.text[:60]}'",
                    })
            except Exception:
                pass

        return json.dumps({
            "found": False,
            "x": 0,
            "y": 0,
            "method": "",
            "confidence": 0,
            "spatial_context": f"Element '{target}' not found via any method",
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "uacc_where_is failed")})


@mcp.tool()
def uacc_expect(
    action_name: str = "",
    expected_change: str = "any",
    expected_text: str = "",
    timeout_ms: int = 5000,
) -> str:
    """Wait for an expected screen change and return a structured diff.

    Captures the current screen state (window title, elements, text),
    then polls until the expected change is detected or timeout.
    Use this AFTER any action that should change the screen to verify it worked.

    Args:
        action_name: Name of the action just performed (for logging).
        expected_change: Type of change expected:
            "any" — any change to window title or elements,
            "window_closed" — the active window changed,
            "element_appeared" — expected_text should appear,
            "window_title_changed" — the title should change,
            "no_change" — verify nothing changed (for read-only actions).
        expected_text: Specific text that should appear (for element_appeared).
        timeout_ms: Maximum time to wait in milliseconds (default 5000).

    Returns:
        JSON with success (whether the expected change occurred),
        before/after snapshots, diff description, and suggestion on failure.
    """
    try:
        from uacc.core.window_manager import get_active_window as _get_win
        from uacc.core.accessibility import get_ui_tree
        from uacc.core.screen_capture import get_screen_size

        def _capture_state():
            win = _get_win() or {}
            title = win.get("title", "")
            elements = []
            try:
                raw = get_ui_tree()
                elements = [el.to_dict() if hasattr(el, 'to_dict') else el for el in (raw or [])]
            except Exception:
                pass
            element_texts = []
            for el in elements:
                t = el.get("text", "") or el.get("name", "") or ""
                if t:
                    element_texts.append(t.lower())
            return {
                "window_title": title,
                "element_count": len(elements),
                "element_texts": " ".join(element_texts),
            }

        before = _capture_state()
        deadline = time.time() + (timeout_ms / 1000.0)
        change_detected = False
        change_type = ""
        after = before

        while time.time() < deadline:
            time.sleep(0.5)
            after = _capture_state()

            if expected_change == "no_change":
                change_detected = (
                    after["window_title"] == before["window_title"] and
                    after["element_count"] == before["element_count"]
                )
                if change_detected:
                    change_type = "no_change_confirmed"
                    break
            elif expected_change == "window_closed":
                if after["window_title"] != before["window_title"]:
                    change_detected = True
                    change_type = "window_title_changed"
                    break
            elif expected_change == "element_appeared":
                if expected_text and expected_text.lower() in after["element_texts"]:
                    change_detected = True
                    change_type = "element_appeared"
                    break
                if after["element_count"] > before["element_count"]:
                    change_detected = True
                    change_type = "new_elements_appeared"
                    break
            elif expected_change == "window_title_changed":
                if after["window_title"] != before["window_title"]:
                    change_detected = True
                    change_type = "window_title_changed"
                    break
            else:
                if after["window_title"] != before["window_title"]:
                    change_detected = True
                    change_type = "window_title_changed"
                    break
                if after["element_count"] != before["element_count"]:
                    change_detected = True
                    change_type = "element_count_changed"
                    break
                if after["element_texts"] != before["element_texts"]:
                    change_detected = True
                    change_type = "text_content_changed"
                    break

        result = {
            "success": change_detected,
            "change_detected": change_detected,
            "change_type": change_type if change_detected else "none",
            "before": before,
            "after": after,
            "elapsed_ms": int((time.time() - (deadline - timeout_ms / 1000.0)) * 1000),
            "action": action_name,
        }

        if not change_detected:
            result["suggestion"] = _suggest_fix(before, after, action_name, expected_change)

        session = get_session()
        session.log_action("uacc_expect", {"action": action_name, "expected": expected_change}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "uacc_expect failed")})


def _suggest_fix(before: dict, after: dict, action_name: str, expected: str) -> str:
    """Generate a human-readable suggestion when an expected change didn't occur."""
    if expected == "window_closed" and before["window_title"] == after["window_title"]:
        return f"Window '{before['window_title']}' is still open. The action may not have worked. Try an alternative click target."
    if expected == "element_appeared":
        return f"Expected text not found. The element may be in a different state. Try uacc_query() to see current screen."
    if before == after:
        return "No change detected at all. The action likely failed. Try a different approach."
    return f"Some state changed (elements: {before['element_count']}→{after['element_count']}) but not the expected type '{expected}'. Check uacc_query() for details."


# ═══════════════════════════════════════════════════════════════
#  SPATIAL QUERY ENGINE
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def find_element_relative(
    anchor: str,
    direction: str,
    target_type: str = "",
    max_distance_px: int = 300,
) -> str:
    """Find UI elements by spatial relationship to a reference element.

    Example: find_element_relative(anchor="Email", direction="below", target_type="text_input")
    finds the input field below the "Email" label.

    Args:
        anchor: Name of the reference element to search from.
        direction: Spatial direction — "above", "below", "left_of", "right_of", "nearest".
        target_type: Optional element type filter (button, text_input, etc.).
        max_distance_px: Maximum pixel distance to search (default 300).

    Returns:
        JSON with matching elements sorted by distance from the anchor.
    """
    try:
        session = get_session()
        # Refresh screen data
        screen_w, screen_h, text_map, _ = _scan_screen()
        all_elements = text_map.all_elements

        # Find anchor element
        anchor_lower = anchor.lower()
        anchor_el = None
        for el in all_elements:
            if anchor_lower in el.text.lower():
                anchor_el = el
                break

        if not anchor_el:
            return json.dumps({
                "success": False,
                "error": f"Anchor element '{anchor}' not found on screen.",
            })

        ax, ay = anchor_el.center

        # Search for elements in the specified direction
        candidates = []
        for el in all_elements:
            if el.id == anchor_el.id:
                continue
            if target_type and el.element_type != target_type:
                continue

            ex, ey = el.center
            dx = ex - ax
            dy = ey - ay
            distance = (dx**2 + dy**2) ** 0.5

            if distance > max_distance_px:
                continue

            # Direction filtering
            matches_direction = False
            if direction == "nearest":
                matches_direction = True
            elif direction == "below" and dy > 10:
                matches_direction = True
            elif direction == "above" and dy < -10:
                matches_direction = True
            elif direction == "right_of" and dx > 10:
                matches_direction = True
            elif direction == "left_of" and dx < -10:
                matches_direction = True

            if matches_direction:
                candidates.append({
                    "id": el.id,
                    "type": el.element_type,
                    "text": el.text,
                    "center": {"x": ex, "y": ey},
                    "bounds": {"left": el.bounds[0], "top": el.bounds[1], "right": el.bounds[2], "bottom": el.bounds[3]},
                    "clickable": el.clickable,
                    "editable": el.editable,
                    "distance_px": round(distance),
                    "direction_from_anchor": direction,
                })

        # Sort by distance
        candidates.sort(key=lambda c: c["distance_px"])

        session.log_action("find_element_relative", {
            "anchor": anchor, "direction": direction, "target_type": target_type,
        }, {"success": True, "matches": len(candidates)})

        return json.dumps({
            "success": True,
            "anchor": {"name": anchor_el.text, "center": {"x": ax, "y": ay}},
            "direction": direction,
            "matches": len(candidates),
            "elements": candidates[:15],
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Find relative failed")})


@mcp.tool()
def find_element_near(
    x: int,
    y: int,
    radius_px: int = 150,
    element_type: str = "",
) -> str:
    """Find all UI elements near a specific screen coordinate.

    Useful for discovering what's around a specific point when you
    know approximately where an element should be.

    Args:
        x: X coordinate to search around.
        y: Y coordinate to search around.
        radius_px: Search radius in pixels (default 150).
        element_type: Optional element type filter.

    Returns:
        JSON with nearby elements sorted by distance from (x, y).
    """
    try:
        session = get_session()
        _, _, text_map, _ = _scan_screen()
        all_elements = text_map.all_elements

        nearby = []
        for el in all_elements:
            if element_type and el.element_type != element_type:
                continue
            ex, ey = el.center
            distance = ((ex - x)**2 + (ey - y)**2) ** 0.5
            if distance <= radius_px:
                nearby.append({
                    "id": el.id,
                    "type": el.element_type,
                    "text": el.text,
                    "center": {"x": ex, "y": ey},
                    "bounds": {"left": el.bounds[0], "top": el.bounds[1], "right": el.bounds[2], "bottom": el.bounds[3]},
                    "clickable": el.clickable,
                    "editable": el.editable,
                    "distance_px": round(distance),
                })

        nearby.sort(key=lambda n: n["distance_px"])

        session.log_action("find_element_near", {
            "x": x, "y": y, "radius": radius_px,
        }, {"success": True, "matches": len(nearby)})

        return json.dumps({
            "success": True,
            "search_center": {"x": x, "y": y},
            "radius_px": radius_px,
            "matches": len(nearby),
            "elements": nearby[:20],
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Find near failed")})


# ═══════════════════════════════════════════════════════════════
#  SYSTEM & PROCESS INSPECTION
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def get_system_info() -> str:
    """Get system hardware and OS information.

    Returns CPU count, RAM, disk usage, OS version, display scaling,
    and Python environment details.

    Returns:
        JSON with system information.
    """
    try:
        import platform
        import psutil

        cpu_pct = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        screen_w, screen_h = get_screen_size()
        monitors = _list_monitors()

        return json.dumps({
            "success": True,
            "os": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
            },
            "cpu": {
                "count": psutil.cpu_count(),
                "usage_percent": cpu_pct,
            },
            "memory": {
                "total_gb": round(mem.total / (1024**3), 1),
                "available_gb": round(mem.available / (1024**3), 1),
                "usage_percent": mem.percent,
            },
            "disk": {
                "total_gb": round(disk.total / (1024**3), 1),
                "free_gb": round(disk.free / (1024**3), 1),
                "usage_percent": round(disk.percent, 1),
            },
            "display": {
                "primary_width": screen_w,
                "primary_height": screen_h,
                "monitor_count": len(monitors),
            },
            "python_version": platform.python_version(),
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "System info failed")})


@mcp.tool()
def list_processes(
    filter_name: str = "",
    sort_by: str = "memory",
    limit: int = 25,
) -> str:
    """List running processes with their resource usage.

    Args:
        filter_name: Optional filter — only show processes matching this name (case-insensitive).
        sort_by: Sort by "memory", "cpu", or "name".
        limit: Maximum number of processes to return (default 25).

    Returns:
        JSON with list of processes (name, pid, cpu%, memory, status).
    """
    try:
        import psutil

        processes = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
            try:
                info = proc.info
                name = info.get("name", "")
                if filter_name and filter_name.lower() not in name.lower():
                    continue

                mem_info = info.get("memory_info")
                mem_mb = round(mem_info.rss / (1024**2), 1) if mem_info else 0

                processes.append({
                    "pid": info["pid"],
                    "name": name,
                    "cpu_percent": info.get("cpu_percent", 0) or 0,
                    "memory_mb": mem_mb,
                    "status": info.get("status", "unknown"),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort
        if sort_by == "memory":
            processes.sort(key=lambda p: p["memory_mb"], reverse=True)
        elif sort_by == "cpu":
            processes.sort(key=lambda p: p["cpu_percent"], reverse=True)
        elif sort_by == "name":
            processes.sort(key=lambda p: p["name"].lower())

        processes = processes[:limit]

        return json.dumps({
            "success": True,
            "count": len(processes),
            "filter": filter_name or "(all)",
            "sorted_by": sort_by,
            "processes": processes,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "List processes failed")})


# ═══════════════════════════════════════════════════════════════
#  BROWSER DOM BRIDGE (Chrome DevTools Protocol)
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def browser_query(
    selector: str,
    all_matches: bool = False,
    limit: int = 25,
) -> str:
    """Find DOM element(s) by CSS selector in active browser tab via CDP.

    Bridges web automation precision with OS control. Returns matching elements,
    their text, attributes, DOM bounding box, and screen center coordinates.

    Args:
        selector: CSS selector (e.g. "input[type='email']", "#submit-btn", "a.nav-link").
        all_matches: If True, return all matching elements (up to limit). Default False.
        limit: Maximum number of elements to return when all_matches=True.

    Returns:
        JSON with matching DOM element(s) and their screen coordinates.
    """
    try:
        from uacc.core.cdp_bridge import auto_connect
        bridge = auto_connect()
        if not bridge.connected:
            return json.dumps({
                "success": False,
                "error": "No browser with remote debugging detected. Launch Chrome/Edge with --remote-debugging-port=9222",
            })

        if all_matches:
            elements = bridge.query_selector_all(selector, limit=limit)
            return json.dumps({
                "success": True,
                "selector": selector,
                "count": len(elements),
                "elements": [e.to_dict() for e in elements],
            })
        else:
            element = bridge.query_selector(selector)
            if not element:
                return json.dumps({
                    "success": False,
                    "error": f"No element found matching CSS selector '{selector}'",
                })
            return json.dumps({
                "success": True,
                "selector": selector,
                "element": element.to_dict(),
            })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Browser query failed")})


@mcp.tool()
def browser_get_page_info() -> str:
    """Get rich metadata and structural summary of the active browser tab via CDP.

    Returns page URL, title, domain, viewport dimensions, and counts of interactive
    elements (forms, links, inputs, buttons, images).

    Returns:
        JSON with comprehensive browser page state.
    """
    try:
        from uacc.core.cdp_bridge import auto_connect
        bridge = auto_connect()
        if not bridge.connected:
            return json.dumps({
                "success": False,
                "error": "No browser with remote debugging detected. Launch Chrome/Edge with --remote-debugging-port=9222",
            })

        info = bridge.get_page_info()
        return json.dumps({
            "success": True,
            "page": info,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Browser page info failed")})


@mcp.tool()
def browser_execute_js(expression: str) -> str:
    """Execute arbitrary JavaScript expression in active browser tab via CDP.

    Args:
        expression: The JavaScript code/expression to evaluate.

    Returns:
        JSON with execution result or error.
    """
    try:
        from uacc.core.cdp_bridge import auto_connect
        bridge = auto_connect()
        if not bridge.connected:
            return json.dumps({
                "success": False,
                "error": "No browser with remote debugging detected. Launch Chrome/Edge with --remote-debugging-port=9222",
            })

        val = bridge.evaluate_js(expression)
        return json.dumps({
            "success": True,
            "result": val,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Browser execute JS failed")})


@mcp.tool()
def browser_wait_for(
    selector: str,
    timeout_ms: int = 10000,
) -> str:
    """Poll until a CSS selector matches an element in active browser tab via CDP.

    Args:
        selector: CSS selector to wait for.
        timeout_ms: Maximum wait time in milliseconds (default 10,000ms).

    Returns:
        JSON with found element or timeout error.
    """
    try:
        from uacc.core.cdp_bridge import auto_connect
        bridge = auto_connect()
        if not bridge.connected:
            return json.dumps({
                "success": False,
                "error": "No browser with remote debugging detected. Launch Chrome/Edge with --remote-debugging-port=9222",
            })

        el = bridge.wait_for_selector(selector, timeout_ms=timeout_ms)
        if not el:
            return json.dumps({
                "success": False,
                "error": f"Timeout ({timeout_ms}ms) waiting for CSS selector '{selector}'",
            })

        return json.dumps({
            "success": True,
            "selector": selector,
            "element": el.to_dict(),
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Browser wait for failed")})


@mcp.tool()
def browser_click(selector: str) -> str:
    """Click a DOM element in active browser tab by CSS selector via CDP.

    Args:
        selector: CSS selector of element to click.

    Returns:
        JSON with click success status.
    """
    try:
        from uacc.core.cdp_bridge import auto_connect
        bridge = auto_connect()
        if not bridge.connected:
            return json.dumps({
                "success": False,
                "error": "No browser with remote debugging detected. Launch Chrome/Edge with --remote-debugging-port=9222",
            })

        ok = bridge.click_element(selector)
        return json.dumps({
            "success": ok,
            "selector": selector,
            "message": f"Clicked '{selector}' via DOM" if ok else f"Element '{selector}' not found",
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Browser click failed")})


@mcp.tool()
def browser_type(
    selector: str,
    text: str,
    clear_first: bool = False,
) -> str:
    """Type text into a DOM input/textarea element by CSS selector via CDP.

    Args:
        selector: CSS selector of target input field.
        text: Text string to type.
        clear_first: If True, clears existing input value before typing.

    Returns:
        JSON with typing success status.
    """
    try:
        from uacc.core.cdp_bridge import auto_connect
        bridge = auto_connect()
        if not bridge.connected:
            return json.dumps({
                "success": False,
                "error": "No browser with remote debugging detected. Launch Chrome/Edge with --remote-debugging-port=9222",
            })

        ok = bridge.type_in_element(selector, text, clear_first=clear_first)
        return json.dumps({
            "success": ok,
            "selector": selector,
            "text_typed": text,
            "clear_first": clear_first,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Browser type failed")})


@mcp.tool()
def browser_navigate(url: str) -> str:
    """Navigate active browser tab to a URL via CDP.

    Args:
        url: Destination URL (e.g. "https://google.com").

    Returns:
        JSON with navigation status.
    """
    try:
        from uacc.core.cdp_bridge import auto_connect
        bridge = auto_connect()
        if not bridge.connected:
            return json.dumps({
                "success": False,
                "error": "No browser with remote debugging detected. Launch Chrome/Edge with --remote-debugging-port=9222",
            })

        res = bridge.navigate(url)
        return json.dumps({
            "success": True,
            "url": url,
            "result": res,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Browser navigate failed")})



# ═══════════════════════════════════════════════════════════════
#  SENTINEL CONTROL TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def acknowledge_user_override() -> str:
    """Acknowledge the user override and reset the kill flag so automation can resume.
    
    Call this after the user has confirmed they want to resume automation.
    If no override is active, this is a no-op.
    """
    _get_sentinel().acknowledge_override()
    return json.dumps({"success": True, "message": "Override acknowledged, kill flag reset"})


_populate_tool_registry()

# ═══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════


def main():
    """Run the UACC MCP server.

    Supports three transport modes for maximum compatibility:
      - stdio:            Claude Code, Claude Desktop, Cursor, Hermes,
                          OpenClaw (local), OpenCode (local)
      - sse:              Legacy SSE-based clients
      - streamable-http:  OpenCode (remote), OpenClaw (remote), web clients
    """
    parser = argparse.ArgumentParser(
        description="UACC MCP Server — Universal AI Computer Control via MCP",
        epilog="Examples:\n"
               "  uacc-mcp                          # stdio (for Claude Desktop)\n"
               "  uacc-mcp --transport sse --port 8765  # SSE transport\n"
               "  uacc-mcp --transport streamable-http  # HTTP transport\n"
               "  uacc-mcp --safe-mode false            # disable safe mode\n"
               "  uacc-mcp --verbose                    # debug logging",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"UACC {uacc_version}",
        help="Show version and exit",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport mode (default: stdio)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind for SSE/HTTP transports (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for SSE/HTTP transports (default: 8765)",
    )
    parser.add_argument(
        "--path",
        type=str,
        default="/mcp",
        help="URL path for streamable-http transport (default: /mcp)",
    )
    parser.add_argument(
        "--safe-mode",
        type=str,
        choices=["true", "false"],
        default=None,
        help="Override safe mode (true/false). Default: from UACC_SAFE_MODE env var.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug-level logging",
    )
    args = parser.parse_args()

    # Apply safe-mode override
    if args.safe_mode is not None:
        config.uacc.safe_mode = args.safe_mode == "true"

    # Configure logging — always to stderr so stdout stays clean for stdio
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    # Check for newer version on PyPI (best-effort, non-blocking)
    try:
        import urllib.request, json
        req = urllib.request.Request(
            "https://pypi.org/pypi/uacc/json",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            latest = json.loads(resp.read())["info"]["version"]
        if latest != uacc_version:
            logger.warning(
                "UACC %s available (you have %s) — run: pip install uacc --upgrade",
                latest, uacc_version,
            )
    except Exception:
        pass  # network failure is non-fatal

    logger.info(
        "Starting UACC MCP server (transport=%s, safe_mode=%s, verbose=%s)",
        args.transport,
        config.uacc.safe_mode,
        args.verbose,
    )

    # Initialize mouse sentinel for safety monitoring
    _get_sentinel()

    try:
        if args.transport == "sse":
            mcp.run(transport="sse", host=args.host, port=args.port)
        elif args.transport == "streamable-http":
            mcp.run(
                transport="streamable-http",
                host=args.host,
                port=args.port,
                path=args.path,
            )
        else:
            mcp.run(transport="stdio")
    finally:
        if _sentinel:
            _sentinel.stop()


if __name__ == "__main__":
    main()

