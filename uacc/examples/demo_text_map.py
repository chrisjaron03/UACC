"""
Demo: Text Map — capture the current screen and print its structured text map.

Run this to see exactly what a text-only LLM would receive as input.

Usage:
    python examples/demo_text_map.py
"""

import logging
import sys
import time

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

from uacc.core.accessibility import get_ui_tree, get_interactive_elements
from uacc.core.screen_capture import capture_full, get_screen_size
from uacc.core.text_map import build_text_map


def main():
    print("=" * 60)
    print("  UACC — Text Map Demo")
    print("  Capturing screen in 3 seconds...")
    print("  (Switch to the window you want to analyze)")
    print("=" * 60)

    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)

    print("\n📸 Capturing screen...")
    screenshot = capture_full()
    screen_w, screen_h = get_screen_size()
    print(f"  Screen: {screen_w}×{screen_h}")

    print("\n🌳 Extracting accessibility tree...")
    ui_elements = get_ui_tree()
    interactive = get_interactive_elements(ui_elements)
    print(f"  Found {len(interactive)} interactive elements")

    print("\n🗺️  Building text map...")
    active_window = ui_elements[0].name if ui_elements else "Unknown"
    text_map = build_text_map(
        screen_width=screen_w,
        screen_height=screen_h,
        ui_elements=ui_elements,
        active_window=active_window,
    )

    # Print compact format (what text-only LLMs see)
    print("\n" + "=" * 60)
    print("  COMPACT TEXT MAP (sent to text-only LLMs)")
    print("=" * 60)
    print(text_map.to_compact_text())

    # Print YAML format
    print("\n" + "=" * 60)
    print("  YAML FORMAT (alternative structured format)")
    print("=" * 60)
    print(text_map.to_yaml())

    # Save screenshot
    output_path = "demo_text_map_screenshot.png"
    screenshot.save(output_path)
    print(f"\n💾 Screenshot saved to: {output_path}")

    # Summary
    summary = text_map.to_dict()
    print(f"\n📊 Summary:")
    print(f"  Total elements: {summary['element_count']}")
    print(f"  Regions: {len(summary['regions'])}")
    for region in summary["regions"]:
        print(f"    - {region['region']}: {len(region['elements'])} elements")


if __name__ == "__main__":
    main()
