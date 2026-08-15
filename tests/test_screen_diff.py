"""
Tests for the Screen Diff engine — pixel-level and semantic comparison
between consecutive screenshots.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from uacc.core.screen_diff import (
    ChangedRegion,
    SemanticDiff,
    compute_diff,
    compute_semantic_diff,
    create_diff_visualization,
    has_changed,
)


def _solid_image(
    size: tuple[int, int] = (50, 50),
    color: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """Create a solid-color RGB image."""
    return Image.new("RGB", size, color)


def _patched_image(
    base: tuple[int, int, int] = (0, 0, 0),
    patch: tuple[int, int, int] = (255, 255, 255),
    size: tuple[int, int] = (50, 50),
    patch_box: tuple[int, int, int, int] = (10, 10, 20, 20),
) -> Image.Image:
    """Create an image with a bright rectangular patch on a solid background."""
    img = Image.new("RGB", size, base)
    arr = np.array(img)
    x1, y1, x2, y2 = patch_box
    arr[y1:y2, x1:x2] = patch
    return Image.fromarray(arr)


class TestChangedRegion:
    """Tests for the ChangedRegion dataclass geometry helpers."""

    def test_geometry(self):
        r = ChangedRegion(bounds=(10, 20, 110, 270), pixel_count=100, change_intensity=42.0)
        assert r.width == 100
        assert r.height == 250
        assert r.area == 25000


class TestSemanticDiff:
    """Tests for SemanticDiff aggregation properties."""

    def test_empty_is_not_changed(self):
        assert SemanticDiff().changed is False
        assert SemanticDiff().summary == "No semantic change"

    def test_changed_flags(self):
        assert SemanticDiff(window_title_changed=True).changed is True
        assert SemanticDiff(text_added=["x"]).changed is True
        assert SemanticDiff(text_removed=["y"]).changed is True
        assert SemanticDiff(element_count_changed=True).changed is True
        assert SemanticDiff(changed_elements=["~ button"]).changed is True

    def test_summary_text_added_collapse(self):
        sem = SemanticDiff(text_added=["a", "b", "c", "d"])
        summary = sem.summary
        # More than 3 entries collapses to a count
        assert "+4 text regions" in summary


class TestHasChanged:
    """Tests for the quick has_changed check."""

    def test_identical_images_no_change(self):
        img = _solid_image()
        assert not has_changed(img, img, threshold=0.0)

    def test_clearly_different_images(self):
        before = _solid_image(color=(0, 0, 0))
        after = _solid_image(color=(255, 255, 255))
        assert has_changed(before, after, threshold=0.0)

    def test_subtle_change_below_pixel_threshold(self):
        # Difference of 5 per channel is below the internal >15 noise floor
        before = _solid_image(color=(100, 100, 100))
        after = _solid_image(color=(105, 105, 105))
        assert not has_changed(before, after, threshold=0.0)

    def test_different_sizes_always_changed(self):
        a = _solid_image(size=(50, 50))
        b = _solid_image(size=(51, 51))
        assert has_changed(a, b)

    def test_threshold_respected(self):
        before = _solid_image(color=(0, 0, 0))
        after = _patched_image(patch_box=(0, 0, 5, 5))  # small patch
        # 25 px out of 2500 = 1% — threshold above 1% should report no change
        assert not has_changed(before, after, threshold=5.0)
        # threshold below 1% should report change
        assert has_changed(before, after, threshold=0.5)


class TestComputeSemanticDiff:
    """Tests for the semantic diff between text maps / window titles."""

    def test_no_change(self):
        sem = compute_semantic_diff("same map", "same map", "Notepad", "Notepad")
        assert sem.changed is False
        assert sem.window_title_changed is False

    def test_window_title_renamed(self):
        sem = compute_semantic_diff(None, None, "A.txt", "B.txt")
        assert sem.window_title_changed is True
        assert sem.window_title_before == "A.txt"
        assert sem.window_title_after == "B.txt"

    def test_window_open_title_appears(self):
        # Regression: a window opening ("" -> title) must be detected.
        sem = compute_semantic_diff(None, None, "", "Notepad")
        assert sem.window_title_changed is True

    def test_window_close_title_disappears(self):
        # Regression: a window closing (title -> "") must be detected.
        sem = compute_semantic_diff(None, None, "Notepad", "")
        assert sem.window_title_changed is True

    def test_both_empty_titles_no_change(self):
        sem = compute_semantic_diff(None, None, "", "")
        assert sem.window_title_changed is False

    def test_text_added_and_removed(self):
        before = '[e1] button "Save"\n[e2] button "Open"'
        after = '[e1] button "Save"\n[e2] button "Close"'
        sem = compute_semantic_diff(before, after, "", "")
        assert '[e2] button "Close"' in sem.text_added
        assert '[e2] button "Open"' in sem.text_removed

    def test_noise_lines_filtered(self):
        before = 'Screen: 1\n───\n[e1] button "Save"'
        after = 'Screen: 2\n───\n[e1] button "Save"'
        sem = compute_semantic_diff(before, after, "", "")
        # Only the noisy Screen:/─── lines differ; they should be filtered out
        assert sem.text_added == []
        assert sem.text_removed == []

    def test_element_count_change(self):
        before = '[e1] button "A"'
        after = '[e1] button "A"\n[e2] button "B"'
        sem = compute_semantic_diff(before, after, "", "")
        assert sem.element_count_changed is True
        assert sem.element_count_before == 1
        assert sem.element_count_after == 2

    def test_new_element_type_detected(self):
        # Real text-map format: "[eN] <type> \"name\""
        before = '[e1] button "Save"'
        after = '[e1] button "Save"\n[e2] menu_item "File"'
        sem = compute_semantic_diff(before, after, "", "")
        assert any("menu_item" in e for e in sem.changed_elements)


class TestComputeDiff:
    """Tests for the combined pixel + semantic diff."""

    def test_identical_images_not_changed(self):
        img = _solid_image()
        result = compute_diff(img, img)
        assert result.changed is False
        assert result.changed_percentage == 0.0
        assert result.total_pixels_changed == 0
        assert result.regions == []

    def test_pixel_change_detected_with_regions(self):
        before = _solid_image(color=(0, 0, 0))
        after = _patched_image(patch_box=(5, 5, 25, 25))
        result = compute_diff(before, after, min_region_area=10)
        assert result.changed is True
        assert result.changed_percentage > 0.0
        assert result.total_pixels_changed > 0
        assert len(result.regions) >= 1
        # Region bounds should cover the patch
        r = result.regions[0]
        assert r.bounds[0] <= 5 and r.bounds[2] >= 25

    def test_size_mismatch_treated_as_full_change(self):
        a = _solid_image(size=(40, 40))
        b = _solid_image(size=(60, 60))
        result = compute_diff(a, b)
        assert result.changed is True
        assert result.changed_percentage == 100.0
        assert result.total_pixels_changed == 60 * 60

    def test_semantic_change_without_pixel_change(self):
        img = _solid_image()
        result = compute_diff(
            img,
            img,
            before_text_map="[button] A",
            after_text_map="[button] B",
        )
        # No pixel change, but semantic diff flips `changed` to True
        assert result.changed is True
        assert result.changed_percentage == 0.0
        assert result.semantic is not None
        assert result.semantic.changed is True

    def test_small_region_filtered_by_min_area(self):
        before = _solid_image(color=(0, 0, 0))
        # 3x3 = 9 px patch, below min_region_area=100
        after = _patched_image(patch_box=(10, 10, 13, 13))
        result = compute_diff(before, after, min_region_area=100, pixel_threshold=10)
        assert result.regions == []
        # changed reflects the tiny pixel percentage > 0.5 check
        assert result.total_pixels_changed == 9

    def test_summary_output(self):
        before = _solid_image(color=(0, 0, 0))
        after = _patched_image(patch_box=(5, 5, 25, 25))
        result = compute_diff(before, after, min_region_area=10)
        summary = result.summary
        assert "Changed" in summary


class TestVisualization:
    """Tests for the diff visualization overlay."""

    def test_visualization_returns_rgba(self):
        before = _solid_image(color=(0, 0, 0))
        after = _patched_image(patch_box=(5, 5, 25, 25))
        result = compute_diff(before, after, min_region_area=10)
        vis = create_diff_visualization(before, after, result)
        assert vis.mode == "RGBA"
        assert vis.size == after.size

    def test_visualization_no_regions(self):
        a = _solid_image()
        result = compute_diff(a, a)
        vis = create_diff_visualization(a, a, result)
        assert vis.mode == "RGBA"
