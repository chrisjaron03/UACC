"""
Fetch Image — download images from the internet for UACC drawing tasks.

This module provides a zero-API-key image fetcher that scrapes public image
search engines to find line art (and general images) suitable for UACC's
``paint_image`` tool.  Downloaded images are saved to ``~/.uacc/fetched_images/``
and the local path is returned so callers can feed it straight into the
drawing pipeline.

**Agent workflow**:
    1. ``fetch_line_art(query="cat")``  →  get ``image_path``
    2. ``paint_image(image_path=<path>)``  →  draw it

Search strategy (tried in order, first success wins):
    1. Bing Image Search (HTML scrape, no API key)
    2. DuckDuckGo instant-answer images
    3. OpenClipart.org (CC0 clipart — great for line art)
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import html
import io
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

# ── Storage ──────────────────────────────────────────────────

_FETCH_DIR = Path.home() / ".uacc" / "fetched_images"


def _ensure_fetch_dir() -> Path:
    """Create the fetch directory if it doesn't exist."""
    _FETCH_DIR.mkdir(parents=True, exist_ok=True)
    return _FETCH_DIR


# ── Shared HTTP Helpers ──────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# File extensions we consider valid images
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tiff"}


def _http_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    max_retries: int = 2,
) -> bytes:
    """Perform a GET request with retry and exponential backoff."""
    hdrs = dict(_HEADERS)
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = 0.5 * (2 ** attempt)  # 0.5s, 1s
                time.sleep(wait)
                logger.debug("Retry %d for %s after %s", attempt + 1, url, exc)

    raise last_exc  # type: ignore[misc]


def _looks_like_image_url(url: str) -> bool:
    """Quick heuristic: does the URL look like it points to an image?"""
    parsed = urllib.parse.urlparse(url)
    path_lower = parsed.path.lower()
    # Direct extension match
    if any(path_lower.endswith(ext) for ext in _IMAGE_EXTENSIONS):
        return True
    # Common image hosting patterns
    image_hosts = ["imgur.com", "i.imgur.com", "pbs.twimg.com",
                   "upload.wikimedia.org", "images.unsplash.com",
                   "openclipart.org", "svgsilh.com", "clipartmax.com"]
    if any(h in parsed.netloc.lower() for h in image_hosts):
        return True
    # URL params that hint at images
    if any(kw in url.lower() for kw in ["image", "img", "photo", "thumb", "pic"]):
        return True
    return True  # Default: try it anyway


def _url_fingerprint(url: str) -> str:
    """Return a short hash of a URL for deduplication."""
    return hashlib.md5(url.encode()).hexdigest()[:12]


# ═════════════════════════════════════════════════════════════
#  Search Backends
# ═════════════════════════════════════════════════════════════


def _search_bing_images(query: str, max_results: int = 15) -> List[Dict[str, str]]:
    """Scrape Bing Image Search results (no API key required).

    Returns a list of dicts with keys: ``url``, ``title``.
    """
    results: List[Dict[str, str]] = []
    try:
        params = urllib.parse.urlencode({
            "q": query,
            "form": "HDRSC2",
            "first": "1",
        })
        url = f"https://www.bing.com/images/search?{params}"
        body = _http_get(url).decode("utf-8", errors="replace")

        # Bing embeds image URLs in "murl" parameter of anchor data attributes
        # Pattern: murl&quot;:&quot;https://...&quot;
        for match in re.finditer(r'murl&quot;:&quot;(https?://[^&]+?)&quot;', body):
            img_url = html.unescape(match.group(1))
            if img_url and len(results) < max_results:
                results.append({"url": img_url, "title": ""})

        # Alternative pattern: data-src attributes on image thumbnails
        if not results:
            for match in re.finditer(r'src2="(https?://[^"]+)"', body):
                img_url = match.group(1)
                if img_url and len(results) < max_results:
                    results.append({"url": img_url, "title": ""})

    except Exception as exc:
        logger.debug("Bing image search failed: %s", exc)

    return results


