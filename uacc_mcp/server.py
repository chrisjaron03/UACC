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

from mcp.server.fastmcp import FastMCP

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
from uacc.core.clipboard import read_clipboard as _clipboard_read, write_clipboard as _clipboard_write
from uacc.core.element_finder import (
    click_element_by_name,
    get_mouse_position as _get_mouse_position,
    wait_for_element as _wait_for_element,
)
from uacc.core.screen_capture import (
    capture_full,
    capture_region,
    get_screen_size,
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

from uacc.actions.artistic_painter import ArtisticPainter
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


def _get_executor() -> ActionExecutor:
    """Lazily create the shared ActionExecutor."""
    global _executor
    if _executor is None:
        _executor = ActionExecutor(
            human_mimicry=config.uacc.human_mimicry,
            safe_mode=config.uacc.safe_mode,
        )
    return _executor


# ═══════════════════════════════════════════════════════════════
#  TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def screenshot(
    region_x: int | None = None,
    region_y: int | None = None,
    width: int | None = None,
    height: int | None = None,
    format: str = "PNG",
    quality: int = 80,
) -> str:
    """Capture a screenshot of the screen.

    Returns a base64-encoded image. Capture the full screen by default,
    or a specific region by providing coordinates.

    Args:
        region_x: Left edge of the capture region (omit for full screen).
        region_y: Top edge of the capture region (omit for full screen).
        width: Width of the capture region in pixels.
        height: Height of the capture region in pixels.
        format: Image format — "PNG" (lossless) or "JPEG" (smaller).
        quality: JPEG quality 1-100 (ignored for PNG).

    Returns:
        JSON with base64-encoded image data and metadata.
    """
    try:
        if region_x is not None and region_y is not None and width and height:
            img = capture_region(region_x, region_y, width, height)
            region_info = f"{width}×{height} at ({region_x}, {region_y})"
        else:
            img = capture_full()
            screen_w, screen_h = img.size
            region_info = f"full screen {screen_w}×{screen_h}"

        b64 = image_to_base64(img, fmt=format, quality=quality)
        media_type = get_image_media_type(format)

        session = get_session()
        session.log_action("screenshot", {"region": region_info}, {"success": True})

        return json.dumps({
            "success": True,
            "image_base64": b64,
            "media_type": media_type,
            "width": img.size[0],
            "height": img.size[1],
            "region": region_info,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Screenshot capture failed")})


@mcp.tool()
def get_screen_info(include_non_interactive: bool = False) -> str:
    """Get a structured text map of all UI elements on the current screen.

    Returns the screen state as a compact text representation showing every
    interactive element with its type, label, coordinates, and capabilities.
    This is how text-only LLMs "see" the screen.

    Args:
        include_non_interactive: If True, include labels and static text.
                                  If False, only interactive elements (buttons, inputs, etc.).

    Returns:
        Structured text map of the screen with element positions.
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

        result = {
            "success": True,
            "screen_width": screen_w,
            "screen_height": screen_h,
            "active_window": active_window,
            "total_elements": len(text_map.all_elements),
            "interactive_elements": interactive_count,
            "text_map": compact,
        }

        if include_non_interactive:
            result["full_yaml"] = text_map.to_yaml()

        session.log_action(
            "get_screen_info",
            {"include_non_interactive": include_non_interactive},
            {"success": True, "elements": len(text_map.all_elements)},
        )

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Screen info failed")})


@mcp.tool()
def click(
    x: int,
    y: int,
    button: str = "left",
    count: int = 1,
    modifiers: list[str] | None = None,
    reasoning: str = "",
) -> str:
    """Click at exact screen coordinates.

    Args:
        x: X coordinate (pixels from left edge).
        y: Y coordinate (pixels from top edge).
        button: Mouse button — "left", "right", or "middle".
        count: Click count — 1 for single click, 2 for double click.
        modifiers: Modifier keys to hold — e.g. ["ctrl"], ["shift", "ctrl"].
        reasoning: Why you're clicking here (for logging).

    Returns:
        JSON with success status and message.
    """
    try:
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

        session = get_session()
        session.log_action(
            "click",
            {"x": x, "y": y, "button": button, "count": count, "reasoning": reasoning},
            result,
        )

        return json.dumps({
            "success": result["success"],
            "message": result["message"],
            "coordinates": {"x": x, "y": y},
            "button": button,
            "count": count,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Click failed")})


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

        return json.dumps({
            "success": result["success"],
            "message": result["message"],
            "characters_typed": len(text),
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Type failed")})


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
        action = HotkeyAction(
            keys=keys,
            reasoning=reasoning,
        )

        executor = _get_executor()
        result = executor.execute(action)

        combo = "+".join(keys)
        session = get_session()
        session.log_action("hotkey", {"keys": keys, "reasoning": reasoning}, result)

        return json.dumps({
            "success": result["success"],
            "message": result["message"],
            "combination": combo,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Hotkey failed")})


@mcp.tool()
def scroll(
    x: int,
    y: int,
    direction: str = "down",
    amount: int = 3,
    reasoning: str = "",
) -> str:
    """Scroll at a specific screen position.

    Args:
        x: X coordinate to scroll at.
        y: Y coordinate to scroll at.
        direction: Scroll direction — "up", "down", "left", or "right".
        amount: Number of scroll increments (typically 1-10).
        reasoning: Why you're scrolling (for logging).

    Returns:
        JSON with success status and scroll details.
    """
    try:
        action = ScrollAction(
            x=x,
            y=y,
            direction=ScrollDirection(direction),
            amount=amount,
            reasoning=reasoning,
        )

        executor = _get_executor()
        result = executor.execute(action)

        session = get_session()
        session.log_action(
            "scroll",
            {"x": x, "y": y, "direction": direction, "amount": amount, "reasoning": reasoning},
            result,
        )

        return json.dumps({
            "success": result["success"],
            "message": result["message"],
            "position": {"x": x, "y": y},
            "direction": direction,
            "amount": amount,
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Scroll failed")})


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

        return json.dumps({
            "success": result["success"],
            "message": result["message"],
            "start": {"x": start_x, "y": start_y},
            "end": {"x": end_x, "y": end_y},
        })

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Drag failed")})


@mcp.tool()
def hover(
    x: int,
    y: int,
    duration_ms: int = 500,
    reasoning: str = "",
) -> str:
    """Move the mouse to a position and hold (useful for triggering tooltips and hover menus).

    Args:
        x: X coordinate to hover at.
        y: Y coordinate to hover at.
        duration_ms: How long to hold the hover in milliseconds.
        reasoning: Why you're hovering here (for logging).

    Returns:
        JSON with success status and hover details.
    """
    try:
        action = HoverAction(
            x=x,
            y=y,
            duration_ms=duration_ms,
            reasoning=reasoning,
        )

        executor = _get_executor()
        result = executor.execute(action)

        session = get_session()
        session.log_action(
            "hover",
            {"x": x, "y": y, "duration_ms": duration_ms, "reasoning": reasoning},
            result,
        )

        return json.dumps({
            "success": result["success"],
            "message": result["message"],
            "position": {"x": x, "y": y},
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

    Scans the accessibility tree and returns matching elements with their
    exact coordinates. Use this to find clickable targets before calling click().

    Args:
        name: Text to search for in element labels (case-insensitive substring match).
              Example: "File", "Save", "OK", "Cancel".
        element_type: Element type to filter by.
              Common types: "button", "menu_item", "text_input", "checkbox",
              "tab", "link", "dropdown", "list_item", "tree_item", "label".
        refresh: If True, re-scan the screen first. If False, use cached data.

    Returns:
        JSON with list of matching elements and their coordinates.
    """
    try:
        session = get_session()

        if refresh:
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
            "tip": "Use click(x, y) with the center coordinates to interact with an element.",
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
        result = _move_window(title, x, y)

        session = get_session()
        session.log_action("move_window", {"title": title, "x": x, "y": y}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Move window failed")})


@mcp.tool()
def minimize_maximize(title: str, action: str = "maximize") -> str:
    """Minimize, maximize, or restore a window.

    Args:
        title: Substring to match against window titles.
        action: One of "minimize", "maximize", or "restore".

    Returns:
        JSON with success status.
    """
    try:
        result = _min_max_window(title, action)

        session = get_session()
        session.log_action("minimize_maximize", {"title": title, "action": action}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Minimize/maximize failed")})


@mcp.tool()
def launch_app(
    name_or_path: str,
    arguments: str = "",
    wait_ms: int = 2000,
) -> str:
    """Launch an application by name or path.

    Supports common app names ("notepad", "chrome", "calc", "code",
    "explorer", "paint", "terminal") or full executable paths.

    Args:
        name_or_path: Application name or full path to executable.
        arguments: Optional command-line arguments.
        wait_ms: Time to wait after launch for window to appear (ms).

    Returns:
        JSON with success status and process info.
    """
    try:
        result = _launch_app(name_or_path, arguments, wait_ms)

        session = get_session()
        session.log_action("launch_app", {"app": name_or_path, "args": arguments}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Launch app failed")})


@mcp.tool()
def open_url(url: str) -> str:
    """Open a URL in the default web browser.

    Args:
        url: The URL to open. Will auto-prepend https:// if no scheme.

    Returns:
        JSON with success status.
    """
    try:
        result = _open_url(url)

        session = get_session()
        session.log_action("open_url", {"url": url}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Open URL failed")})


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
    """Find a UI element by name and click it.

    This is the "easy mode" alternative to raw coordinate clicking.
    UACC finds the element using fuzzy name matching and clicks its
    center coordinates automatically.

    Args:
        name: Text to search for in element labels (fuzzy match).
              Examples: "File", "Save", "OK", "Submit", "Cancel".
        element_type: Optional type filter (button, menu_item, text_input, etc.).
        button: Mouse button — "left", "right", or "middle".
        reasoning: Why you're clicking this element (for logging).

    Returns:
        JSON with clicked element info and coordinates.
    """
    try:
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
        preset_name: The design to draw ("rose", "galaxy", "mountains", "peacock").

    Returns:
        JSON with success status and drawing stroke details.
    """
    try:
        # 1. Launch Paint
        _launch_app("mspaint", wait_ms=2000)

        # 2. Get screen dimensions to find canvas center
        screen_w, screen_h = get_screen_size()
        cx, cy = screen_w // 2, screen_h // 2 + 80  # Off-center to fit toolbar

        # 3. Instantiate painter and draw
        painter = ArtisticPainter()
        result = painter.draw_preset(preset_name, (cx, cy))

        session = get_session()
        session.log_action("paint_preset", {"preset": preset_name}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Paint preset failed")})


@mcp.tool()
def paint_image(image_path: str, max_strokes: int = 150) -> str:
    """Sketch the outline of an image file on screen inside MS Paint.

    Launches Paint, loads the image from disk, extracts its outline
    contours using edge detection, and draws the sketch using UACC's
    brush stroke coordinates.

    Args:
        image_path: Absolute path to the source image file to sketch.
        max_strokes: Maximum brush strokes to draw (default 150).

    Returns:
        JSON with success status and drawing stroke details.
    """
    try:
        # 1. Launch Paint
        _launch_app("mspaint", wait_ms=2000)

        # 2. Determine canvas coordinates
        screen_w, screen_h = get_screen_size()
        
        # Approximate canvas bounds in Paint: main workspace
        canvas_bounds = (10, 150, screen_w - 200, screen_h - 100)

        # 3. Paint image outlines
        painter = ArtisticPainter()
        result = painter.draw_image(image_path, canvas_bounds, max_strokes=max_strokes)

        session = get_session()
        session.log_action("paint_image", {"image_path": image_path, "max_strokes": max_strokes}, result)

        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"success": False, "error": format_error(exc, "Paint image failed")})


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
               Example: [{\"tool\": \"launch_app\", \"params\": {\"name_or_path\": \"notepad\"}}]
        tags: Optional tags for categorising workflows.

    Returns:
        JSON with success status and workflow details.
    """
    try:
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

            # Look up the MCP tool function from the global namespace
            tool_fn = globals().get(tool_name)

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
    args = parser.parse_args()

    # Configure logging — always to stderr so stdout stays clean for stdio
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    logger.info(
        "Starting UACC MCP server (transport=%s, safe_mode=%s)",
        args.transport,
        config.uacc.safe_mode,
    )

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


if __name__ == "__main__":
    main()

