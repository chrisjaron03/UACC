"""
Artistic Painter — turn UACC into an AI painter that can paint images or preset designs
directly on screen in Microsoft Paint using precise drag-and-drop stroke trajectories.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageFilter, ImageOps
import pyautogui

from uacc.actions.schema import DragAction, MouseButton, ClickAction, HotkeyAction
from uacc.actions.executor import ActionExecutor

logger = logging.getLogger(__name__)


class ArtisticPainter:
    """Converts images or geometric presets into vector stroke paths and paints
    them in Microsoft Paint using the UACC ActionExecutor."""

    def __init__(self, executor: Optional[ActionExecutor] = None):
        self.executor = executor or ActionExecutor(human_mimicry=False, action_delay_ms=5)

    def draw_preset(self, preset_name: str, canvas_center: Tuple[int, int]) -> Dict[str, Any]:
        """Paint a built-in masterpiece design by name.

        Presets: "rose", "galaxy", "peacock", "mountains".
        """
        preset_name = preset_name.lower().strip()
        cx, cy = canvas_center

        logger.info("Painting preset art: '%s' around (%d, %d)", preset_name, cx, cy)

        if preset_name == "rose":
            strokes = self._generate_rose(cx, cy)
        elif preset_name == "galaxy":
            strokes = self._generate_galaxy(cx, cy)
        elif preset_name == "mountains":
            strokes = self._generate_mountains(cx, cy)
        elif preset_name == "peacock":
            return self._draw_peacock_direct(cx, cy)
        else:
            return {"success": False, "message": f"Unknown preset: '{preset_name}'"}

        return self._execute_strokes(strokes)

    def draw_image(
        self,
        image_path: str,
        canvas_bounds: Tuple[int, int, int, int],  # (left, top, right, bottom)
        max_strokes: int = 150,
        edge_threshold: int = 100,
    ) -> Dict[str, Any]:
        """Load an image, extract its outline contours, and paint it on screen.

        Args:
            image_path: Path to the image file to paint.
            canvas_bounds: Screen coordinates of Paint's drawing canvas (left, top, right, bottom).
            max_strokes: Cap on number of strokes to avoid infinite execution.
            edge_threshold: Threshold to detect edges (higher = fewer lines, faster).
        """
        try:
            img = Image.open(image_path)
        except Exception as exc:
            return {"success": False, "message": f"Failed to load image: {exc}"}

        # Step 1: Image Processing (Resize to fit canvas and extract edges)
        canvas_w = canvas_bounds[2] - canvas_bounds[0]
        canvas_h = canvas_bounds[3] - canvas_bounds[1]

        # Preserve aspect ratio
        img.thumbnail((canvas_w - 40, canvas_h - 40))
        img_w, img_h = img.size

        # Offset to center within the canvas
        offset_x = canvas_bounds[0] + (canvas_w - img_w) // 2
        offset_y = canvas_bounds[1] + (canvas_h - img_h) // 2

        # Grayscale + find edges
        gray = ImageOps.grayscale(img)
        edges = gray.filter(ImageFilter.FIND_EDGES)
        
        # Binary thresholding
        binary_edges = edges.point(lambda p: 255 if p > edge_threshold else 0)
        width, height = binary_edges.size
        pixels = binary_edges.load()

        # Step 2: Path Tracing (Contiguous DFS tracking of edge pixels)
        visited = set()
        strokes = []

        def get_neighbors(x, y):
            neighbors = []
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        neighbors.append((nx, ny))
            return neighbors

        # Traverse and find contiguous lines
        for y in range(0, height, 2):  # Step 2 to downsample slightly
            for x in range(0, width, 2):
                if pixels[x, y] == 255 and (x, y) not in visited:
                    # Start a new stroke path
                    path = []
                    curr_x, curr_y = x, y
                    path.append((curr_x, curr_y))
                    visited.add((curr_x, curr_y))

                    # Follow the line
                    while True:
                        next_pixel = None
                        for nx, ny in get_neighbors(curr_x, curr_y):
                            if pixels[nx, ny] == 255 and (nx, ny) not in visited:
                                next_pixel = (nx, ny)
                                break
                        if next_pixel:
                            curr_x, curr_y = next_pixel
                            path.append((curr_x, curr_y))
                            visited.add((curr_x, curr_y))
                            if len(path) > 30:  # Cap single path length to avoid huge vectors
                                break
                        else:
                            break

                    if len(path) > 3:  # Filter out short noise dots
                        strokes.append(path)

        # Cap the total strokes
        strokes = strokes[:max_strokes]
        logger.info("Generated %d outline paths from image", len(strokes))

        # Step 3: Convert paths to DragActions (downsampled for 6x speed-up)
        drag_actions = []
        for path in strokes:
            simplified_path = []
            step = 6  # Take every 6th point along the path
            for idx in range(0, len(path), step):
                simplified_path.append(path[idx])
            if path[-1] not in simplified_path:
                simplified_path.append(path[-1])

            for i in range(len(simplified_path) - 1):
                x1, y1 = simplified_path[i]
                x2, y2 = simplified_path[i + 1]
                drag_actions.append(
                    DragAction(
                        start_x=int(x1 + offset_x),
                        start_y=int(y1 + offset_y),
                        end_x=int(x2 + offset_x),
                        end_y=int(y2 + offset_y),
                        button=MouseButton.LEFT,
                        duration_ms=40,  # Fast drawing strokes
                        reasoning="Tracing edge outline",
                    )
                )

        return self._execute_strokes(drag_actions)

    # ── Masterpiece Preset Generators ─────────────────────────

    def _generate_rose(self, cx: int, cy: int) -> List[DragAction]:
        """Generate a mathematical rose curve (Rhodonea curve)."""
        actions = []
        n, d = 5, 1  # 5-lobed rose
        k = n / d
        a = 150     # Radius/size
        steps = 180

        points = []
        for i in range(steps + 1):
            theta = (2 * math.pi * i) / steps
            r = a * math.cos(k * theta)
            x = cx + r * math.cos(theta)
            y = cy + r * math.sin(theta)
            points.append((int(x), int(y)))

        for i in range(len(points) - 1):
            actions.append(
                DragAction(
                    start_x=points[i][0],
                    start_y=points[i][1],
                    end_x=points[i+1][0],
                    end_y=points[i+1][1],
                    button=MouseButton.LEFT,
                    duration_ms=50,
                    reasoning="Drawing rose petal curve",
                )
            )
        return actions

    def _generate_galaxy(self, cx: int, cy: int) -> List[DragAction]:
        """Generate a double spiral galaxy pattern."""
        actions = []
        arms = 2
        rotations = 3.5
        max_r = 180
        steps = 150

        # Arm 1 & Arm 2
        for arm in range(arms):
            points = []
            angle_offset = arm * math.pi
            for i in range(steps):
                t = i / steps
                r = t * max_r
                theta = t * rotations * 2 * math.pi + angle_offset
                x = cx + r * math.cos(theta)
                y = cy + r * math.sin(theta)
                points.append((int(x), int(y)))

            for i in range(len(points) - 1):
                actions.append(
                    DragAction(
                        start_x=points[i][0],
                        start_y=points[i][1],
                        end_x=points[i+1][0],
                        end_y=points[i+1][1],
                        button=MouseButton.LEFT,
                        duration_ms=40,
                        reasoning="Drawing galaxy arm spiral",
                    )
                )
        return actions

    def _generate_mountains(self, cx: int, cy: int) -> List[DragAction]:
        """Generate a mountain range silhouette with a rising sun."""
        actions = []
        
        # 1. Draw Sun (ellipse)
        sun_r = 50
        sun_cx = cx
        sun_cy = cy - 60
        sun_pts = []
        for i in range(21):
            theta = math.pi * i / 20  # Half circle top
            x = sun_cx + sun_r * math.cos(theta)
            y = sun_cy - sun_r * math.sin(theta)
            sun_pts.append((int(x), int(y)))
        
        for i in range(len(sun_pts) - 1):
            actions.append(
                DragAction(
                    start_x=sun_pts[i][0],
                    start_y=sun_pts[i][1],
                    end_x=sun_pts[i+1][0],
                    end_y=sun_pts[i+1][1],
                    button=MouseButton.LEFT,
                    duration_ms=60,
                    reasoning="Drawing sun",
                )
            )

        # 2. Draw Mountain ridges
        peaks = [
            (cx - 200, cy + 80),
            (cx - 100, cy - 20),
            (cx, cy + 40),
            (cx + 120, cy - 50),
            (cx + 220, cy + 80)
        ]
        
        for i in range(len(peaks) - 1):
            actions.append(
                DragAction(
                    start_x=peaks[i][0],
                    start_y=peaks[i][1],
                    end_x=peaks[i+1][0],
                    end_y=peaks[i+1][1],
                    button=MouseButton.LEFT,
                    duration_ms=100,
                    reasoning="Drawing mountain ridge line",
                )
            )
            
        # Draw second background ridge
        peaks2 = [
            (cx - 150, cy + 80),
            (cx - 40, cy + 10),
            (cx + 60, cy - 10),
            (cx + 170, cy + 80)
        ]
        for i in range(len(peaks2) - 1):
            actions.append(
                DragAction(
                    start_x=peaks2[i][0],
                    start_y=peaks2[i][1],
                    end_x=peaks2[i+1][0],
                    end_y=peaks2[i+1][1],
                    button=MouseButton.LEFT,
                    duration_ms=100,
                    reasoning="Drawing background mountain ridge",
                )
            )

        return actions

    def _draw_peacock_direct(self, cx: int, cy: int) -> Dict[str, Any]:
        """Robust direct drawing of the famous peacock preset."""
        # Standard utility helpers
        def curve_points(x0, y0, x1, y1, cx, cy, n=8):
            pts = []
            for i in range(n + 1):
                t = i / n
                x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t**2 * x1
                y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t**2 * y1
                pts.append((int(x), int(y)))
            return pts

        def connect_points(pts):
            acts = []
            for i in range(len(pts) - 1):
                acts.append(
                    DragAction(
                        start_x=pts[i][0], start_y=pts[i][1],
                        end_x=pts[i+1][0], end_y=pts[i+1][1],
                        button=MouseButton.LEFT, duration_ms=100,
                        reasoning="Peacock drawing path"
                    )
                )
            return acts

        def ellipse(ecx, ecy, rx, ry, steps=16):
            pts = []
            for i in range(steps + 1):
                theta = 2 * math.pi * i / steps
                pts.append((int(ecx + rx * math.cos(theta)), int(ecy + ry * math.sin(theta))))
            return connect_points(pts)

        all_actions = []
        tail_origin_x, tail_origin_y = cx - 40, cy + 20

        # Draw tail feathers
        feather_angles = [(-45, 200), (-25, 170), (25, 170), (45, 200)]
        for angle_deg, length in feather_angles:
            angle = math.radians(angle_deg)
            tx = tail_origin_x + length * math.cos(angle)
            ty = tail_origin_y + length * math.sin(angle)

            shaft = curve_points(
                tail_origin_x, tail_origin_y, tx, ty,
                tail_origin_x + 30 * math.cos(angle + 0.1),
                tail_origin_y + 30 * math.sin(angle + 0.1),
                n=8
            )
            all_actions.extend(connect_points(shaft))
            all_actions.extend(ellipse(tx, ty, 15, 8, steps=12))

        # Body
        all_actions.extend(ellipse(cx + 10, cy + 20, 30, 50, steps=20))
        # Neck
        neck = curve_points(cx + 10, cy - 20, cx + 50, cy - 100, cx + 40, cy - 60, n=8)
        all_actions.extend(connect_points(neck))
        # Head
        all_actions.extend(ellipse(cx + 50, cy - 100, 10, 12, steps=12))

        return self._execute_strokes(all_actions)

    def _execute_strokes(self, strokes: List[DragAction]) -> Dict[str, Any]:
        """Execute a list of DragActions sequentially."""
        total = len(strokes)
        if total == 0:
            return {"success": False, "message": "No paths or lines generated."}

        logger.info("Executing %d drawing strokes...", total)
        success_count = 0

        # Hold mouse click down safety checks in pyautogui
        for idx, action in enumerate(strokes, 1):
            res = self.executor.execute(action)
            if res.get("success"):
                success_count += 1
            # Add small pause so user can abort if necessary
            time.sleep(0.01)

        pct = (success_count / total) * 100
        return {
            "success": success_count > 0,
            "message": f"Successfully completed {success_count}/{total} strokes ({pct:.1f}%)",
            "total_strokes": total,
            "success_strokes": success_count,
        }