def _search_duckduckgo_images(query: str, max_results: int = 12) -> List[Dict[str, str]]:
    """Search DuckDuckGo for images matching *query*.

    Uses the DDG VQD token flow.  May fail if DDG changes their API.
    Returns a list of dicts with keys: ``url``, ``title``.
    """
    results: List[Dict[str, str]] = []
    try:
        # Step 1: Get VQD token
        token_url = f"https://duckduckgo.com/?q={urllib.parse.quote(query)}&iar=images&iax=images&ia=images"
        body = _http_get(token_url).decode("utf-8", errors="replace")
        vqd_match = re.search(r"vqd=['\"]([^'\"]+)['\"]", body)
        if not vqd_match:
            vqd_match = re.search(r"vqd=(\d[\d-]+)", body)
        if not vqd_match:
            return []
        vqd = vqd_match.group(1)

        # Step 2: Fetch image results
        params = urllib.parse.urlencode({
            "l": "us-en", "o": "json", "q": query,
            "vqd": vqd, "f": ",,,,,", "p": "1",
        })
        api_url = f"https://duckduckgo.com/i.js?{params}"
        data = json.loads(_http_get(
            api_url,
            headers={"Referer": "https://duckduckgo.com/", "Accept": "application/json"},
        ).decode("utf-8", errors="replace"))

        for item in data.get("results", [])[:max_results]:
            img_url = item.get("image", "")
            if img_url:
                results.append({"url": img_url, "title": item.get("title", "")})

    except Exception as exc:
        logger.debug("DuckDuckGo image search failed: %s", exc)

    return results


def _search_openclipart(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """Search OpenClipart for free SVG/PNG clipart (CC0 license).

    Returns a list of dicts with keys: ``url``, ``title``.
    """
    results: List[Dict[str, str]] = []
    try:
        params = urllib.parse.urlencode({"query": query, "amount": max_results, "sort": "downloads"})
        url = f"https://openclipart.org/search/json/?{params}"
        data = json.loads(_http_get(url).decode("utf-8", errors="replace"))

        for item in data.get("payload", []):
            svg_url = item.get("svg", {}).get("png_2400px", "")
            if not svg_url:
                svg_url = item.get("svg", {}).get("png_800px", "")
            if svg_url:
                results.append({"url": svg_url, "title": item.get("title", "")})

    except Exception as exc:
        logger.debug("OpenClipart search failed: %s", exc)

    return results


def _search_images(query: str, max_results: int = 15) -> List[Dict[str, str]]:
    """Search multiple backends for images, deduplicate, and return merged results."""
    seen_urls: Set[str] = set()
    all_results: List[Dict[str, str]] = []

    def _add_results(items: List[Dict[str, str]]) -> None:
        for item in items:
            fp = _url_fingerprint(item["url"])
            if fp not in seen_urls:
                seen_urls.add(fp)
                all_results.append(item)

    # Try Bing first (most reliable for HTML scraping)
    results = _search_bing_images(query, max_results)
    if results:
        logger.info("Found %d images via Bing", len(results))
        _add_results(results)

    # Also try DuckDuckGo for diversity
    results = _search_duckduckgo_images(query, max_results)
    if results:
        logger.info("Found %d images via DuckDuckGo", len(results))
        _add_results(results)

    # Also try OpenClipart (CC0 clipart — great for line art)
    results = _search_openclipart(query, max_results)
    if results:
        logger.info("Found %d images via OpenClipart", len(results))
        _add_results(results)

    # Cap at max_results
    return all_results[:max_results]


# ── Image Download & Processing ──────────────────────────────


def _download_image(url: str, timeout: int = 20) -> Optional[Image.Image]:
    """Download an image from *url* and return it as a PIL Image."""
    try:
        # Content-type pre-check via HEAD (skip obvious non-images)
        try:
            head_req = urllib.request.Request(url, method="HEAD", headers=dict(_HEADERS))
            with urllib.request.urlopen(head_req, timeout=8) as resp:
                ct = resp.headers.get("Content-Type", "")
                if ct and "image" not in ct.lower() and "octet" not in ct.lower():
                    logger.debug("Skipping non-image URL (Content-Type=%s): %s", ct, url)
                    return None
        except Exception:
            pass  # HEAD failed — still try GET

        raw = _http_get(url, headers={"Accept": "image/*,*/*;q=0.8"}, timeout=timeout)
        return Image.open(io.BytesIO(raw))
    except Exception as exc:
        logger.debug("Failed to download %s: %s", url, exc)
        return None


def _download_candidates_parallel(
    candidates: List[Dict[str, str]],
    max_workers: int = 4,
    timeout: int = 20,
) -> List[Tuple[Dict[str, str], Image.Image]]:
    """Download multiple image candidates in parallel.

    Returns a list of (candidate_dict, PIL.Image) tuples for successful downloads.
    Order is preserved from the input list.
    """
    results: List[Tuple[Optional[Dict[str, str]], Optional[Image.Image]]] = [
        (None, None)
    ] * len(candidates)

    def _download_one(idx: int, candidate: Dict[str, str]) -> Tuple[int, Optional[Image.Image]]:
        img = _download_image(candidate["url"], timeout=timeout)
        return idx, img

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_download_one, i, c): i
            for i, c in enumerate(candidates)
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                idx, img = future.result()
                if img is not None:
                    results[idx] = (candidates[idx], img)
            except Exception:
                pass

    # Filter out failures, preserve order
    return [(c, img) for c, img in results if c is not None and img is not None]


