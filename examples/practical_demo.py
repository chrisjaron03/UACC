"""
UACC Practical Demo — standalone automation using UACC's core modules.
No LLM/API key required. Demonstrates: screen capture, text map, window
management, keyboard/mouse control, clipboard, and human mimicry.
"""

import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uacc.core.screen_capture import capture_full, get_screen_size
from uacc.core.accessibility import get_ui_tree, get_interactive_elements
from uacc.core.window_manager import get_active_window, launch_application, list_windows, focus_window
from uacc.core.text_map import build_text_map
from uacc.core.clipboard import read_clipboard, write_clipboard
from uacc.actions.executor import ActionExecutor
from uacc.actions.schema import TypeAction, HotkeyAction, LaunchAction, ClickAction


def section(title):
    print()
    print("=" * 65)
    print(f"  {title}")
    print("=" * 65)


section("1. CAPTURE SCREEN INFO")
screenshot = capture_full()
w, h = get_screen_size()
print(f"   Resolution: {w}x{h}")
print(f"   Screenshot size: {len(screenshot.tobytes())} bytes")
screenshot.save("uacc_demo_screenshot.png")
print(f"   Saved: uacc_demo_screenshot.png")


section("2. ACCESSIBILITY TREE")
ui_elements = get_ui_tree()
interactive = get_interactive_elements(ui_elements)
active_win = get_active_window()
title = active_win.title if active_win else "Unknown"
print(f"   Active window: {title}")
print(f"   UI tree root elements: {len(ui_elements)}")
print(f"   Interactive elements: {len(interactive)}")
for i, el in enumerate(interactive[:6]):
    print(f"     [{el.control_type}] \"{el.name[:45]}\" @ ({el.center[0]}, {el.center[1]})")


section("3. TEXT MAP (what text-only LLMs 'see')")
text_map = build_text_map(w, h, ui_elements, active_window=title)
compact = text_map.to_compact_text()
for line in compact.split("\n")[:15]:
    print(f"   {line}")
print(f"   ... ({len(text_map.all_elements)} total elements)")


section("4. ACTION EXECUTOR")
executor = ActionExecutor(human_mimicry=True, safe_mode=True)
print("   Human mimicry: ON (Bézier curve mouse, variable typing)")
print("   Safe mode: ON (blocks destructive actions)")


section("5. LAUNCH NOTEPAD")
print("   Launching in 2 seconds...")
time.sleep(2)
result = launch_application("notepad")
print(f"   Result: {result['message']}")
time.sleep(1.5)


section("6. TYPE TEXT IN NOTEPAD")
executor.execute(TypeAction(text="Hello from UACC!"))
executor.execute(HotkeyAction(keys=["enter"]))
executor.execute(TypeAction(text="This text was typed by an AI agent controlling this computer."))
executor.execute(HotkeyAction(keys=["enter"]))
executor.execute(TypeAction(text=f"Screen: {w}x{h}  |  Human mimicry: ON  |  Safe mode: ON"))
executor.execute(HotkeyAction(keys=["enter"]))
executor.execute(TypeAction(text="UACC enables any LLM to control a desktop."))
print("   Text typed successfully!")


section("7. CLIPBOARD OPERATIONS")
time.sleep(0.5)
executor.execute(HotkeyAction(keys=["ctrl", "a"]))
time.sleep(0.3)
executor.execute(HotkeyAction(keys=["ctrl", "c"]))
time.sleep(0.5)
clip_text = read_clipboard().get("text", "")
print(f"   Copied from Notepad ({len(clip_text)} chars):")
print(f"   \"{clip_text[:100]}\"")
write_clipboard("UACC demo complete!")
print(f"   Clipboard written: \"UACC demo complete!\"")


section("8. WINDOW MANAGEMENT")
windows = list_windows()
print(f"   Open windows ({len(windows)}):")
for win in windows[:8]:
    t = win.title or "(no title)"
    b = win.bounds
    print(f"     - \"{t[:55]}\" @ ({b[0]}, {b[1]}) {win.width}x{win.height}")


section("SUMMARY")
print()
print("   UACC successfully demonstrated:")
print(f"    1. Screen capture       ({w}x{h}, saved to PNG)")
print(f"    2. Accessibility tree   ({len(ui_elements)} roots, {len(interactive)} interactive)")
print(f"    3. Text map generation  ({len(text_map.all_elements)} elements for text-only LLMs)")
print(f"    4. Human-like mouse/keyboard  (Bézier curves + variable typing)")
print(f"    5. App launched         (Notepad)")
print(f"    6. Text typed           (multi-line)")
print(f"    7. Hotkeys              (Ctrl+A, Ctrl+C)")
print(f"    8. Clipboard            (read {len(clip_text)} chars + write)")
print(f"    9. Window listing       ({len(windows)} windows found)")
print()
print("   All without a vision model, API key, or cloud dependency!")
