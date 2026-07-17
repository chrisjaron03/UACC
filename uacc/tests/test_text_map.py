"""
Tests for the Text Map builder — verifies structured screen representation.
"""

from uacc.core.accessibility import UIElement
from uacc.core.ocr_engine import OCRResult
from uacc.core.text_map import (
    ScreenElement,
    TextMap,
    build_text_map,
)


def _make_ui_element(
    id: str,
    name: str,
    control_type: str = "Button",
    bounds: tuple = (0, 0, 100, 30),
    clickable: bool = True,
) -> UIElement:
    """Helper to create test UIElements."""
    center = ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)
    return UIElement(
        id=id,
        control_type=control_type,
        name=name,
        bounds=bounds,
        center=center,
        clickable=clickable,
    )


class TestTextMapBuild:
    """Test the text map builder."""

    def test_build_empty(self):
        """Empty element list produces a valid TextMap."""
        tm = build_text_map(
            screen_width=1920,
            screen_height=1080,
            ui_elements=[],
            active_window="Test",
        )
        assert isinstance(tm, TextMap)
        assert tm.screen_width == 1920
        assert tm.screen_height == 1080
        assert tm.active_window == "Test"
        assert len(tm.all_elements) == 0

    def test_build_with_elements(self):
        """Elements are correctly converted to ScreenElements."""
        elements = [
            _make_ui_element("e1", "File", bounds=(0, 0, 50, 30)),
            _make_ui_element("e2", "Edit", bounds=(50, 0, 100, 30)),
            _make_ui_element(
                "e3", "Search", control_type="Edit",
                bounds=(200, 0, 600, 30), clickable=False,
            ),
        ]
        # Set editable on e3
        elements[2].editable = True

        tm = build_text_map(
            screen_width=1920,
            screen_height=1080,
            ui_elements=elements,
            active_window="VS Code",
        )

        assert len(tm.all_elements) >= 3
        assert tm.active_window == "VS Code"

    def test_compact_text_format(self):
        """Compact text output is readable and contains key info."""
        elements = [
            _make_ui_element("e1", "File", bounds=(0, 0, 50, 30)),
            _make_ui_element("e2", "Save", bounds=(100, 800, 200, 830)),
        ]

        tm = build_text_map(
            screen_width=1920,
            screen_height=1080,
            ui_elements=elements,
            active_window="Test App",
        )

        text = tm.to_compact_text()
        assert "1920x1080" in text
        assert "Test App" in text
        assert "Interactive Elements" in text

    def test_yaml_format(self):
        """YAML output is parseable."""
        import yaml

        elements = [
            _make_ui_element("e1", "Button1", bounds=(100, 100, 200, 130)),
        ]
        tm = build_text_map(
            screen_width=1920,
            screen_height=1080,
            ui_elements=elements,
        )

        yaml_str = tm.to_yaml()
        parsed = yaml.safe_load(yaml_str)
        assert parsed["screen"]["resolution"] == "1920x1080"

    def test_ocr_merge_no_overlap(self):
        """OCR results that don't overlap with accessibility elements are added."""
        ui_elements = [
            _make_ui_element("e1", "File", bounds=(0, 0, 50, 30)),
        ]
        ocr_results = [
            OCRResult(
                text="Some OCR Text",
                bounds=(500, 500, 700, 530),
                center=(600, 515),
                confidence=0.9,
            ),
        ]

        tm = build_text_map(
            screen_width=1920,
            screen_height=1080,
            ui_elements=ui_elements,
            ocr_results=ocr_results,
        )

        # Should have the UI element + the OCR text
        texts = [el.text for el in tm.all_elements]
        assert "File" in texts
        assert "Some OCR Text" in texts

    def test_ocr_merge_overlapping(self):
        """OCR results that overlap with accessibility elements are NOT duplicated."""
        ui_elements = [
            _make_ui_element("e1", "File", bounds=(0, 0, 50, 30)),
        ]
        ocr_results = [
            OCRResult(
                text="File",
                bounds=(5, 5, 45, 25),  # Overlaps with e1
                center=(25, 15),
                confidence=0.95,
            ),
        ]

        tm = build_text_map(
            screen_width=1920,
            screen_height=1080,
            ui_elements=ui_elements,
            ocr_results=ocr_results,
        )

        # "File" should appear only once
        file_count = sum(1 for el in tm.all_elements if el.text == "File")
        assert file_count == 1

    def test_region_grouping(self):
        """Elements are grouped into spatial regions."""
        elements = [
            _make_ui_element("e1", "Menu", bounds=(10, 10, 100, 30)),  # top
            _make_ui_element("e2", "Sidebar", bounds=(10, 200, 200, 600)),  # left
            _make_ui_element("e3", "Content", bounds=(500, 400, 900, 700)),  # center
            _make_ui_element("e4", "Status", bounds=(10, 1060, 500, 1080)),  # bottom
        ]

        tm = build_text_map(
            screen_width=1920,
            screen_height=1080,
            ui_elements=elements,
        )

        region_names = [r.name for r in tm.regions]
        # Should have at least top_bar and some content regions
        assert len(tm.regions) >= 1


class TestScreenElement:
    """Test ScreenElement serialization."""

    def test_to_dict(self):
        se = ScreenElement(
            id="e1",
            element_type="button",
            text="Submit",
            bounds=(100, 200, 300, 230),
            center=(200, 215),
            clickable=True,
        )
        d = se.to_dict()
        assert d["id"] == "e1"
        assert d["type"] == "button"
        assert d["text"] == "Submit"
        assert d["bounds"] == [100, 200, 300, 230]
        assert d["center"] == [200, 215]
        assert d["clickable"] is True
        assert "editable" not in d  # False values omitted

    def test_optional_fields_omitted(self):
        se = ScreenElement(
            id="e2",
            element_type="label",
            text="Hello",
            bounds=(0, 0, 50, 20),
            center=(25, 10),
        )
        d = se.to_dict()
        assert "clickable" not in d
        assert "expandable" not in d
        assert "value" not in d
