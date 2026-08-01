import os
import json
import pytest
from pathlib import Path
from PIL import Image

from uacc.tools.fetch_image import (
    classify_query_source,
    fetch_reference_image,
    _sanitize_filename,
    _generate_fallback_canvas
)

def test_classify_query_source():
    # URL classification
    assert classify_query_source("http://example.com/spiderman.png") == "url"
    assert classify_query_source("https://example.com/scenery.jpg") == "url"

    # Specific entities (characters, figures, monuments) -> 'web'
    assert classify_query_source("spiderman") == "web"
    assert classify_query_source("Statue of Liberty") == "web"
    assert classify_query_source("Naruto") == "web"
    assert classify_query_source("Eiffel Tower") == "web"

    # Generic art / concepts -> 'pollinations'
    assert classify_query_source("a beautiful sunset over mountains") == "pollinations"
    assert classify_query_source("a simple cozy house in the forest") == "pollinations"
    assert classify_query_source("red flower with green leaves") == "pollinations"


def test_sanitize_filename():
    assert _sanitize_filename("Spider-Man (Marvel)") == "spider-man_marvel"
    assert _sanitize_filename("Statue of Liberty!") == "statue_of_liberty"
    assert _sanitize_filename("") == "reference_image"


def test_generate_fallback_canvas(tmp_path):
    target_path = tmp_path / "test_fallback.png"
    res = _generate_fallback_canvas("spiderman", target_path)
    assert res is True
    assert target_path.exists()

    with Image.open(target_path) as img:
        assert img.size == (512, 512)


def test_fetch_reference_image_fallback(tmp_path):
    out_file = tmp_path / "custom_spiderman.png"
    # Force fallback mode to verify execution flow without external network call dependency
    res = fetch_reference_image("spiderman", output_path=str(out_file), source="fallback")
    
    assert res["success"] is True
    assert res["source"] == "fallback"
    assert res["image_path"] == str(out_file.resolve())
    assert res["width"] == 512
    assert res["height"] == 512
    assert os.path.exists(res["image_path"])


def test_mcp_fetch_image_tool():
    from uacc_mcp.server import fetch_image
    output_json = fetch_image(query="house", source="fallback")
    data = json.loads(output_json)
    
    assert data["success"] is True
    assert "image_path" in data
    assert os.path.exists(data["image_path"])
