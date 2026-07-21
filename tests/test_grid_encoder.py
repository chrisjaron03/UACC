"""
Tests for the Grid Encoder — verifies grid overlay and marker generation.
"""

from PIL import Image

from uacc.core.accessibility import UIElement
from uacc.core.grid_encoder import (
    _cell_label,
    build_marker_legend,
    grid_cell_to_pixel,
    overlay_grid,
    overlay_markers,
    zoom_region,
)


def _make_test_image(w: int = 1920, h: int = 1080) -> Image.Image:
    """Create a solid test image."""
    return Image.new("RGB", (w, h), color=(30, 30, 30))


def _make_ui_element(
    id: str,
    name: str,
    cx: int,
    cy: int,
    clickable: bool = True,
) -> UIElement:
    return UIElement(
        id=id,
        control_type="Button",
        name=name,
        bounds=(cx - 40, cy - 15, cx + 40, cy + 15),
        center=(cx, cy),
        clickable=clickable,
    )


class TestCellLabel:
    def test_basic_labels(self):
        assert _cell_label(0, 0) == "A1"
        assert _cell_label(1, 0) == "B1"
        assert _cell_label(0, 1) == "A2"
        assert _cell_label(25, 0) == "Z1"

    def test_double_letter(self):
        assert _cell_label(26, 0) == "AA1"
        assert _cell_label(27, 0) == "AB1"


class TestOverlayGrid:
    def test_output_is_rgba(self):
        img = _make_test_image()
        result = overlay_grid(img, mode="coarse")
        assert result.mode == "RGBA"
        assert result.size == (1920, 1080)

    def test_different_modes(self):
        img = _make_test_image(800, 600)
        for mode in ["coarse", "medium", "fine", "micro"]:
            result = overlay_grid(img, mode=mode)
            assert result.size == (800, 600)

    def test_small_image(self):
        img = _make_test_image(320, 240)
        result = overlay_grid(img, mode="coarse")
        assert result.size == (320, 240)


class TestOverlayMarkers:
    def test_markers_drawn(self):
        img = _make_test_image()
        elements = [
            _make_ui_element("e1", "File", 50, 15),
            _make_ui_element("e2", "Edit", 120, 15),
            _make_ui_element("e3", "View", 190, 15),
        ]
        result = overlay_markers(img, elements)
        assert result.mode == "RGBA"
        # Image should be modified (not identical to input)
        assert result.tobytes() != img.convert("RGBA").tobytes()

    def test_max_markers_limit(self):
        img = _make_test_image()
        elements = [
            _make_ui_element(f"e{i}", f"Btn{i}", 50 + i * 20, 15)
            for i in range(100)
        ]
        result = overlay_markers(img, elements, max_markers=10)
        assert result is not None

    def test_non_clickable_excluded(self):
        elements = [
            _make_ui_element("e1", "File", 50, 15, clickable=True),
            _make_ui_element("e2", "Label", 200, 15, clickable=False),
        ]
        legend = build_marker_legend(elements)
        assert "File" in legend
        assert "Label" not in legend


class TestGridCellToPixel:
    def test_center_of_first_cell(self):
        x, y = grid_cell_to_pixel(0, 0, 1920, 1080, mode="coarse")
        # Coarse = 20×12, cell size = 96×90
        assert 40 <= x <= 55  # ~48
        assert 35 <= y <= 50  # ~45

    def test_last_cell(self):
        x, y = grid_cell_to_pixel(19, 11, 1920, 1080, mode="coarse")
        assert x > 1800
        assert y > 950


class TestZoomRegion:
    def test_zoom_output_size(self):
        img = _make_test_image()
        zoomed = zoom_region(img, 960, 540, zoom_level=2, output_size=(800, 600))
        assert zoomed.size == (800, 600)

    def test_zoom_at_edge(self):
        """Zoom at screen edge shouldn't crash."""
        img = _make_test_image()
        zoomed = zoom_region(img, 10, 10, zoom_level=4)
        assert zoomed is not None


class TestMarkerLegend:
    def test_legend_format(self):
        elements = [
            _make_ui_element("e1", "File", 50, 15),
            _make_ui_element("e2", "Edit", 120, 15),
        ]
        legend = build_marker_legend(elements)
        # Badge numbers are stable hashes of element properties — check presence of text not specific IDs
        assert "File" in legend
        assert "Edit" in legend
        assert "50" in legend or "15" in legend