# ── Image Quality Scoring ────────────────────────────────────


def _has_transparency(img: Image.Image) -> bool:
    """Check if the image has a usable alpha channel (transparency)."""
    if img.mode in ("RGBA", "LA", "PA"):
        alpha = img.getchannel("A")
        # If alpha has a good range, it has real transparency
        extrema = alpha.getextrema()
        return extrema[0] < 200  # Some pixels are not fully opaque
    return False


def _score_image(
    img: Image.Image,
    target_size: Tuple[int, int],
    prefer_line_art: bool = True,
) -> float:
    """Score an image candidate from 0.0 (worst) to 1.0 (best).

    Considers: resolution match, aspect ratio, line-art suitability,
    transparency, and image cleanliness.
    """
    score = 0.0

    # ── Size / resolution score (0–0.25) ─────────────────────
    tw, th = target_size
    size_ratio = min(img.width, tw) / max(tw, 1) * min(img.height, th) / max(th, 1)
    # Penalise tiny images heavily
    if img.width < 80 or img.height < 80:
        size_ratio *= 0.1
    elif img.width < 150 or img.height < 150:
        size_ratio *= 0.5
    score += min(size_ratio, 1.0) * 0.25

    # ── Aspect ratio score (0–0.15) ──────────────────────────
    aspect = img.width / max(img.height, 1)
    if 0.3 < aspect < 3.0:
        # Closer to 1:1 = better for drawing
        aspect_score = 1.0 - abs(1.0 - aspect) * 0.3
        score += max(aspect_score, 0.0) * 0.15
    # else: extreme ratio → 0 points

    # ── Transparency bonus (0–0.15) ──────────────────────────
    if _has_transparency(img):
        score += 0.15

    # ── Line-art suitability (0–0.30) ────────────────────────
    if prefer_line_art:
        gray = img.convert("L")
        hist = gray.histogram()
        total = sum(hist)
        if total > 0:
            dark = sum(hist[:60])
            light = sum(hist[190:])
            bimodal_ratio = (dark + light) / total
            # Line art: bimodal distribution (lots of black + white)
            if bimodal_ratio > 0.70:
                score += 0.30
            elif bimodal_ratio > 0.50:
                score += 0.22
            elif bimodal_ratio > 0.30:
                score += 0.12
            else:
                score += 0.03
    else:
        # For non-line-art, give full marks for being colorful
        score += 0.20

    # ── Edge density bonus (0–0.15) ──────────────────────────
    # Good line art has clear edges
    if prefer_line_art:
        try:
            edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
            edge_hist = edges.histogram()
            total_edge = sum(edge_hist)
            if total_edge > 0:
                strong_edges = sum(edge_hist[100:])
                edge_ratio = strong_edges / total_edge
                if 0.02 < edge_ratio < 0.40:
                    score += 0.15  # Sweet spot for line art
                elif edge_ratio > 0.005:
                    score += 0.08
        except Exception:
            pass

    return min(score, 1.0)


