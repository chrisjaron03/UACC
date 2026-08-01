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

from uacc.actions.schema import ClickAction, DragAction, MouseButton
from uacc.actions.executor import ActionExecutor
from uacc.core.accessibility import flatten_elements, get_ui_tree, invalidate_tree_cache
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
        max_strokes: int = 1500,
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

        # Step 2: Image Processing (Assess space, fit within canvas bounds with margin)
        canvas_w = max(100, canvas_bounds[2] - canvas_bounds[0])
        canvas_h = max(100, canvas_bounds[3] - canvas_bounds[1])

        # Preserve aspect ratio with protective canvas margin.
        margin = 40
        max_target_w = max(50, canvas_w - margin * 2)
        max_target_h = max(50, canvas_h - margin * 2)
        scale = min(max_target_w / max(1, img.width), max_target_h / max(1, img.height))
        if scale != 1.0:
            new_w = max(50, int(img.width * scale))
            new_h = max(50, int(img.height * scale))
            img = img.resize((new_w, new_h), Image.LANCZOS)
        img_w, img_h = img.size

        # Assess starting offset to center artwork strictly within available canvas space
        offset_x = canvas_bounds[0] + (canvas_w - img_w) // 2
        offset_y = canvas_bounds[1] + (canvas_h - img_h) // 2

        # Grayscale + edge detection with Bilateral Filtering noise reduction.
        #
        # Bilateral filtering smooths out smooth color gradients, skin textures,
        # background noise, and JPEG compression specks while preserving sharp
        # character boundaries and facial/clothing outlines.
        import numpy as np
        import cv2

        gray = ImageOps.grayscale(img)
        arr = np.array(gray)

        # Apply Bilateral Filter to suppress noise specks while preserving outlines
        denoised = cv2.bilateralFilter(arr, d=7, sigmaColor=50, sigmaSpace=50)

        # Mild contrast enhancement to boost subtle outlines without noise amplification
        clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        enhanced = clahe.apply(denoised)

        # --- Edge detection & Contour Extraction ---
        min_path_len = max(12, int(min(img_w, img_h) * 0.025))
        target_mass = max(300, int(arr.size * 0.015))
        raw_paths = []

        # Try Canny first (clean single-pixel edges)
        for hi in (150, 110, 75, 45):
            lo = max(20, int(hi * 0.4))
            edges = cv2.Canny(enhanced, lo, hi)
            contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            candidates = []
            for c in contours:
                # Polygon approximation using Ramer-Douglas-Peucker (RDP) algorithm
                # to simplify jagged pixel contours into smooth vector curves.
                epsilon = max(1.0, min(img_w, img_h) * 0.003)
                approx = cv2.approxPolyDP(c, epsilon, closed=False)
                pts = approx.reshape(-1, 2)
                if len(pts) >= 2:
                    arc_len = cv2.arcLength(c, False)
                    if arc_len >= min_path_len:
                        candidates.append([(int(x), int(y)) for x, y in pts])
            total_pts = sum(len(p) for p in candidates)
            if total_pts >= target_mass:
                raw_paths = candidates
                break
            raw_paths = candidates

        # Fallback: Sobel gradient magnitude thresholding with high noise floor
        if sum(len(p) for p in raw_paths) < target_mass:
            gx = cv2.Sobel(enhanced, cv2.CV_64F, 1, 0, ksize=3)
            gy = cv2.Sobel(enhanced, cv2.CV_64F, 0, 1, ksize=3)
            mag = np.sqrt(gx ** 2 + gy ** 2)
            mag_norm = (mag / (mag.max() + 1e-6) * 255).astype(np.uint8)
            for t in (40, 25, 18):
                _, binary = cv2.threshold(mag_norm, t, 255, cv2.THRESH_BINARY)
                binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
                contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
                candidates = []
                for c in contours:
                    epsilon = max(1.0, min(img_w, img_h) * 0.003)
                    approx = cv2.approxPolyDP(c, epsilon, closed=False)
                    pts = approx.reshape(-1, 2)
                    if len(pts) >= 2 and cv2.arcLength(c, False) >= min_path_len:
                        candidates.append([(int(x), int(y)) for x, y in pts])
                if sum(len(p) for p in candidates) >= target_mass:
                    raw_paths = candidates
                    break
                raw_paths = candidates

        logger.info("Edge detection: %d simplified contours (%d points)", len(raw_paths), sum(len(p) for p in raw_paths))

        if not raw_paths:
            return {"success": False, "message": "No outline paths extracted from image."}

        # Step 3: Smart Full-Character Path Selection (Avoiding Truncation)
        #
        # To draw a complete character (or subject) without leaving the face, lower body,
        # or limbs unpainted:
        # 1. Identify key facial/central feature paths (located in upper-middle region).
        # 2. Divide vertical height into bands (top, middle, bottom) and ensure
        #    structural outlines are selected across all vertical bands.
        # 3. Fill remaining max_strokes quota with secondary details.

        def get_path_bounds(path):
            xs = [pt[0] for pt in path]
            ys = [pt[1] for pt in path]
            return min(xs), min(ys), max(xs), max(ys)

        def path_length(path):
            total = 0.0
            for i in range(len(path) - 1):
                total += math.hypot(path[i+1][0] - path[i][0], path[i+1][1] - path[i][1])
            return total

        # Classify paths
        face_feature_paths = []
        structural_paths = []
        detail_paths = []

        for p in raw_paths:
            plen = path_length(p)
            min_x, min_y, max_x, max_y = get_path_bounds(p)
            cx = (min_x + max_x) / 2.0
            cy = (min_y + max_y) / 2.0

            # Facial / central feature check: upper-middle region, moderate size
            is_facial = (
                (0.20 * img_w <= cx <= 0.80 * img_w) and
                (0.12 * img_h <= cy <= 0.50 * img_h) and
                (10 <= plen <= min(img_w, img_h) * 0.8) and
                (max_x - min_x < img_w * 0.5) and (max_y - min_y < img_h * 0.5)
            )

            if is_facial:
                face_feature_paths.append(p)
            elif plen >= min(img_w, img_h) * 0.12:
                structural_paths.append(p)
            else:
                detail_paths.append(p)

        # Sort structural paths by length (longest first)
        structural_paths.sort(key=lambda p: path_length(p), reverse=True)
        face_feature_paths.sort(key=lambda p: path_length(p), reverse=True)
        detail_paths.sort(key=lambda p: path_length(p), reverse=True)

        # Collect paths ensuring full vertical spatial coverage (top, middle, bottom)
        selected_paths = []
        selected_set = set()

        def add_to_selected(p):
            pid = id(p)
            if pid not in selected_set and len(selected_paths) < max_strokes:
                selected_set.add(pid)
                selected_paths.append(p)

        # 1. Always prioritize facial features first
        for p in face_feature_paths:
            add_to_selected(p)

        # 2. Select structural paths distributed across vertical bands (Top, Middle, Bottom)
        # to ensure the character silhouette is completely drawn from head to toe.
        top_struct = [p for p in structural_paths if get_path_bounds(p)[1] < img_h * 0.33]
        mid_struct = [p for p in structural_paths if img_h * 0.25 <= get_path_bounds(p)[1] <= img_h * 0.70]
        bot_struct = [p for p in structural_paths if get_path_bounds(p)[1] > img_h * 0.60]

        # Interleave structural paths from top, mid, bot bands
        max_len = max(len(top_struct), len(mid_struct), len(bot_struct), len(structural_paths))
        for i in range(max_len):
            if i < len(top_struct): add_to_selected(top_struct[i])
            if i < len(mid_struct): add_to_selected(mid_struct[i])
            if i < len(bot_struct): add_to_selected(bot_struct[i])
            if i < len(structural_paths): add_to_selected(structural_paths[i])
            if len(selected_paths) >= max_strokes:
                break

        # 3. Fill remaining quota with detail paths
        for p in detail_paths:
            add_to_selected(p)
            if len(selected_paths) >= max_strokes:
                break

        # Sort the selected set spatially to minimize pen travel distance
        def sort_spatially(paths_list):
            if not paths_list:
                return []
            paths_copy = list(paths_list)
            paths_copy.sort(key=lambda p: (p[0][1], p[0][0]))
            ordered = [paths_copy.pop(0)]
            while paths_copy:
                last_pt = ordered[-1][-1]
                best_idx = 0
                best_dist = float("inf")
                best_reverse = False

                for idx, p in enumerate(paths_copy):
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

                next_path = paths_copy.pop(best_idx)
                if best_reverse:
                    next_path.reverse()
                ordered.append(next_path)
            return ordered

        final_ordered_paths = sort_spatially(selected_paths)

        logger.info("Generated %d spatially optimized full-character stroke paths", len(final_ordered_paths))

        # Step 4: Convert paths to DragActions (smooth vector strokes)
        drag_actions = []
        for path in final_ordered_paths:
            if len(path) < 2:
                continue
            for i in range(len(path) - 1):
                x1, y1 = path[i]
                x2, y2 = path[i + 1]
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

    def _find_tool_button(self, tool_name: str) -> Optional[Tuple[bool, int, int]]:
        """Locate a Paint ribbon tool button via the UIA accessibility tree.

        Args:
            tool_name: Exact button name to look for (e.g. "Pencil").

        Returns:
            (toggled, center_x, center_y) when the button is exposed by UIA,
            or None when the tree is unavailable or the button is not found.
        """
        try:
            tree = get_ui_tree("Paint", max_depth=6)
            for el in flatten_elements(tree):
                if el.control_type == "Button" and el.name == tool_name:
                    return (bool(el.toggled), el.center[0], el.center[1])
        except Exception as exc:
            logger.debug("UIA scan for '%s' button failed: %s", tool_name, exc)
        return None

    def _ensure_pencil_selected(self) -> Dict[str, Any]:
        """Verify the Pencil tool is selected in Paint's toolbar before drawing.

        Finds the Pencil toggle button via the UIA accessibility tree and
        confirms it is toggled On. When it is not selected, clicks it and
        re-verifies (retrying once). Falls back to the legacy fixed toolbar
        position when the tree does not expose the button.

        Returns:
            Dict with "selected", "method", and "clicked" (the position the
            mouse ended at, or None when no click was needed).
        """
        info = self._find_tool_button("Pencil")

        if info is None:
            logger.warning(
                "Pencil button not exposed via accessibility tree — "
                "falling back to legacy fixed toolbar click"
            )
            self.executor.execute(ClickAction(
                x=170, y=105, button=MouseButton.LEFT,
                reasoning="Select Pencil tool (legacy fallback)",
            ))
            return {"selected": True, "method": "fallback", "clicked": (170, 105)}

        toggled, cx, cy = info
        if toggled:
            logger.info("Pencil tool already selected — no toolbar click needed")
            return {"selected": True, "method": "uia-check", "clicked": None}

        for attempt in (1, 2):
            logger.info(
                "Pencil tool NOT selected — clicking it at (%d, %d) (attempt %d)",
                cx, cy, attempt,
            )
            self.executor.execute(ClickAction(
                x=cx, y=cy, button=MouseButton.LEFT,
                reasoning=f"Select Pencil tool (attempt {attempt})",
            ))
            time.sleep(0.2)
            invalidate_tree_cache()
            info = self._find_tool_button("Pencil")
            if info is not None and info[0]:
                return {"selected": True, "method": "uia-click", "clicked": (cx, cy)}
            if info is not None:
                toggled, cx, cy = info

        logger.warning("Pencil tool still not selected after 2 click attempts")
        return {"selected": False, "method": "uia-click", "clicked": (cx, cy)}

    def _execute_strokes(self, strokes: List[DragAction]) -> Dict[str, Any]:
        """Execute a list of DragActions sequentially using fast inline pyautogui calls."""
        total = len(strokes)
        if total == 0:
            return {"success": False, "message": "No paths or lines generated."}

        logger.info("Executing %d drawing strokes...", total)
        import pyautogui

        # Prime the sentinel: reset any stale kill flag and anchor the expected
        # cursor position to where the mouse currently is. Without this, the
        # expected position left over from a previous tool call makes the
        # sentinel false-kill the drawing before the first stroke starts.
        if self.sentinel:
            cur = pyautogui.position()
            self.sentinel.acknowledge_override()
            self.sentinel.set_expected_position(int(cur.x), int(cur.y))

        # Ensure the Pencil tool (black ink) is selected in MS Paint toolbar.
        # The selection is verified via the UIA accessibility tree; when it is
        # not selected we click the button and re-verify, falling back to the
        # legacy fixed toolbar position only if UIA doesn't expose the button.
        ensure = self._ensure_pencil_selected()
        if ensure.get("clicked") is not None and self.sentinel:
            # Re-anchor the sentinel at the position where the mouse ended up
            # so the per-stroke check_killed() doesn't false-trigger.
            self.sentinel.set_expected_position(*ensure["clicked"])

        success_count = 0

        try:
            for idx, action in enumerate(strokes, 1):
                # Safety check — verify sentinel killed status
                if self.sentinel and self.sentinel.check_killed():
                    logger.warning("User override detected via MouseSentinel at stroke %d/%d", idx, total)
                    try:
                        pyautogui.mouseUp()
                    except Exception:
                        pass
                    return {
                        "success": False,
                        "message": "Drawing halted: user override detected (mouse moved/dragged)",
                        "killed": True,
                        "completed_strokes": idx - 1,
                        "total_strokes": total,
                    }

                # Each stroke is self-contained: position → press → wait → drag → release
                if self.sentinel:
                    self.sentinel.set_moving(True)

                pyautogui.moveTo(action.start_x, action.start_y, duration=0.01)
                pyautogui.mouseDown(button=action.button.value)
                time.sleep(0.03)  # let Paint register the button press before dragging
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
            "tool": "pencil",
            "tool_selection": ensure,
        }

    def cleanup(self) -> None:
        """Restore pyautogui global state after painting."""
        import pyautogui
        pyautogui.PAUSE = self._orig_pause
        pyautogui.FAILSAFE = self._orig_failsafe
