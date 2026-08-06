"""
Image Processor — CV2-based image processing pipeline for accurate stroke extraction.

Handles transparent PNG compositing, contrast enhancement (CLAHE), adaptive
thresholding, noise removal, skeletonization, intelligent gap-filling, and
DFS path extraction from 1px skeleton strokes.
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ── Image Loading & Alpha Compositing ─────────────────────────

def load_image(image_path: str) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """Load an image from disk, handling transparent PNGs by compositing onto white.

    Returns:
        Tuple of (BGR image composited on white, alpha foreground mask or None).
        The alpha mask is 255 where the subject is opaque, 0 where transparent.
    """
    img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"Image not found or unreadable: {image_path}")

    alpha_mask = None
    if len(img.shape) == 3 and img.shape[2] == 4:
        # Extract alpha mask: 255 where alpha > 20 (subject), 0 where transparent
        alpha_mask = (img[:, :, 3] > 20).astype(np.uint8) * 255
        # Dilate slightly to catch anti-aliasing fringe pixels
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        alpha_mask = cv2.dilate(alpha_mask, kernel, iterations=1)

    return composite_on_white(img), alpha_mask


def composite_on_white(img: np.ndarray) -> np.ndarray:
    """Composite a potentially-transparent BGRA image onto a white background."""
    if img is None:
        raise FileNotFoundError("Input image is None.")
    if len(img.shape) == 3 and img.shape[2] == 4:
        bgr = img[:, :, :3].astype(np.float32)
        alpha = img[:, :, 3].astype(np.float32) / 255.0
        white = np.full_like(bgr, 255.0)
        out = bgr * alpha[:, :, None] + white * (1.0 - alpha[:, :, None])
        return out.astype(np.uint8)
    return img


# ── Preprocessing ─────────────────────────────────────────────

def preprocess_for_strokes(
    bgr_img: np.ndarray,
    foreground_mask: Optional[np.ndarray] = None,
    clahe_clip: float = 1.9,
    clahe_grid: Tuple[int, int] = (8, 8),
    bilateral_d: int = 7,
    bilateral_sigma_color: int = 30,
    bilateral_sigma_space: int = 30,
    adaptive_block_size: int = 17,
    adaptive_c: int = 6,
) -> np.ndarray:
    """Convert a BGR image to a clean binary mask of black strokes (foreground=255).

    Pipeline: grayscale -> CLAHE contrast -> bilateral filter -> adaptive threshold -> median blur.
    If a foreground_mask is provided, background regions are zeroed out after thresholding.

    Returns:
        Binary mask where 255 = stroke foreground, 0 = background.
    """
    gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)

    # CLAHE for local contrast enhancement
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=clahe_grid)
    gray = clahe.apply(gray)

    # Bilateral filter -- smooth noise while preserving edges
    gray = cv2.bilateralFilter(gray, d=bilateral_d, sigmaColor=bilateral_sigma_color,
                               sigmaSpace=bilateral_sigma_space)

    # Adaptive threshold -- preserves line art feel
    bw = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        adaptive_block_size, adaptive_c,
    )

    # Clean tiny specks with median blur
    bw = cv2.medianBlur(bw, 3)

    # Invert: black strokes become white foreground
    result = 255 - bw

    # Mask out transparent background if alpha mask is available
    if foreground_mask is not None:
        resized_mask = foreground_mask
        if foreground_mask.shape[:2] != result.shape[:2]:
            resized_mask = cv2.resize(foreground_mask, (result.shape[1], result.shape[0]),
                                     interpolation=cv2.INTER_NEAREST)
        result = cv2.bitwise_and(result, resized_mask)

    return result


def preprocess_canny(
    bgr_img: np.ndarray,
    foreground_mask: Optional[np.ndarray] = None,
    canny_low: int = 50,
    canny_high: int = 150,
    blur_ksize: int = 5,
) -> np.ndarray:
    """Canny edge detection pipeline for complex colored images (e.g. photos, renders).

    Better than adaptive thresholding for images with solid color regions and
    gradient shading, since it only captures actual edges rather than texture.

    Pipeline: grayscale -> Gaussian blur -> Canny edges -> dilate -> mask.

    Returns:
        Binary mask where 255 = edge foreground, 0 = background.
    """
    gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)

    # Gaussian blur to reduce noise before edge detection
    blurred = cv2.GaussianBlur(gray, (blur_ksize, blur_ksize), 0)

    # Canny edge detection
    edges = cv2.Canny(blurred, canny_low, canny_high)

    # Slight dilation to connect nearby edge fragments
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    edges = cv2.dilate(edges, kernel, iterations=1)

    # Mask out transparent background if alpha mask is available
    if foreground_mask is not None:
        resized_mask = foreground_mask
        if foreground_mask.shape[:2] != edges.shape[:2]:
            resized_mask = cv2.resize(foreground_mask, (edges.shape[1], edges.shape[0]),
                                     interpolation=cv2.INTER_NEAREST)
        edges = cv2.bitwise_and(edges, resized_mask)

    return edges


# ── Noise Removal ─────────────────────────────────────────────

def clean_small_components(binary: np.ndarray, min_area: int = 10) -> np.ndarray:
    """Remove connected components smaller than min_area pixels."""
    fg = (binary > 0).astype(np.uint8)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)

    clean = np.zeros_like(binary)
    for i in range(1, num):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            clean[labels == i] = 255

    return clean


# ── Skeletonization ───────────────────────────────────────────

def skeletonize_binary(binary: np.ndarray) -> np.ndarray:
    """Reduce binary foreground to 1px-wide center-line skeleton.

    Tries cv2.ximgproc.thinning first, then skimage, then morphological fallback.
    Input: 255 foreground, 0 background.
    Returns: 255 skeleton foreground, 0 background.
    """
    fg = (binary > 0).astype(np.uint8)

    # Try OpenCV contrib thinning (fastest)
    try:
        skel = cv2.ximgproc.thinning(fg * 255)
        return (skel > 0).astype(np.uint8) * 255
    except Exception:
        pass

    # Try scikit-image skeletonize
    try:
        from skimage.morphology import skeletonize
        skel = skeletonize(fg.astype(bool))
        return skel.astype(np.uint8) * 255
    except Exception:
        pass

    # Morphological skeleton fallback
    size = np.size(fg)
    skel = np.zeros(fg.shape, np.uint8)
    element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    img = fg.copy()
    while True:
        eroded = cv2.erode(img, element)
        temp = cv2.dilate(eroded, element)
        temp = cv2.subtract(img, temp)
        skel = cv2.bitwise_or(skel, temp)
        img = eroded.copy()
        zeros = size - cv2.countNonZero(img)
        if zeros == size:
            break

    return skel * 255


# ── Endpoint Detection & Gap Filling ─────────────────────────

def _find_endpoints(skel: np.ndarray) -> List[Tuple[int, int]]:
    """Find skeleton endpoints (pixels with exactly 1 neighbor)."""
    s = (skel > 0).astype(np.uint8)
    neighbor_counts = cv2.filter2D(
        s, -1, np.ones((3, 3), np.uint8), borderType=cv2.BORDER_CONSTANT
    ) - s
    ys, xs = np.where((s == 1) & (neighbor_counts == 1))
    return list(zip(xs.tolist(), ys.tolist()))


def _endpoint_direction(skel: np.ndarray, x: int, y: int) -> np.ndarray:
    """Return a unit vector pointing from the endpoint into the stroke interior."""
    s = (skel > 0).astype(np.uint8)
    h, w = s.shape
    pts = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and s[ny, nx]:
                pts.append((dx, dy))
    if not pts:
        return np.array([0.0, 0.0], dtype=np.float32)
    v = np.array(pts[0], dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _angle_between(u: np.ndarray, v: np.ndarray) -> float:
    """Angle in radians between two vectors."""
    nu, nv = np.linalg.norm(u), np.linalg.norm(v)
    if nu == 0 or nv == 0:
        return np.pi
    c = np.clip(np.dot(u, v) / (nu * nv), -1.0, 1.0)
    return float(np.arccos(c))


def fill_skeleton_gaps(
    skeleton: np.ndarray,
    max_dist: int = 18,
    max_angle_deg: float = 55.0,
) -> np.ndarray:
    """Intelligently connect broken skeleton strokes by pairing nearby endpoints.

    Uses direction-aware mutual best-match pairing to bridge gaps without
    creating spurious connections.

    Args:
        skeleton: 255 foreground skeleton.
        max_dist: Maximum pixel distance between connectable endpoints.
        max_angle_deg: Maximum angular deviation from endpoint direction (degrees).

    Returns:
        Skeleton with gap-filling lines drawn.
    """
    skel_fg = (skeleton > 0).astype(np.uint8)
    numc, comp = cv2.connectedComponents(skel_fg, connectivity=8)

    endpoints = _find_endpoints(skeleton)
    if not endpoints:
        return skeleton

    max_angle = np.deg2rad(max_angle_deg)

    # Precompute endpoint properties
    ep_data = []
    for x, y in endpoints:
        d = _endpoint_direction(skeleton, x, y)
        c = int(comp[y, x]) if 0 <= y < comp.shape[0] and 0 <= x < comp.shape[1] else 0
        ep_data.append({"pt": (x, y), "dir": d, "comp": c})

    # Build candidate scores — only pair endpoints from different components
    candidates = {}
    for i, a in enumerate(ep_data):
        ax, ay = a["pt"]
        for j, b in enumerate(ep_data):
            if j <= i:
                continue
            bx, by = b["pt"]
            if a["comp"] == 0 or b["comp"] == 0 or a["comp"] == b["comp"]:
                continue
            dx, dy = bx - ax, by - ay
            dist = float(np.hypot(dx, dy))
            if dist > max_dist or dist < 2:
                continue
            vec_ab = np.array([dx, dy], dtype=np.float32)
            vec_ba = -vec_ab
            ang_a = _angle_between(a["dir"], vec_ab)
            ang_b = _angle_between(b["dir"], vec_ba)
            if ang_a > max_angle or ang_b > max_angle:
                continue
            score = dist + 10.0 * (ang_a + ang_b)
            candidates[(i, j)] = score

    # Greedy mutual best pairing
    best_for: dict = {}
    for (i, j), score in candidates.items():
        if i not in best_for or score < best_for[i][0]:
            best_for[i] = (score, j)
        if j not in best_for or score < best_for[j][0]:
            best_for[j] = (score, i)

    used: set = set()
    filled = skeleton.copy()
    pair_count = 0
    for i, (score_i, j) in best_for.items():
        if i in used or j in used:
            continue
        if j in best_for and best_for[j][1] == i:
            ai = ep_data[i]["pt"]
            bj = ep_data[j]["pt"]
            cv2.line(filled, ai, bj, 255, 1, cv2.LINE_AA)
            used.add(i)
            used.add(j)
            pair_count += 1

    logger.debug("Gap-filled %d endpoint pairs", pair_count)
    return filled


# ── Path Extraction from Skeleton ─────────────────────────────

def extract_stroke_paths(
    skeleton: np.ndarray,
    min_path_length: int = 4,
    max_path_length: int = 500,
) -> List[List[Tuple[int, int]]]:
    """Extract contiguous stroke paths from a 1px skeleton using DFS tracing.

    Returns a list of paths, where each path is a list of (x, y) pixel coordinates.
    """
    s = (skeleton > 0).astype(np.uint8)
    height, width = s.shape

    visited = set()
    raw_paths: List[List[Tuple[int, int]]] = []

    def get_unvisited_neighbors(x: int, y: int) -> List[Tuple[int, int]]:
        neighbors = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height and s[ny, nx] and (nx, ny) not in visited:
                    neighbors.append((nx, ny))
        return neighbors

    # Start tracing from endpoints first (pixels with exactly 1 neighbor) for cleaner paths
    endpoints = _find_endpoints(skeleton)
    start_points = [(x, y) for x, y in endpoints if (x, y) not in visited]

    # Then scan remaining unvisited skeleton pixels
    def all_start_points():
        yield from start_points
        for y in range(height):
            for x in range(width):
                if s[y, x] and (x, y) not in visited:
                    yield (x, y)

    for sx, sy in all_start_points():
        if (sx, sy) in visited:
            continue

        path = [(sx, sy)]
        visited.add((sx, sy))
        cx, cy = sx, sy

        while True:
            neighbors = get_unvisited_neighbors(cx, cy)
            if not neighbors:
                break
            # Prefer cardinal directions over diagonals for smoother paths
            neighbors.sort(key=lambda p: abs(p[0] - cx) + abs(p[1] - cy))
            nx, ny = neighbors[0]
            path.append((nx, ny))
            visited.add((nx, ny))
            cx, cy = nx, ny
            if len(path) >= max_path_length:
                break

        if len(path) >= min_path_length:
            raw_paths.append(path)

    return raw_paths


# ── Full Pipeline ─────────────────────────────────────────────

def process_image_to_paths(
    image_path: str,
    target_size: Tuple[int, int] = (500, 500),
    min_component_area: int = 10,
    min_path_length: int = 4,
    max_path_length: int = 500,
    gap_max_dist: int = 18,
    gap_max_angle_deg: float = 55.0,
) -> Tuple[List[List[Tuple[int, int]]], int, int]:
    """Full pipeline: load image -> preprocess -> skeletonize -> gap-fill -> extract paths.

    Automatically selects the best preprocessing strategy:
    - Images with alpha channel (isolated subjects): Canny edge detection + alpha masking
    - Images without alpha (line art, sketches): Adaptive thresholding

    Args:
        image_path: Path to the source image file.
        target_size: Maximum (width, height) to resize to while preserving aspect ratio.
        min_component_area: Minimum connected component area to keep (noise filter).
        min_path_length: Minimum stroke path length in pixels.
        max_path_length: Maximum single stroke path length.
        gap_max_dist: Maximum distance for gap-filling endpoint connections.
        gap_max_angle_deg: Maximum angular deviation for gap-filling.

    Returns:
        Tuple of (paths, image_width, image_height) where paths is a list of
        coordinate sequences [(x,y), ...].
    """
    # Load and handle alpha -- also get foreground mask if PNG with transparency
    img, alpha_mask = load_image(image_path)

    # Resize to fit target while preserving aspect ratio
    h, w = img.shape[:2]
    tw, th = target_size
    scale = min(tw / w, th / h, 1.0)  # Don't upscale
    if scale < 1.0:
        new_w, new_h = int(w * scale), int(h * scale)
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        if alpha_mask is not None:
            alpha_mask = cv2.resize(alpha_mask, (new_w, new_h),
                                    interpolation=cv2.INTER_NEAREST)
    img_h, img_w = img.shape[:2]

    logger.info("Processing image %s (%dx%d) for stroke extraction", image_path, img_w, img_h)

    # Select preprocessing strategy based on image type
    if alpha_mask is not None:
        # Complex colored image with alpha -- use Canny for clean outlines
        logger.info("Alpha channel detected -- using Canny edge pipeline with foreground masking")
        binary = preprocess_canny(img, foreground_mask=alpha_mask)
    else:
        # Line art / no alpha -- use adaptive thresholding
        binary = preprocess_for_strokes(img)

    logger.debug("Preprocessing produced %d foreground pixels", cv2.countNonZero(binary))

    # Clean small noise components (enforce minimum area of 25 for alpha images)
    effective_min_area = max(min_component_area, 25) if alpha_mask is not None else min_component_area
    clean = clean_small_components(binary, min_area=effective_min_area)
    logger.debug("After noise removal: %d foreground pixels", cv2.countNonZero(clean))

    # Morphological close to merge nearby edge fragments before skeletonization
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    clean = cv2.morphologyEx(clean, cv2.MORPH_CLOSE, kernel)

    # Skeletonize to 1px strokes
    skel = skeletonize_binary(clean)
    logger.debug("Skeleton: %d foreground pixels", cv2.countNonZero(skel))

    # Fill gaps between broken strokes
    filled = fill_skeleton_gaps(skel, max_dist=gap_max_dist,
                                max_angle_deg=gap_max_angle_deg)

    # Extract paths from skeleton
    paths = extract_stroke_paths(filled, min_path_length=min_path_length,
                                  max_path_length=max_path_length)

    logger.info("Extracted %d stroke paths from image", len(paths))

    return paths, img_w, img_h
