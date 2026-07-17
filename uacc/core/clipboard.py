"""
Clipboard — read and write text clipboard content.

Provides cross-platform clipboard access using native Windows APIs
with fallback to pyperclip.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def read_clipboard() -> Dict[str, Any]:
    """Read the current clipboard text content.

    Returns:
        Dict with success status and clipboard text.
    """
    text = _read_clipboard_text()
    if text is not None:
        return {
            "success": True,
            "text": text,
            "length": len(text),
            "message": f"Read {len(text)} characters from clipboard",
        }
    return {
        "success": False,
        "text": "",
        "message": "Clipboard is empty or contains non-text data",
    }


def write_clipboard(text: str) -> Dict[str, Any]:
    """Write text to the clipboard.

    Args:
        text: The text to place on the clipboard.

    Returns:
        Dict with success status.
    """
    try:
        _write_clipboard_text(text)
        return {
            "success": True,
            "message": f"Wrote {len(text)} characters to clipboard",
            "length": len(text),
        }
    except Exception as exc:
        return {
            "success": False,
            "message": f"Failed to write to clipboard: {exc}",
        }


def _read_clipboard_text() -> Optional[str]:
    """Read text from clipboard using native APIs."""
    # Try win32clipboard first (fastest, most reliable on Windows)
    try:
        import os
        dll_dir = os.path.join(sys.prefix, 'Lib', 'site-packages', 'pywin32_system32')
        if os.path.isdir(dll_dir):
            os.add_dll_directory(dll_dir)
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                return str(data)
            elif win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_TEXT):
                data = win32clipboard.GetClipboardData(win32clipboard.CF_TEXT)
                return data.decode("utf-8", errors="replace")
        finally:
            win32clipboard.CloseClipboard()
        return None
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("win32clipboard read failed: %s", exc)

    # Fallback: ctypes (no extra dependencies)
    try:
        import ctypes

        CF_UNICODETEXT = 13
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        if not user32.OpenClipboard(0):
            return None
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return None
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                text = ctypes.c_wchar_p(ptr).value
                return text or ""
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception as exc:
        logger.debug("ctypes clipboard read failed: %s", exc)

    # Final fallback: subprocess
    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.rstrip("\r\n")
    except Exception as exc:
        logger.debug("PowerShell clipboard read failed: %s", exc)

    return None


def _write_clipboard_text(text: str) -> None:
    """Write text to clipboard using native APIs."""
    # Try win32clipboard first
    try:
        import os
        dll_dir = os.path.join(sys.prefix, 'Lib', 'site-packages', 'pywin32_system32')
        if os.path.isdir(dll_dir):
            os.add_dll_directory(dll_dir)
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("win32clipboard write failed: %s", exc)

    # Fallback: ctypes
    try:
        import ctypes

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        encoded = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            raise RuntimeError("GlobalLock returned NULL")
        try:
            ctypes.memmove(ptr, encoded, len(encoded))
        finally:
            kernel32.GlobalUnlock(handle)

        if user32.OpenClipboard(0):
            try:
                user32.EmptyClipboard()
                user32.SetClipboardData(CF_UNICODETEXT, handle)
            finally:
                user32.CloseClipboard()
        return
    except Exception as exc:
        logger.debug("ctypes clipboard write failed: %s", exc)

    # Final fallback: subprocess
    try:
        import subprocess
        subprocess.run(
            ["powershell", "-Command", f'Set-Clipboard -Value "{text}"'],
            timeout=5, check=True,
        )
        return
    except Exception as exc:
        raise RuntimeError(f"All clipboard write methods failed. Last error: {exc}")
