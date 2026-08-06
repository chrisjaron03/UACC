"""
Artistic Painter — turn UACC into an AI painter that can paint images or preset designs
directly on screen in Microsoft Paint using precise drag-and-drop stroke trajectories.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple


from uacc.actions.schema import ClickAction, DragAction, MouseButton
from uacc.actions.executor import ActionExecutor
from uacc.safety.mouse_sentinel import MouseSentinel

logger = logging.getLogger(__name__)


class ArtisticPainter:
    """Converts images or geometric presets into vector stroke paths and paints
    them in Microsoft Paint using the UACC ActionExecutor."""

    def __init__(
        self,
        executor: Optional[ActionExecutor] = None,
        sentinel: Optional[MouseSentinel] = None,
    ):
        self.executor = executor or ActionExecutor(human_mimicry=False, action_delay_ms=0)
        self.sentinel = sentinel
        import pyautogui
        self._orig_pause = pyautogui.PAUSE
        self._orig_failsafe = pyautogui.FAILSAFE
        pyautogui.PAUSE = 0  # eliminate 50ms idle after every API call
        pyautogui.FAILSAFE = False  # don't abort when mouse reaches screen corner

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
        elif preset_name == "house":
            strokes = self._generate_house(cx, cy)
        elif preset_name == "peacock":
            return self._draw_peacock_direct(cx, cy)
        else:
            return {"success": False, "message": f"Unknown preset: '{preset_name}'"}

        return self._execute_strokes(strokes)

    def draw_image(
        self,
        image_path: str,
        canvas_bounds: Tuple[int, int, int, int],  # (left, top, right, bottom)
        max_strokes: int = 500,
        edge_threshold: int = 100,
    ) -> Dict[str, Any]:
        """Load an image, extract its outline strokes via CV2 pipeline, and paint on screen.

        Uses CLAHE contrast enhancement, adaptive thresholding, skeletonization,
        and intelligent gap-filling for accurate stroke extraction.

        Args:
            image_path: Path to the image file to paint.
            canvas_bounds: Screen coordinates of Paint's drawing canvas (left, top, right, bottom).
            max_strokes: Cap on number of stroke paths to draw.
            edge_threshold: Legacy parameter (kept for API compatibility, not used by CV2 pipeline).
        """
        from uacc.actions.image_processor import process_image_to_paths

        # Step 1: Calculate canvas dimensions and target image size
        canvas_w = max(100, canvas_bounds[2] - canvas_bounds[0])
        canvas_h = max(100, canvas_bounds[3] - canvas_bounds[1])
        margin = 40
        target_w = max(50, canvas_w - margin * 2)
        target_h = max(50, canvas_h - margin * 2)

        # Step 2: Full CV2 processing pipeline — image → stroke paths
        try:
            raw_paths, img_w, img_h = process_image_to_paths(
                image_path,
                target_size=(target_w, target_h),
                min_component_area=10,
                min_path_length=4,
                max_path_length=500,
                gap_max_dist=18,
                gap_max_angle_deg=55.0,
            )
        except FileNotFoundError as exc:
            return {"success": False, "message": f"Failed to load image: {exc}"}
        except Exception as exc:
            return {"success": False, "message": f"Image processing failed: {exc}"}

        if not raw_paths:
            return {"success": False, "message": "No stroke paths extracted from image."}

        # Step 3: Center artwork within canvas
        offset_x = canvas_bounds[0] + (canvas_w - img_w) // 2
        offset_y = canvas_bounds[1] + (canvas_h - img_h) // 2

        # Step 4: Spatial sorting — structural strokes first, then detail
        raw_paths.sort(key=lambda p: len(p), reverse=True)
        primary_count = max(1, int(len(raw_paths) * 0.4))
        structural_paths = raw_paths[:primary_count]
        detail_paths = raw_paths[primary_count:]

        def sort_spatially(paths_list):
            """Sort paths greedily to minimize pen travel distance between consecutive strokes."""
            if not paths_list:
                return []
            paths_list.sort(key=lambda p: (p[0][1], p[0][0]))
            ordered = [paths_list.pop(0)]
            while paths_list:
                last_pt = ordered[-1][-1]
                best_idx = 0
                best_dist = float("inf")
                best_reverse = False

                for idx, p in enumerate(paths_list):
                    d_start = math.hypot(p[0][0] - last_pt[0], p[0][1] - last_pt[1])
                    d_end = math.hypot(p[-1][0] - last_pt[0], p[-1][1] - last_pt[1])
                    if d_start < best_dist:
                        best_dist = d_start
                        best_idx = idx
                        best_reverse = False
                    if d_end < best_dist:
                        best_dist = d_end
                        best_idx = idx
                        best_reverse = True

                next_path = paths_list.pop(best_idx)
                if best_reverse:
                    next_path.reverse()
                ordered.append(next_path)
            return ordered

        ordered_structural = sort_spatially(structural_paths)
        ordered_detail = sort_spatially(detail_paths)
        final_ordered_paths = (ordered_structural + ordered_detail)[:max_strokes]

        logger.info("Generated %d spatially optimized stroke paths", len(final_ordered_paths))

        # Step 5: Convert paths to DragActions using Douglas-Peucker simplification
        # and continuous stroke chaining for fewer mouse down/up cycles
        import numpy as np
        import cv2

        drag_actions = []
        for path in final_ordered_paths:
            # Douglas-Peucker simplification — adapts to curvature instead of fixed step
            pts_array = np.array(path, dtype=np.float32).reshape(-1, 1, 2)
            epsilon = max(1.0, len(path) * 0.02)  # ~2% tolerance
            simplified = cv2.approxPolyDP(pts_array, epsilon, False)
            simplified_path = [(int(p[0][0]), int(p[0][1])) for p in simplified]

            if len(simplified_path) < 2:
                continue

            # Chain consecutive points into continuous strokes
            # Each path becomes a series of connected DragActions that maintain
            # the pen-down state between segments for smoother drawing
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
                        duration_ms=15,
                        reasoning="Drawing outline stroke",
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
        """Generate an enhanced mountain landscape with sun rays, mountain hatching, trees, horizon, and birds."""
        actions = []

        def add_line(p1: Tuple[int, int], p2: Tuple[int, int], desc: str, dur: int = 80):
            actions.append(
                DragAction(
                    start_x=p1[0], start_y=p1[1],
                    end_x=p2[0], end_y=p2[1],
                    button=MouseButton.LEFT, duration_ms=dur,
                    reasoning=desc,
                )
            )

        # 1. Full Sun & Rays
        sun_r = 45
        sun_cx = cx
        sun_cy = cy - 70
        sun_pts = []
        for i in range(25):
            theta = 2 * math.pi * i / 24
            x = sun_cx + sun_r * math.cos(theta)
            y = sun_cy + sun_r * math.sin(theta)
            sun_pts.append((int(x), int(y)))
        
        for i in range(len(sun_pts) - 1):
            add_line(sun_pts[i], sun_pts[i+1], "Drawing sun disk", 40)

        # Sun Rays
        for i in range(8):
            angle = (math.pi * i / 4) + (math.pi / 8)
            x1 = sun_cx + (sun_r + 8) * math.cos(angle)
            y1 = sun_cy + (sun_r + 8) * math.sin(angle)
            x2 = sun_cx + (sun_r + 25) * math.cos(angle)
            y2 = sun_cy + (sun_r + 25) * math.sin(angle)
            add_line((int(x1), int(y1)), (int(x2), int(y2)), "Drawing sun ray", 60)

        # 2. Main Mountain Range
        peaks = [
            (cx - 240, cy + 90),
            (cx - 160, cy - 30),
            (cx - 70, cy + 30),
            (cx + 40, cy - 80),
            (cx + 140, cy + 20),
            (cx + 240, cy + 90)
        ]
        
        for i in range(len(peaks) - 1):
            add_line(peaks[i], peaks[i+1], "Drawing main mountain outline", 90)

        # Main Peak Center Creases & Shading Lines
        creases = [
            (peaks[1], (cx - 160, cy + 90)),
            (peaks[3], (cx + 40, cy + 90)),
        ]
        for top, bot in creases:
            add_line(top, bot, "Drawing mountain peak ridge divide", 80)
            # Add hatching on right side of ridge for 3D depth effect
            for h in range(1, 5):
                t = h / 5.0
                hx1 = int(top[0] + (bot[0] - top[0]) * t)
                hy1 = int(top[1] + (bot[1] - top[1]) * t)
                hx2 = hx1 + 25
                hy2 = hy1 + 15
                add_line((hx1, hy1), (hx2, hy2), "Mountain shadow hatching", 50)

        # 3. Horizon Ground Line
        add_line((cx - 280, cy + 90), (cx + 280, cy + 90), "Drawing ground line", 100)

        # 4. Pine Trees (Silhouettes along the base)
        tree_xs = [cx - 210, cx - 180, cx + 180, cx + 210]
        for tx in tree_xs:
            # Trunk
            add_line((tx, cy + 90), (tx, cy + 50), "Tree trunk", 50)
            # Foliage triangles
            add_line((tx - 15, cy + 75), (tx, cy + 55), "Tree branch", 40)
            add_line((tx, cy + 55), (tx + 15, cy + 75), "Tree branch", 40)
            add_line((tx - 10, cy + 62), (tx, cy + 45), "Tree top", 40)
            add_line((tx, cy + 45), (tx + 10, cy + 62), "Tree top", 40)

        # 5. Birds in the Sky
        bird_centers = [(cx - 150, cy - 110), (cx - 110, cy - 130), (cx + 140, cy - 100)]
        for bx, by in bird_centers:
            add_line((bx - 12, by + 5), (bx, by), "Bird left wing", 40)
            add_line((bx, by), (bx + 12, by + 5), "Bird right wing", 40)

        return actions

    def _generate_house(self, cx: int, cy: int) -> List[DragAction]:
        """Generate vector strokes for a classic house preset (walls, roof, door, window, chimney, tree, sun)."""
        actions = []

        def add_line(p1: Tuple[int, int], p2: Tuple[int, int], desc: str):
            actions.append(
                DragAction(
                    start_x=p1[0], start_y=p1[1],
                    end_x=p2[0], end_y=p2[1],
                    button=MouseButton.LEFT, duration_ms=100,
                    reasoning=desc,
                )
            )

        def add_rect(x1: int, y1: int, x2: int, y2: int, desc: str):
            add_line((x1, y1), (x2, y1), desc)
            add_line((x2, y1), (x2, y2), desc)
            add_line((x2, y2), (x1, y2), desc)
            add_line((x1, y2), (x1, y1), desc)

        # 1. Main House Frame (Walls)
        w, h = 180, 120
        hx1, hy1 = cx - w // 2, cy - 20
        hx2, hy2 = cx + w // 2, hy1 + h
        add_rect(hx1, hy1, hx2, hy2, "House walls")

        # 2. Roof (Triangle)
        apex = (cx, hy1 - 80)
        add_line((hx1 - 15, hy1), apex, "Roof left slope")
        add_line(apex, (hx2 + 15, hy1), "Roof right slope")
        add_line((hx1 - 15, hy1), (hx2 + 15, hy1), "Roof overhang base")

        # 3. Chimney
        ch_x1, ch_y1 = cx + 40, hy1 - 60
        ch_x2, ch_y2 = cx + 60, hy1 - 30
        add_line((ch_x1, ch_y1), (ch_x2, ch_y1), "Chimney top")
        add_line((ch_x1, ch_y1), (ch_x1, hy1 - 40), "Chimney left side")
        add_line((ch_x2, ch_y1), (ch_x2, hy1 - 15), "Chimney right side")

        # 4. Front Door
        dw, dh = 40, 70
        dx1, dy1 = cx - dw // 2, hy2 - dh
        dx2, dy2 = cx + dw // 2, hy2
        add_rect(dx1, dy1, dx2, dy2, "Door frame")
        # Door knob
        add_line((cx + 12, hy2 - 35), (cx + 14, hy2 - 35), "Door knob")

        # 5. Windows (Left and Right)
        # Left window
        lw_x1, lw_y1 = hx1 + 20, hy1 + 20
        lw_x2, lw_y2 = lw_x1 + 35, lw_y1 + 35
        add_rect(lw_x1, lw_y1, lw_x2, lw_y2, "Left window")
        add_line(((lw_x1 + lw_x2) // 2, lw_y1), ((lw_x1 + lw_x2) // 2, lw_y2), "Left window vertical pane")
        add_line((lw_x1, (lw_y1 + lw_y2) // 2), (lw_x2, (lw_y1 + lw_y2) // 2), "Left window horizontal pane")

        # Right window
        rw_x2, rw_y1 = hx2 - 20, hy1 + 20
        rw_x1, rw_y2 = rw_x2 - 35, rw_y1 + 35
        add_rect(rw_x1, rw_y1, rw_x2, rw_y2, "Right window")
        add_line(((rw_x1 + rw_x2) // 2, rw_y1), ((rw_x1 + rw_x2) // 2, rw_y2), "Right window vertical pane")
        add_line((rw_x1, (rw_y1 + rw_y2) // 2), (rw_x2, (rw_y1 + rw_y2) // 2), "Right window horizontal pane")

        # 6. Ground Line
        add_line((cx - 260, hy2), (cx + 260, hy2), "Ground line")

        # 7. Tree on the left
        tx = cx - 180
        add_rect(tx - 10, hy2 - 60, tx + 10, hy2, "Tree trunk")
        # Tree canopy (triangle)
        add_line((tx - 35, hy2 - 60), (tx, hy2 - 120), "Tree canopy left")
        add_line((tx, hy2 - 120), (tx + 35, hy2 - 60), "Tree canopy right")
        add_line((tx - 35, hy2 - 60), (tx + 35, hy2 - 60), "Tree canopy base")

        # 8. Sun on top right
        sx, sy, sr = cx + 180, hy1 - 80, 20
        for i in range(12):
            a1 = 2 * math.pi * i / 12
            a2 = 2 * math.pi * (i + 1) / 12
            add_line((int(sx + sr * math.cos(a1)), int(sy + sr * math.sin(a1))),
                     (int(sx + sr * math.cos(a2)), int(sy + sr * math.sin(a2))), "Sun circle segment")
        # Sun rays
        for i in range(8):
            a = 2 * math.pi * i / 8
            rx1 = int(sx + (sr + 5) * math.cos(a))
            ry1 = int(sy + (sr + 5) * math.sin(a))
            rx2 = int(sx + (sr + 18) * math.cos(a))
            ry2 = int(sy + (sr + 18) * math.sin(a))
            add_line((rx1, ry1), (rx2, ry2), "Sun ray")

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
        """Execute a list of DragActions sequentially using fast inline pyautogui calls."""
        total = len(strokes)
        if total == 0:
            return {"success": False, "message": "No paths or lines generated."}

        logger.info("Executing %d drawing strokes...", total)
        import pyautogui
        from uacc.safety.mouse_sentinel import is_escape_pressed

        # Ensure Pencil tool (black ink) is selected in MS Paint toolbar
        self.executor.execute(ClickAction(x=170, y=105, button=MouseButton.LEFT, reasoning="Select Pencil tool"))

        success_count = 0

        try:
            for idx, action in enumerate(strokes, 1):
                # Safety check — verify Escape key or sentinel killed status
                if is_escape_pressed() or (self.sentinel and self.sentinel.check_killed()):
                    was_escape = is_escape_pressed()
                    msg = (
                        "Drawing halted: Escape key pressed"
                        if was_escape
                        else "Drawing halted: user override detected (mouse moved/dragged)"
                    )
                    logger.warning("Painting halted at stroke %d/%d: %s", idx, total, msg)
                    try:
                        pyautogui.mouseUp()
                    except Exception:
                        pass
                    return {
                        "success": False,
                        "message": msg,
                        "killed": True,
                        "escape_pressed": was_escape,
                        "completed_strokes": idx - 1,
                        "total_strokes": total,
                    }

                # Each stroke is self-contained: position → press → wait → drag → release
                if self.sentinel:
                    self.sentinel.set_moving(True)

                pyautogui.moveTo(action.start_x, action.start_y, duration=0.01)

                if is_escape_pressed():
                    try:
                        pyautogui.mouseUp()
                    except Exception:
                        pass
                    if self.sentinel:
                        self.sentinel.set_moving(False)
                    return {
                        "success": False,
                        "message": "Drawing halted: Escape key pressed",
                        "killed": True,
                        "escape_pressed": True,
                        "completed_strokes": success_count,
                        "total_strokes": total,
                    }

                pyautogui.mouseDown(button=action.button.value)
                time.sleep(0.03)  # let Paint register the button press before dragging

                if is_escape_pressed():
                    try:
                        pyautogui.mouseUp(button=action.button.value)
                    except Exception:
                        pass
                    if self.sentinel:
                        self.sentinel.set_moving(False)
                    return {
                        "success": False,
                        "message": "Drawing halted: Escape key pressed",
                        "killed": True,
                        "escape_pressed": True,
                        "completed_strokes": success_count,
                        "total_strokes": total,
                    }

                seg_duration = max(action.duration_ms, 35) / 1000
                pyautogui.moveTo(action.end_x, action.end_y, duration=seg_duration)
                pyautogui.mouseUp(button=action.button.value)
                success_count += 1
                last_expected_pos = (action.end_x, action.end_y)

                if self.sentinel:
                    self.sentinel.set_expected_position(action.end_x, action.end_y)
                    self.sentinel.set_moving(False)

        except Exception as exc:
            try:
                pyautogui.mouseUp()
            except Exception:
                pass
            if self.sentinel:
                self.sentinel.set_moving(False)
            logger.error("Drawing aborted by exception: %s", exc)
            return {
                "success": False,
                "message": f"Drawing aborted: {exc}",
                "completed_strokes": success_count,
                "total_strokes": total,
            }

        pct = (success_count / total) * 100
        return {
            "success": True,
            "message": f"Completed {success_count}/{total} strokes ({pct:.1f}%)",
            "total_strokes": total,
            "success_strokes": success_count,
        }

    def cleanup(self) -> None:
        """Restore pyautogui global state after painting."""
        import pyautogui
        pyautogui.PAUSE = self._orig_pause
        pyautogui.FAILSAFE = self._orig_failsafe
