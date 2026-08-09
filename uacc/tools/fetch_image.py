from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Default directory for downloaded/generated reference images
DEFAULT_IMAGE_DIR = Path.home() / ".uacc" / "images"

# Keywords/Patterns indicating specific entities (characters, figures, monuments)
ENTITY_KEYWORDS = {
    # Public figures / celebrities / historical
    "spiderman", "spider-man", "batman", "superman", "ironman", "iron man",
    "naruto", "goku", "pikachu", "mickey", "mickey mouse", "elvis", "mona lisa",
    "statue of liberty", "liberty statue", "eiffel tower", "taj mahal", "pyramids",
    "big ben", "colosseum", "mount rushmore", "white house", "empire state",
    "donald trump", "obama", "elon musk", "messi", "ronaldo", "lebron",
    "anime", "marvel", "dc", "pokemon", "disney"
}


def _sanitize_filename(name: str) -> str:
    """Sanitize string for use as a filename."""
    clean = re.sub(r'[^a-zA-Z0-9_\-]', '_', name.lower())
    clean = re.sub(r'_+', '_', clean).strip('_')
    return clean[:50] or "reference_image"


def classify_query_source(query: str) -> str:
    """Classify query into 'url', 'web', or 'pollinations'.
    
    - Direct HTTP/HTTPS links -> 'url'
    - Known specific subjects, characters, figures, monuments -> 'web'
    - General scenes, concepts, objects -> 'pollinations'
    """
    query_str = query.strip()
    if query_str.lower().startswith(("http://", "https://")):
        return "url"

    q_lower = query_str.lower()
    # Check if query contains any entity keyword or proper noun pattern
    for kw in ENTITY_KEYWORDS:
        if kw in q_lower:
            return "web"

    # Also check if capitalized words (e.g. "Spider Man", "Statue of Liberty") indicate a specific proper noun
    words = query_str.split()
    if len(words) <= 3 and any(w.istitle() for w in words):
        # Heuristic: Short query with capitalized words is likely a named entity
        return "web"

    return "pollinations"