def _is_valid_image(img: Image.Image, min_size: int = 80) -> bool:
    """Quick heuristic check: is this image usable at all?

    Rejects tiny images and extreme aspect ratios.
    """
    if img.width < min_size or img.height < min_size:
        return False

    aspect = img.width / max(img.height, 1)
    if aspect < 0.2 or aspect > 5.0:
        return False

    return True


# ── Image Processing Pipeline ────────────────────────────────


def _auto_crop(img: Image.Image, border_threshold: int = 245, min_border: int = 10) -> Image.Image:
    """Auto-crop excessive whitespace/border from an image.

    Trims uniform-colored borders, keeping at least `min_border` pixels of padding.
    """
    try:
        # Convert to grayscale for border detection
        gray = img.convert("L")

        # Find the bounding box of non-white content
        bbox = gray.point(lambda p: 0 if p > border_threshold else 255).getbbox()
        if bbox is None:
            return img  # Entirely white/blank — return as-is

        x0, y0, x1, y1 = bbox

        # Add padding back
        x0 = max(0, x0 - min_border)
        y0 = max(0, y0 - min_border)
        x1 = min(img.width, x1 + min_border)
        y1 = min(img.height, y1 + min_border)

        # Only crop if we'd remove a meaningful border (>15% on any side)
        w_removed = (img.width - (x1 - x0)) / max(img.width, 1)
        h_removed = (img.height - (y1 - y0)) / max(img.height, 1)
        if w_removed > 0.10 or h_removed > 0.10:
            return img.crop((x0, y0, x1, y1))
    except Exception as exc:
        logger.debug("Auto-crop failed: %s", exc)

    return img


def _enhance_for_line_art(img: Image.Image) -> Image.Image:
    """Enhance contrast and sharpness to produce cleaner line art.

    Applies edge-preserving contrast boost before B&W thresholding.
    """
    try:
        # Boost contrast
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)

        # Sharpen edges
        enhancer = ImageEnhance.Sharpness(img)
        img = enhancer.enhance(1.8)
    except Exception as exc:
        logger.debug("Enhancement failed: %s", exc)

    return img


def _convert_to_line_art(img: Image.Image) -> Image.Image:
    """Convert an image to clean black-and-white line art.

    Uses edge-preserving contrast enhancement followed by adaptive
    thresholding so the result is optimised for UACC's edge-tracing pipeline.
    """
    # Enhance before conversion
    img = _enhance_for_line_art(img)

    # Convert to grayscale
    gray = img.convert("L")

    # Adaptive-ish threshold — use slightly lower threshold for better detail
    bw = gray.point(lambda p: 255 if p > 180 else 0, mode="1")

    # Convert back to RGB for compatibility with paint_image pipeline
    return bw.convert("RGB")


def _save_metadata_sidecar(
    save_path: Path,
    query: str,
    full_query: str,
    source_url: str,
    style: str,
    width: int,
    height: int,
    score: float,
) -> None:
    """Save a JSON metadata sidecar alongside the image for traceability."""
    meta_path = save_path.with_suffix(".json")
    meta = {
        "query": query,
        "full_query": full_query,
        "source_url": source_url,
        "style": style,
        "width": width,
        "height": height,
        "quality_score": round(score, 3),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "image_file": save_path.name,
    }
    try:
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("Failed to write metadata sidecar: %s", exc)


