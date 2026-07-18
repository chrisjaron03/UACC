"""
Window Manager — control application windows via pywinauto.

Provides window listing, focusing, resizing, moving, minimizing/maximizing,
and application launching. All operations target the Windows UI Automation
backend for maximum compatibility.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Ensure pywin32 DLLs can be loaded (Python 3.8+ on Windows)
try:
    import os
    _dll_dir = os.path.join(sys.prefix, 'Lib', 'site-packages', 'pywin32_system32')
    if os.path.isdir(_dll_dir):
        os.add_dll_directory(_dll_dir)
except Exception:
    pass


def _is_zoomed(hwnd: int) -> bool:
    """Check if a window is maximized, with fallback for missing IsZoomed."""
    try:
        import win32gui
        return bool(win32gui.IsZoomed(hwnd))
    except AttributeError:
        pass
    try:
        import win32gui
        placement = win32gui.GetWindowPlacement(hwnd)
        # placement[1] = showCmd: 3=SW_SHOWMAXIMIZED
        return placement[1] == 3
    except Exception:
        return False


@dataclass
class WindowInfo:
    """Information about an open window."""

    title: str
    bounds: Tuple[int, int, int, int]  # (left, top, right, bottom)
    center: Tuple[int, int]
    width: int
    height: int
    process_name: str
    process_id: int
    is_visible: bool
    is_focused: bool
    is_maximized: bool
    is_minimized: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "bounds": {
                "left": self.bounds[0],
                "top": self.bounds[1],
                "right": self.bounds[2],
                "bottom": self.bounds[3],
            },
            "center": {"x": self.center[0], "y": self.center[1]},
            "width": self.width,
            "height": self.height,
            "process_name": self.process_name,
            "process_id": self.process_id,
            "is_visible": self.is_visible,
            "is_focused": self.is_focused,
            "is_maximized": self.is_maximized,
            "is_minimized": self.is_minimized,
        }


def _get_window_info(win: Any, is_focused: bool = False) -> Optional[WindowInfo]:
    """Extract WindowInfo from a pywinauto window wrapper."""
    try:
        rect = win.rectangle()
        if rect.width() <= 0 or rect.height() <= 0:
            return None

        title = ""
        try:
            title = win.window_text() or ""
        except Exception:
            pass

        if not title.strip():
            return None

        process_name = ""
        pid = 0
        try:
            pid = win.process_id()
            import psutil
            proc = psutil.Process(pid)
            process_name = proc.name()
        except Exception:
            try:
                pid = win.process_id()
            except Exception:
                pass

        is_maximized = False
        is_minimized = False
        try:
            placement = win.get_show_state()
            is_maximized = placement == 3  # SW_SHOWMAXIMIZED
            is_minimized = placement == 2  # SW_SHOWMINIMIZED
        except Exception:
            pass

        return WindowInfo(
            title=title.strip(),
            bounds=(rect.left, rect.top, rect.right, rect.bottom),
            center=(rect.mid_point().x, rect.mid_point().y),
            width=rect.width(),
            height=rect.height(),
            process_name=process_name,
            process_id=pid,
            is_visible=True,
            is_focused=is_focused,
            is_maximized=is_maximized,
            is_minimized=is_minimized,
        )
    except Exception as exc:
        logger.debug("Failed to get window info: %s", exc)
        return None


def get_active_window() -> Optional[WindowInfo]:
    """Get information about the currently focused window.

    Returns:
        WindowInfo for the foreground window, or None if not available.
    """
    if sys.platform != "win32":
        try:
            import pywinctl as pwc
            win = pwc.getActiveWindow()
            if not win:
                return None
            left, top, right, bottom = win.left, win.top, win.right, win.bottom
            width, height = win.width, win.height
            title = win.title or ""
            pid = 0
            process_name = ""
            try:
                pid = win.pid
                if pid > 0:
                    import psutil
                    proc = psutil.Process(pid)
                    process_name = proc.name()
            except Exception:
                pass
            is_maximized = False
            is_minimized = False
            try:
                is_maximized = win.isMaximized
                is_minimized = win.isMinimized
            except Exception:
                pass
            return WindowInfo(
                title=title,
                bounds=(left, top, right, bottom),
                center=((left + right) // 2, (top + bottom) // 2),
                width=width,
                height=height,
                process_name=process_name,
                process_id=pid,
                is_visible=True,
                is_focused=True,
                is_maximized=is_maximized,
                is_minimized=is_minimized,
            )
        except Exception as exc:
            logger.warning("Failed to get active window via pywinctl: %s", exc)
            return None

    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)

        import win32process
        _, pid = win32process.GetWindowThreadProcessId(hwnd)

        rect = win32gui.GetWindowRect(hwnd)
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top

        process_name = ""
        try:
            import psutil
            proc = psutil.Process(pid)
            process_name = proc.name()
        except Exception:
            pass

        is_maximized = _is_zoomed(hwnd)
        is_minimized = win32gui.IsIconic(hwnd)

        return WindowInfo(
            title=title,
            bounds=(left, top, right, bottom),
            center=((left + right) // 2, (top + bottom) // 2),
            width=width,
            height=height,
            process_name=process_name,
            process_id=pid,
            is_visible=True,
            is_focused=True,
            is_maximized=bool(is_maximized),
            is_minimized=bool(is_minimized),
        )
    except ImportError:
        # Fallback to pywinauto
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            windows = desktop.windows(active_only=True, visible_only=True)
            if windows:
                return _get_window_info(windows[0], is_focused=True)
        except Exception as exc:
            logger.warning("Failed to get active window: %s", exc)
    except Exception as exc:
        logger.warning("Failed to get active window: %s", exc)

    return None


def list_windows(include_hidden: bool = False) -> List[WindowInfo]:
    """List all open windows.

    Args:
        include_hidden: If True, include non-visible windows.

    Returns:
        List of WindowInfo objects for each window.
    """
    results: List[WindowInfo] = []

    if sys.platform != "win32":
        try:
            import pywinctl as pwc
            active_win = pwc.getActiveWindow()
            active_title = active_win.title if active_win else ""
            for win in pwc.getAllWindows():
                try:
                    if not include_hidden and not win.isVisible:
                        continue
                    title = win.title or ""
                    if not title.strip():
                        continue
                    left, top, right, bottom = win.left, win.top, win.right, win.bottom
                    width, height = win.width, win.height
                    if width <= 0 or height <= 0:
                        continue
                    pid = 0
                    process_name = ""
                    try:
                        pid = win.pid
                        if pid > 0:
                            import psutil
                            proc = psutil.Process(pid)
                            process_name = proc.name()
                    except Exception:
                        pass
                    is_maximized = False
                    is_minimized = False
                    try:
                        is_maximized = win.isMaximized
                        is_minimized = win.isMinimized
                    except Exception:
                        pass
                    results.append(WindowInfo(
                        title=title.strip(),
                        bounds=(left, top, right, bottom),
                        center=((left + right) // 2, (top + bottom) // 2),
                        width=width,
                        height=height,
                        process_name=process_name,
                        process_id=pid,
                        is_visible=True,
                        is_focused=(title == active_title),
                        is_maximized=is_maximized,
                        is_minimized=is_minimized,
                    ))
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Failed to list windows via pywinctl: %s", exc)
        return results

    try:
        import win32gui

        active_hwnd = win32gui.GetForegroundWindow()

        def enum_callback(hwnd: int, _: Any) -> None:
            if not include_hidden and not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title.strip():
                return

            try:
                import win32process
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                rect = win32gui.GetWindowRect(hwnd)
                left, top, right, bottom = rect
                width = right - left
                height = bottom - top

                if width <= 0 or height <= 0:
                    return

                process_name = ""
                try:
                    import psutil
                    proc = psutil.Process(pid)
                    process_name = proc.name()
                except Exception:
                    pass

                results.append(WindowInfo(
                    title=title.strip(),
                    bounds=(left, top, right, bottom),
                    center=((left + right) // 2, (top + bottom) // 2),
                    width=width,
                    height=height,
                    process_name=process_name,
                    process_id=pid,
                    is_visible=win32gui.IsWindowVisible(hwnd),
                    is_focused=(hwnd == active_hwnd),
                    is_maximized=_is_zoomed(hwnd),
                    is_minimized=bool(win32gui.IsIconic(hwnd)),
                ))
            except Exception:
                pass

        win32gui.EnumWindows(enum_callback, None)

    except ImportError:
        # Fallback to pywinauto
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            for win in desktop.windows(visible_only=not include_hidden):
                info = _get_window_info(win)
                if info:
                    results.append(info)
        except Exception as exc:
            logger.warning("Failed to list windows: %s", exc)

    logger.info("Listed %d windows", len(results))
    return results


def focus_window(title: str) -> Dict[str, Any]:
    """Bring a window to the foreground by title (substring match).

    Args:
        title: Substring to match against window titles (case-insensitive).

    Returns:
        Result dict with success status and matched window info.
    """
    if sys.platform != "win32":
        try:
            import pywinctl as pwc
            for win in pwc.getAllWindows():
                wt = win.title or ""
                if title.lower() in wt.lower():
                    try:
                        if win.isMinimized:
                            win.restore()
                    except Exception:
                        pass
                    win.activate()
                    time.sleep(0.2)
                    return {
                        "success": True,
                        "message": f"Focused window: '{wt}'",
                        "window_title": wt,
                    }
            return {"success": False, "message": f"No window found matching '{title}'"}
        except Exception as exc:
            return {"success": False, "message": f"Failed to focus window: {exc}"}

    try:
        import win32gui
        import win32con

        target_hwnd = None
        target_title = ""

        def enum_callback(hwnd: int, _: Any) -> None:
            nonlocal target_hwnd, target_title
            if not win32gui.IsWindowVisible(hwnd):
                return
            wt = win32gui.GetWindowText(hwnd)
            if title.lower() in wt.lower():
                target_hwnd = hwnd
                target_title = wt

        win32gui.EnumWindows(enum_callback, None)

        if target_hwnd is None:
            return {"success": False, "message": f"No window found matching '{title}'"}

        # Restore if minimized
        if win32gui.IsIconic(target_hwnd):
            win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)

        win32gui.SetForegroundWindow(target_hwnd)
        time.sleep(0.2)  # Allow window to come to front

        return {
            "success": True,
            "message": f"Focused window: '{target_title}'",
            "window_title": target_title,
        }

    except ImportError:
        # Fallback to pywinauto
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            windows = desktop.windows(title_re=f".*{title}.*", visible_only=True)
            if not windows:
                return {"success": False, "message": f"No window found matching '{title}'"}
            windows[0].set_focus()
            time.sleep(0.2)
            wt = windows[0].window_text()
            return {
                "success": True,
                "message": f"Focused window: '{wt}'",
                "window_title": wt,
            }
        except Exception as exc:
            return {"success": False, "message": f"Failed to focus window: {exc}"}

    except Exception as exc:
        return {"success": False, "message": f"Failed to focus window: {exc}"}


def resize_window(title: str, width: int, height: int) -> Dict[str, Any]:
    """Resize a window by title.

    Args:
        title: Substring to match against window titles.
        width: New width in pixels.
        height: New height in pixels.

    Returns:
        Result dict with success status.
    """
    if sys.platform != "win32":
        try:
            import pywinctl as pwc
            for win in pwc.getAllWindows():
                wt = win.title or ""
                if title.lower() in wt.lower():
                    try:
                        if win.isMinimized:
                            win.restore()
                    except Exception:
                        pass
                    win.resizeTo(width, height)
                    return {
                        "success": True,
                        "message": f"Resized window '{wt}' to {width}x{height}",
                        "window_title": wt,
                    }
            return {"success": False, "message": f"No window found matching '{title}'"}
        except Exception as exc:
            return {"success": False, "message": f"Failed to resize window: {exc}"}

    try:
        import win32gui
        import win32con

        target_hwnd = None
        target_title = ""

        def enum_callback(hwnd: int, _: Any) -> None:
            nonlocal target_hwnd, target_title
            if not win32gui.IsWindowVisible(hwnd):
                return
            wt = win32gui.GetWindowText(hwnd)
            if title.lower() in wt.lower():
                target_hwnd = hwnd
                target_title = wt

        win32gui.EnumWindows(enum_callback, None)

        if target_hwnd is None:
            return {"success": False, "message": f"No window found matching '{title}'"}

        # Restore if maximized
        if _is_zoomed(target_hwnd):
            win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
            time.sleep(0.1)

        rect = win32gui.GetWindowRect(target_hwnd)
        win32gui.MoveWindow(target_hwnd, rect[0], rect[1], width, height, True)

        return {
            "success": True,
            "message": f"Resized '{target_title}' to {width}×{height}",
            "window_title": target_title,
        }

    except ImportError:
        return {"success": False, "message": "win32gui not available — install pywin32"}
    except Exception as exc:
        return {"success": False, "message": f"Failed to resize window: {exc}"}


def move_window(title: str, x: int, y: int) -> Dict[str, Any]:
    """Move a window to a new position.

    Args:
        title: Substring to match against window titles.
        x: New left edge position.
        y: New top edge position.

    Returns:
        Result dict with success status.
    """
    if sys.platform != "win32":
        try:
            import pywinctl as pwc
            for win in pwc.getAllWindows():
                wt = win.title or ""
                if title.lower() in wt.lower():
                    try:
                        if win.isMinimized:
                            win.restore()
                    except Exception:
                        pass
                    win.moveTo(x, y)
                    return {
                        "success": True,
                        "message": f"Moved window '{wt}' to ({x}, {y})",
                        "window_title": wt,
                    }
            return {"success": False, "message": f"No window found matching '{title}'"}
        except Exception as exc:
            return {"success": False, "message": f"Failed to move window: {exc}"}

    try:
        import win32gui
        import win32con

        target_hwnd = None
        target_title = ""

        def enum_callback(hwnd: int, _: Any) -> None:
            nonlocal target_hwnd, target_title
            if not win32gui.IsWindowVisible(hwnd):
                return
            wt = win32gui.GetWindowText(hwnd)
            if title.lower() in wt.lower():
                target_hwnd = hwnd
                target_title = wt

        win32gui.EnumWindows(enum_callback, None)

        if target_hwnd is None:
            return {"success": False, "message": f"No window found matching '{title}'"}

        # Restore if maximized
        if _is_zoomed(target_hwnd):
            win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
            time.sleep(0.1)

        rect = win32gui.GetWindowRect(target_hwnd)
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        win32gui.MoveWindow(target_hwnd, x, y, width, height, True)

        return {
            "success": True,
            "message": f"Moved '{target_title}' to ({x}, {y})",
            "window_title": target_title,
        }

    except ImportError:
        return {"success": False, "message": "win32gui not available — install pywin32"}
    except Exception as exc:
        return {"success": False, "message": f"Failed to move window: {exc}"}


def minimize_maximize_window(
    title: str, action: str = "maximize"
) -> Dict[str, Any]:
    """Minimize, maximize, or restore a window.

    Args:
        title: Substring to match against window titles.
        action: One of "minimize", "maximize", "restore".

    Returns:
        Result dict with success status.
    """
    if sys.platform != "win32":
        try:
            import pywinctl as pwc
            for win in pwc.getAllWindows():
                wt = win.title or ""
                if title.lower() in wt.lower():
                    if action.lower() == "minimize":
                        win.minimize()
                    elif action.lower() == "maximize":
                        win.maximize()
                    elif action.lower() == "restore":
                        win.restore()
                    else:
                        return {"success": False, "message": f"Unknown action: {action}. Use minimize/maximize/restore."}
                    time.sleep(0.2)
                    return {
                        "success": True,
                        "message": f"{action.capitalize()}d '{wt}'",
                        "window_title": wt,
                    }
            return {"success": False, "message": f"No window found matching '{title}'"}
        except Exception as exc:
            return {"success": False, "message": f"Failed to {action} window: {exc}"}

    try:
        import win32gui
        import win32con

        target_hwnd = None
        target_title = ""

        def enum_callback(hwnd: int, _: Any) -> None:
            nonlocal target_hwnd, target_title
            if not win32gui.IsWindowVisible(hwnd):
                return
            wt = win32gui.GetWindowText(hwnd)
            if title.lower() in wt.lower():
                target_hwnd = hwnd
                target_title = wt

        win32gui.EnumWindows(enum_callback, None)

        if target_hwnd is None:
            return {"success": False, "message": f"No window found matching '{title}'"}

        cmd_map = {
            "minimize": win32con.SW_MINIMIZE,
            "maximize": win32con.SW_MAXIMIZE,
            "restore": win32con.SW_RESTORE,
        }
        cmd = cmd_map.get(action.lower())
        if cmd is None:
            return {"success": False, "message": f"Unknown action: {action}. Use minimize/maximize/restore."}

        win32gui.ShowWindow(target_hwnd, cmd)
        time.sleep(0.2)

        return {
            "success": True,
            "message": f"{action.capitalize()}d '{target_title}'",
            "window_title": target_title,
        }

    except ImportError:
        return {"success": False, "message": "win32gui not available — install pywin32"}
    except Exception as exc:
        return {"success": False, "message": f"Failed to {action} window: {exc}"}


def launch_application(
    name_or_path: str, arguments: str = "", wait_ms: int = 2000
) -> Dict[str, Any]:
    """Launch an application by name or path.

    Supports:
    - Full paths: "C:\\Windows\\notepad.exe"
    - App names: "notepad", "calc", "chrome", "firefox"
    - Start menu entries (via `start` command)

    Args:
        name_or_path: Application name or full path.
        arguments: Optional command-line arguments.
        wait_ms: Time to wait after launch (ms) for window to appear.

    Returns:
        Result dict with success status and process info.
    """
    if sys.platform == "win32":
        # Windows-specific app mappings
        APP_ALIASES = {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "calc": "calc.exe",
            "paint": "mspaint.exe",
            "explorer": "explorer.exe",
            "cmd": "cmd.exe",
            "powershell": "powershell.exe",
            "terminal": "wt.exe",
            "chrome": "chrome.exe",
            "firefox": "firefox.exe",
            "edge": "msedge.exe",
            "code": "code.exe",
            "vscode": "code.exe",
            "word": "winword.exe",
            "excel": "excel.exe",
            "outlook": "outlook.exe",
            "teams": "teams.exe",
            "slack": "slack.exe",
            "spotify": "spotify.exe",
            "discord": "discord.exe",
        }
        try:
            executable = APP_ALIASES.get(name_or_path.lower(), name_or_path)
            cmd_parts = [executable]
            if arguments:
                cmd_parts.extend(arguments.split())
            try:
                proc = subprocess.Popen(
                    cmd_parts,
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                proc = subprocess.Popen(
                    f'start "" "{executable}" {arguments}',
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            time.sleep(wait_ms / 1000)
            return {
                "success": True,
                "message": f"Launched '{name_or_path}' (PID: {proc.pid})",
                "process_id": proc.pid,
                "executable": executable,
            }
        except Exception as exc:
            return {"success": False, "message": f"Failed to launch '{name_or_path}': {exc}"}
    else:
        # macOS & Linux implementation
        if sys.platform == "darwin":
            APP_ALIASES = {
                "notepad": "TextEdit",
                "calculator": "Calculator",
                "calc": "Calculator",
                "terminal": "Terminal",
                "chrome": "Google Chrome",
                "safari": "Safari",
                "vscode": "Visual Studio Code",
                "code": "Visual Studio Code",
            }
        else:
            APP_ALIASES = {
                "notepad": "gedit",
                "calculator": "gnome-calculator",
                "calc": "gnome-calculator",
                "terminal": "gnome-terminal",
                "chrome": "google-chrome",
                "firefox": "firefox",
                "vscode": "code",
                "code": "code",
            }
        try:
            executable = APP_ALIASES.get(name_or_path.lower(), name_or_path)
            if sys.platform == "darwin":
                if "/" not in executable and not executable.endswith(".app"):
                    cmd = ["open", "-a", executable]
                    if arguments:
                        cmd.extend(["--args"] + arguments.split())
                else:
                    cmd = ["open", executable]
                    if arguments:
                        cmd.extend(["--args"] + arguments.split())
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                # Linux
                cmd_parts = [executable]
                if arguments:
                    cmd_parts.extend(arguments.split())
                try:
                    proc = subprocess.Popen(
                        cmd_parts,
                        shell=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except FileNotFoundError:
                    proc = subprocess.Popen(
                        ["xdg-open", executable],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            time.sleep(wait_ms / 1000)
            return {
                "success": True,
                "message": f"Launched '{name_or_path}' (PID: {proc.pid})",
                "process_id": proc.pid,
                "executable": executable,
            }
        except Exception as exc:
            return {"success": False, "message": f"Failed to launch '{name_or_path}': {exc}"}


def open_url(url: str) -> Dict[str, Any]:
    """Open a URL in the default web browser.

    Args:
        url: The URL to open (must start with http:// or https://).

    Returns:
        Result dict with success status.
    """
    import webbrowser

    try:
        # Ensure URL has a scheme
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        webbrowser.open(url)
        time.sleep(1.0)  # Allow browser to open

        return {
            "success": True,
            "message": f"Opened URL: {url}",
            "url": url,
        }
    except Exception as exc:
        return {"success": False, "message": f"Failed to open URL: {exc}"}