def _download_url(url: str, save_path: Path, timeout: int = 10) -> bool:
    """Download an image from a URL to save_path and verify it is a valid image."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read()
            if not data:
                return False
            
            # Save data temporarily
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(data)

            # Verify image format via PIL
            with Image.open(save_path) as img:
                img.verify()
            return True
    except Exception as e:
        logger.warning(f"Failed to download image from {url}: {e}")
        if save_path.exists():
            try:
                save_path.unlink()
            except Exception:
                pass
        return False


def _fetch_from_pollinations(prompt: str, save_path: Path) -> bool:
    """Fetch image generated via Pollinations AI API optimized for clean edge detection and sketch tracing."""
    enhanced_prompt = f"{prompt.strip()} clean high contrast black and white vector line art stencil outline on pure white background"
    encoded_prompt = urllib.parse.quote(enhanced_prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=512&height=512&nologo=true"
    logger.info(f"Fetching Pollinations AI image for prompt '{prompt}'...")
    return _download_url(url, save_path, timeout=15)


def _fetch_from_web_search(query: str, save_path: Path) -> bool:
    """Fetch image for specific entities using DuckDuckGo Search API / Wikimedia Commons, optimized for line art."""
    search_query = f"{query.strip()} clean high contrast black and white vector line art stencil outline clipart"
    # 1. Try DuckDuckGo Image Search via ddgs / public API endpoint
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.images(search_query, max_results=5))
            for res in results:
                img_url = res.get("image")
                if img_url and _download_url(img_url, save_path, timeout=8):
                    logger.info(f"Downloaded web search image for '{query}' from DuckDuckGo")
                    return True
    except Exception as e:
        logger.debug(f"DuckDuckGo search attempt failed: {e}")

    # 2. Try Wikimedia Commons Search API
    try:
        encoded_q = urllib.parse.quote(search_query)
        wiki_url = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch={encoded_q}&gsrnamespace=6&prop=imageinfo&iiprop=url&format=json"
        req = urllib.request.Request(wiki_url, headers={"User-Agent": "UACC/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            pages = data.get("query", {}).get("pages", {})
            for page_id, page_data in pages.items():
                imageinfo = page_data.get("imageinfo", [])
                if imageinfo:
                    img_url = imageinfo[0].get("url")
                    if img_url and _download_url(img_url, save_path, timeout=8):
                        logger.info(f"Downloaded Wikimedia image for '{query}'")
                        return True
    except Exception as e:
        logger.debug(f"Wikimedia search attempt failed: {e}")

    # 3. Fallback to Pollinations if web search fails
    logger.info(f"Web search yielded no direct results for '{query}'. Falling back to Pollinations AI...")
    return _fetch_from_pollinations(query, save_path)


def _optimize_image_for_edge_detection(save_path: Path) -> None:
    """Post-process fetched image to ensure high-contrast line art on a clean white canvas for easy edge detection."""
    try:
        if not save_path.exists():
            return
        with Image.open(save_path) as img:
            img_rgba = img.convert("RGBA")
            bg = Image.new("RGBA", img_rgba.size, (255, 255, 255, 255))
            composite = Image.alpha_composite(bg, img_rgba).convert("RGB")
            composite.save(save_path, "PNG")
    except Exception as e:
        logger.debug(f"Image edge detection optimization failed: {e}")


def _generate_fallback_canvas(query: str, save_path: Path) -> bool:
    """Generate a clean local reference outline image using PIL if all network methods fail."""
    try:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (512, 512), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        # Draw a clean outline box & banner
        draw.rectangle([(20, 20), (492, 492)], outline=(0, 0, 0), width=4)
        draw.rectangle([(40, 40), (472, 100)], fill=(230, 230, 230), outline=(0, 0, 0), width=2)
        
        # Draw label
        text = f"Ref: {query[:30]}"
        draw.text((60, 60), text, fill=(0, 0, 0))

        # Draw stylized placeholder geometric silhouette
        draw.ellipse([(150, 150), (362, 362)], outline=(0, 0, 0), width=4)
        draw.line([(150, 362), (362, 150)], fill=(0, 0, 0), width=3)
        draw.line([(150, 150), (362, 362)], fill=(0, 0, 0), width=3)

        img.save(save_path, "PNG")
        logger.info(f"Generated local fallback reference canvas at {save_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to generate fallback canvas: {e}")
        return False


from uacc.tools.registry import tool


@tool(name="fetch_image", risk_level="read")
def fetch_reference_image(
    query: str,
    output_path: Optional[str] = None,
    source: str = "auto"
) -> Dict[str, Any]:
    """Fetch or generate a reference image for UACC drawing tasks.
    
    Args:
        query: Image topic, character name, scene description, or image URL.
        output_path: Optional absolute or relative file path to save image.
        source: 'auto', 'pollinations', 'web', 'url', or 'fallback'.

    Returns:
        Dict with success status, image_path, source used, dimensions, and message.
    """
    if not query or not query.strip():
        return {"success": False, "error": "Query parameter cannot be empty"}

    query_clean = query.strip()
    
    # Determine output path
    if output_path and output_path.strip():
        target_path = Path(output_path.strip()).resolve()
    else:
        filename = f"{_sanitize_filename(query_clean)}.png"
        target_path = DEFAULT_IMAGE_DIR / filename

    # Classify source strategy if 'auto'
    selected_source = source.lower()
    if selected_source == "auto":
        selected_source = classify_query_source(query_clean)

    success = False
    used_source = selected_source

    if selected_source == "url":
        success = _download_url(query_clean, target_path)
    elif selected_source == "pollinations":
        success = _fetch_from_pollinations(query_clean, target_path)
        if not success:
            logger.info("Pollinations failed. Retrying via Web search...")
            success = _fetch_from_web_search(query_clean, target_path)
            used_source = "web"
    elif selected_source == "web":
        success = _fetch_from_web_search(query_clean, target_path)
        if not success:
            logger.info("Web search failed. Retrying via Pollinations...")
            success = _fetch_from_pollinations(query_clean, target_path)
            used_source = "pollinations"

    # Final offline fallback if all network attempts failed
    if not success or not target_path.exists():
        logger.warning(f"All online fetch methods failed for '{query_clean}'. Using local fallback generator.")
        success = _generate_fallback_canvas(query_clean, target_path)
        used_source = "fallback"

    if success and target_path.exists():
        _optimize_image_for_edge_detection(target_path)
        try:
            with Image.open(target_path) as img:
                w, h = img.size
                file_size = target_path.stat().st_size
                return {
                    "success": True,
                    "image_path": str(target_path),
                    "query": query_clean,
                    "source": used_source,
                    "width": w,
                    "height": h,
                    "file_size_bytes": file_size,
                    "message": f"Successfully fetched reference image for '{query_clean}' via {used_source}"
                }
        except Exception as e:
            return {"success": False, "error": f"Image file corrupted or invalid: {e}"}

    return {"success": False, "error": f"Failed to fetch image for '{query_clean}'"}