# ═════════════════════════════════════════════════════════════
#  Query Building
# ═════════════════════════════════════════════════════════════


# Style keywords used to enrich search queries
_STYLE_KEYWORDS = {
    "outline": "line art outline black and white simple clean",
    "coloring_page": "coloring page printable line drawing black white",
    "sketch": "pencil sketch line drawing black white",
    "silhouette": "silhouette black outline simple",
    "cartoon": "cartoon clipart simple illustration",
    "realistic": "realistic drawing detailed illustration",
}

# Category-specific keyword hints for even better search results
_CATEGORY_KEYWORDS = {
    "animal": "animal clipart",
    "vehicle": "vehicle transport clipart",
    "nature": "nature plant flower clipart",
    "object": "object item clipart",
    "character": "character person figure clipart",
    "symbol": "symbol icon sign clipart",
    "building": "building architecture clipart",
    "food": "food drink clipart",
}


def _build_search_query(
    query: str,
    style: str = "outline",
    category: Optional[str] = None,
    prefer_transparent: bool = False,
) -> str:
    """Build an enriched search query for better image results."""
    parts = [query]

    # Style suffix
    style_suffix = _STYLE_KEYWORDS.get(style.lower(), _STYLE_KEYWORDS["outline"])
    parts.append(style_suffix)

    # Category hint
    if category and category.lower() in _CATEGORY_KEYWORDS:
        parts.append(_CATEGORY_KEYWORDS[category.lower()])

    # Transparency hint
    if prefer_transparent:
        parts.append("transparent background PNG")

    return " ".join(parts)


# ═════════════════════════════════════════════════════════════
#  Public API
# ═════════════════════════════════════════════════════════════


