"""
Demo: Grid Overlay — capture the screen and save a gridded + marked image.

Shows what vision LLMs see: a screenshot with a coordinate grid and
numbered badges on interactive elements.

Usage:
    python examples/demo_grid.py
    python examples/demo_grid.py --mode fine
"""

import argparse
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

from uacc.core.accessibility import get_ui_tree, flatten_elements
from uacc.core.grid_encoder import overlay_grid, overlay_markers, build_marker_legend
from uacc.core.screen_capture import capture_full, get_screen_size


def main():
    parser = argparse.ArgumentParser(description="UACC Grid Overlay Demo")
    parser.add_argument(
        "--mode",
        choices=["coarse", "medium", "fine", "micro"],
        default="medium",
        help="Grid density (default: medium)",
    )
    parser.add_argument(
        "--no-markers",
        action="store_true",
        help="Skip element markers (just show grid)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print(f"  UACC — Grid Overlay Demo (mode: {args.mode})")
    print("  Capturing screen in 3 seconds...")
    print("=" * 60)

    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)

    # Capture
    print("\n📸 Capturing screen...")
    screenshot = capture_full()
    screen_w, screen_h = get_screen_size()
    print(f"  Screen: {screen_w}×{screen_h}")

    # Grid overlay
    print(f"\n📐 Overlaying {args.mode} grid...")
    gridded = overlay_grid(screenshot, mode=args.mode)
    grid_output = f"demo_grid_{args.mode}.png"
    gridded.save(grid_output)
    print(f"  💾 Saved: {grid_output}")

    # Markers
    if not args.no_markers:
        print("\n🔢 Adding element markers...")
        ui_elements = get_ui_tree()
        flat = flatten_elements(ui_elements)
        interactive = [el for el in flat if el.clickable or el.editable or el.expandable]
        print(f"  Found {len(interactive)} interactive elements")

        marked = overlay_markers(screenshot, flat)
        marked_output = "demo_grid_markers.png"
        marked.save(marked_output)
        print(f"  💾 Saved: {marked_output}")

        # Combined: grid + markers
        gridded_marked = overlay_markers(gridded, flat)
        combined_output = f"demo_grid_{args.mode}_markers.png"
        gridded_marked.save(combined_output)
        print(f"  💾 Saved: {combined_output}")

        # Print legend
        legend = build_marker_legend(flat)
        print(f"\n📋 Element Legend:")
        print(legend)

    print("\n✅ Done! Open the saved images to see the grid overlays.")


if __name__ == "__main__":
    main()