def fetch_line_art(
    query: str,
    style: str = "outline",
    category: Optional[str] = None,
    max_candidates: int = 12,
    convert_bw: bool = True,
    target_size: Optional[Tuple[int, int]] = None,
) -> Dict[str, Any]:
    """Search the internet for a line art image and save it locally.

    **IMPORTANT FOR AGENTS**: You MUST call this tool before ``paint_image``
    or ``paint_preset`` when you need to draw something and don't already
    have an image file path.  The workflow is::

        1. result = fetch_line_art(query="cat")
        2. paint_image(image_path=result["image_path"])

    Args:
        query: What to search for (e.g. "cat", "dragon", "rose flower").
        style: Art style hint — ``"outline"``, ``"coloring_page"``,
               ``"sketch"``, ``"silhouette"``, ``"cartoon"``, or
               ``"realistic"``.  Appended to the search query for better
               results.  Default: ``"outline"``.
        category: Optional subject category to refine search —
                  ``"animal"``, ``"vehicle"``, ``"nature"``, ``"object"``,
                  ``"character"``, ``"symbol"``, ``"building"``,
                  ``"food"``.  Leave as None for auto-detect.
        max_candidates: How many search results to try downloading
                        before giving up (default 12).
        convert_bw: If True, convert the downloaded image to clean
                    black-and-white line art.
        target_size: Optional (width, height) to resize the image to.
                     Defaults to 800×800 if not specified.

    Returns:
        Dict with keys:
            ``success`` (bool), ``image_path`` (str), ``query`` (str),
            ``source_url`` (str), ``width`` (int), ``height`` (int),
            ``quality_score`` (float 0–1), ``message`` (str),
            ``next_step`` (str — tells agent what to do next).
    """
    target_size = target_size or (800, 800)

    # ── Build enriched search query ──────────────────────────
    full_query = _build_search_query(
        query, style=style, category=category,
        prefer_transparent=(style in ("outline", "coloring_page", "silhouette")),
    )

    logger.info("Fetching line art for query: '%s' (style=%s)", query, style)

    # ── Search (multi-backend, deduplicated) ─────────────────
    results = _search_images(full_query, max_results=max_candidates)

    if not results:
        # Retry with a simpler query
        fallback_query = f"{query} line art black white"
        logger.info("No results — retrying with: '%s'", fallback_query)
        results = _search_images(fallback_query, max_results=max_candidates)

    if not results:
        # Final fallback — extremely simple
        bare_query = f"{query} clipart PNG"
        logger.info("Still no results — retrying with: '%s'", bare_query)
        results = _search_images(bare_query, max_results=max_candidates)

    if not results:
        return {
            "success": False,
            "message": f"No images found for query: '{query}'. Try a simpler or more common subject.",
            "query": full_query,
            "next_step": "Try fetch_line_art with a different query or simpler terms.",
        }

    # ── Download candidates in parallel ──────────────────────
    downloaded = _download_candidates_parallel(results, max_workers=4)

    if not downloaded:
        return {
            "success": False,
            "message": f"Found {len(results)} image URLs but could not download any. Network or image format issues.",
            "query": full_query,
            "candidates_tried": len(results),
            "next_step": "Try fetch_line_art with a different query.",
        }

    # ── Score and rank candidates ────────────────────────────
    scored: List[Tuple[float, Dict[str, str], Image.Image]] = []
    for candidate, img in downloaded:
        if not _is_valid_image(img):
            continue
        sc = _score_image(img, target_size, prefer_line_art=True)
        scored.append((sc, candidate, img))

    if not scored:
        # Fallback: use first downloaded image regardless of score
        candidate, img = downloaded[0]
        scored = [(0.1, candidate, img)]

    # Sort by score (best first)
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_candidate, best_img = scored[0]

    source_url = best_candidate["url"]
    logger.info(
        "Best candidate (score=%.2f) from: %s (%dx%d)",
        best_score, source_url, best_img.width, best_img.height,
    )

    # ── Process ──────────────────────────────────────────────
    # Auto-crop excessive whitespace
    best_img = _auto_crop(best_img)

    if convert_bw:
        best_img = _convert_to_line_art(best_img)

    # Resize maintaining aspect ratio
    best_img.thumbnail(target_size, Image.LANCZOS)

    # ── Save to disk ─────────────────────────────────────────
    save_dir = _ensure_fetch_dir()
    slug = re.sub(r"[^a-z0-9]+", "_", query.lower().strip())[:40]
    ts = int(time.time())
    filename = f"{slug}_{ts}.png"
    save_path = save_dir / filename

    # Ensure RGB for PNG save
    if best_img.mode not in ("RGB", "RGBA"):
        best_img = best_img.convert("RGB")

    best_img.save(str(save_path), "PNG")
    logger.info("Saved line art to: %s (%dx%d)", save_path, best_img.width, best_img.height)

    # Save metadata sidecar
    _save_metadata_sidecar(
        save_path, query, full_query, source_url, style,
        best_img.width, best_img.height, best_score,
    )

    return {
        "success": True,
        "image_path": str(save_path),
        "query": full_query,
        "source_url": source_url,
        "width": best_img.width,
        "height": best_img.height,
        "quality_score": round(best_score, 3),
        "candidates_scored": len(scored),
        "message": (
            f"✅ Line art saved to {save_path} "
            f"({best_img.width}×{best_img.height}px, score={best_score:.2f}). "
            f"Use paint_image(image_path=\"{save_path}\") to draw it."
        ),
        "next_step": (
            f"Now call paint_image(image_path=\"{save_path}\") to draw this image, "
            f"or paint_preset if you want a predefined shape instead."
        ),
    }


def fetch_image(
    query: str,
    style: str = "cartoon",
    category: Optional[str] = None,
    max_candidates: int = 12,
    target_size: Optional[Tuple[int, int]] = None,
) -> Dict[str, Any]:
    """Search the internet for a general image and save it locally.

    Unlike ``fetch_line_art``, this does NOT convert to black-and-white.
    Use this when you need a reference photo, colored clipart, icon, or
    any non-line-art image.

    **IMPORTANT FOR AGENTS**: If you need an image to draw, use
    ``fetch_line_art`` instead — it optimises the image for tracing.

    Args:
        query: What to search for (e.g. "sunset", "logo", "icon").
        style: Style hint — ``"cartoon"``, ``"realistic"``, ``"outline"``,
               or any of the styles from ``fetch_line_art``.
        category: Optional category hint (same as ``fetch_line_art``).
        max_candidates: How many search results to try (default 12).
        target_size: Optional (width, height). Defaults to 800×800.

    Returns:
        Dict with ``success``, ``image_path``, ``query``, ``source_url``,
        ``width``, ``height``, ``quality_score``, ``message``.
    """
    target_size = target_size or (800, 800)

    full_query = _build_search_query(
        query, style=style, category=category,
        prefer_transparent=False,
    )

    logger.info("Fetching image for query: '%s' (style=%s)", query, style)

    results = _search_images(full_query, max_results=max_candidates)
    if not results:
        fallback_query = f"{query} image PNG"
        results = _search_images(fallback_query, max_results=max_candidates)

    if not results:
        return {
            "success": False,
            "message": f"No images found for query: '{query}'.",
            "query": full_query,
        }

    downloaded = _download_candidates_parallel(results, max_workers=4)
    if not downloaded:
        return {
            "success": False,
            "message": f"Found URLs but could not download any images for: '{query}'.",
            "query": full_query,
        }

    # Score candidates (not preferring line art)
    scored: List[Tuple[float, Dict[str, str], Image.Image]] = []
    for candidate, img in downloaded:
        if not _is_valid_image(img):
            continue
        sc = _score_image(img, target_size, prefer_line_art=False)
        scored.append((sc, candidate, img))

    if not scored:
        candidate, img = downloaded[0]
        scored = [(0.1, candidate, img)]

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_candidate, best_img = scored[0]
    source_url = best_candidate["url"]

    # Auto-crop and resize
    best_img = _auto_crop(best_img)
    best_img.thumbnail(target_size, Image.LANCZOS)

    # Save
    save_dir = _ensure_fetch_dir()
    slug = re.sub(r"[^a-z0-9]+", "_", query.lower().strip())[:40]
    ts = int(time.time())
    filename = f"{slug}_{ts}.png"
    save_path = save_dir / filename

    if best_img.mode not in ("RGB", "RGBA"):
        best_img = best_img.convert("RGB")

    best_img.save(str(save_path), "PNG")
    logger.info("Saved image to: %s (%dx%d)", save_path, best_img.width, best_img.height)

    _save_metadata_sidecar(
        save_path, query, full_query, source_url, style,
        best_img.width, best_img.height, best_score,
    )

    return {
        "success": True,
        "image_path": str(save_path),
        "query": full_query,
        "source_url": source_url,
        "width": best_img.width,
        "height": best_img.height,
        "quality_score": round(best_score, 3),
        "message": (
            f"✅ Image saved to {save_path} "
            f"({best_img.width}×{best_img.height}px, score={best_score:.2f})."
        ),
    }


def list_fetched_images() -> List[Dict[str, Any]]:
    """List all previously fetched images (line art and general).

    Returns:
        List of dicts with ``path``, ``filename``, ``size_bytes``,
        ``width``, ``height``, and ``metadata`` (from sidecar JSON
        if available) for each cached image.

    **TIP FOR AGENTS**: Call this before ``fetch_line_art`` to check
    if a suitable image was already downloaded in a previous session.
    Each ``path`` can be passed to ``paint_image(image_path=...)`` directly.
    """
    save_dir = _ensure_fetch_dir()
    images = []
    for f in sorted(save_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True):
        entry: Dict[str, Any] = {
            "path": str(f),
            "filename": f.name,
            "size_bytes": f.stat().st_size,
        }
        try:
            img = Image.open(f)
            entry["width"] = img.width
            entry["height"] = img.height
        except Exception:
            pass

        # Load sidecar metadata if available
        meta_path = f.with_suffix(".json")
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                entry["metadata"] = meta
            except Exception:
                pass

        images.append(entry)

    return images
