import os
from unittest.mock import MagicMock, patch

from uacc.core.window_manager import WindowInfo, launch_application, open_url
from uacc.core.clipboard import read_clipboard, write_clipboard
from uacc.core.element_finder import find_elements_smart, click_element_by_name, wait_for_element
from uacc.core.text_map import ScreenElement


# ── Window Manager Tests ─────────────────────────────────────

def test_window_info_to_dict():
    info = WindowInfo(
        title="Test Window",
        bounds=(10, 20, 110, 120),
        center=(60, 70),
        width=100,
        height=100,
        process_name="test.exe",
        process_id=1234,
        is_visible=True,
        is_focused=True,
        is_maximized=False,
        is_minimized=False,
    )
    d = info.to_dict()
    assert d["title"] == "Test Window"
    assert d["bounds"]["left"] == 10
    assert d["bounds"]["bottom"] == 120
    assert d["center"]["x"] == 60
    assert d["width"] == 100
    assert d["process_name"] == "test.exe"
    assert d["is_focused"] is True


@patch("subprocess.Popen")
def test_launch_application(mock_popen):
    mock_proc = MagicMock()
    mock_proc.pid = 9999
    mock_popen.return_value = mock_proc

    res = launch_application("notepad", wait_ms=0)
    assert res["success"] is True
    assert res["process_id"] == 9999
    assert "Launched 'notepad'" in res["message"]


@patch("subprocess.Popen")
def test_launch_application_chrome_accessibility(mock_popen):
    mock_proc = MagicMock()
    mock_proc.pid = 8888
    mock_popen.return_value = mock_proc

    res = launch_application("chrome", wait_ms=0)
    assert res["success"] is True
    assert res["process_id"] == 8888
    # Assert Popen was called with the accessibility flag
    args = mock_popen.call_args[0][0]
    assert "--force-renderer-accessibility" in args



@patch("uacc.core.window_manager.launch_application")
@patch("webbrowser.open")
def test_open_url(mock_webbrowser_open, mock_launch_app):
    mock_launch_app.return_value = {"success": False, "message": "Failed to launch"}
    res = open_url("google.com")
    assert res["success"] is True
    assert res["url"] == "https://google.com"
    mock_webbrowser_open.assert_called_once_with("https://google.com")


@patch("uacc.core.window_manager.launch_application")
@patch("webbrowser.open")
def test_open_url_launches_chrome(mock_webbrowser_open, mock_launch_app):
    mock_launch_app.return_value = {"success": True, "message": "Launched"}
    res = open_url("google.com")
    assert res["success"] is True
    assert res["url"] == "https://google.com"
    mock_launch_app.assert_called_with("chrome", arguments="https://google.com")
    assert not mock_webbrowser_open.called


def test_is_security_dialog_open_detected():
    import sys
    mock_win32gui = MagicMock()
    mock_win32process = MagicMock()
    mock_psutil = MagicMock()

    # Pre-populate sys.modules with mock modules
    sys.modules["win32gui"] = mock_win32gui
    sys.modules["win32process"] = mock_win32process
    sys.modules["psutil"] = mock_psutil

    try:
        from uacc.core.window_manager import is_security_dialog_open

        # Mock EnumWindows to call the callback with fake HWND
        def mock_enum(cb, param):
            cb(5555, param)
        mock_win32gui.EnumWindows.side_effect = mock_enum
        mock_win32gui.IsWindowVisible.return_value = True
        mock_win32gui.GetWindowText.return_value = "Windows Security"
        mock_win32process.GetWindowThreadProcessId.return_value = (0, 1234)

        mock_proc = MagicMock()
        mock_proc.name.return_value = "credentialuibroker.exe"
        mock_psutil.Process.return_value = mock_proc

        with patch("sys.platform", "win32"):
            msg = is_security_dialog_open()
            assert msg is not None
            assert "credentialuibroker" in msg

    finally:
        # Clean up sys.modules
        sys.modules.pop("win32gui", None)
        sys.modules.pop("win32process", None)
        sys.modules.pop("psutil", None)


# ── Clipboard Tests ──────────────────────────────────────────

def test_clipboard_roundtrip():
    # Test writing and reading using our clipboard module
    test_text = "Hello from UACC test!"
    write_res = write_clipboard(test_text)
    assert write_res["success"] is True

    read_res = read_clipboard()
    assert read_res["success"] is True
    assert read_res["text"] == test_text


# ── Element Finder Tests ──────────────────────────────────────

def test_find_elements_smart():
    elements = [
        ScreenElement(id="e1", element_type="button", text="Save changes", bounds=(0,0,10,10), center=(5,5), clickable=True),
        ScreenElement(id="e2", element_type="text_input", text="Search", bounds=(10,0,20,10), center=(15,5), editable=True),
        ScreenElement(id="e3", element_type="label", text="Static Text", bounds=(20,0,30,10), center=(25,5)),
    ]

    # Fuzzy matches
    matches = find_elements_smart(name="save", min_confidence=0.6, elements=elements)
    assert len(matches) == 1
    assert matches[0].element_id == "e1"
    assert matches[0].confidence >= 0.8

    # Filter by type
    matches = find_elements_smart(element_type="text_input", elements=elements)
    assert len(matches) == 1
    assert matches[0].element_id == "e2"

    # Minimal confidence filter
    matches = find_elements_smart(name="notfound", min_confidence=0.9, elements=elements)
    assert len(matches) == 0


def test_click_element_by_name():
    elements = [
        ScreenElement(id="e1", element_type="button", text="Cancel", bounds=(0,0,10,10), center=(5,5), clickable=True),
    ]

    with patch("uacc.core.element_finder._scan_elements", return_value=elements):
        res = click_element_by_name("cancel")
        assert res["success"] is True
        assert res["click_x"] == 5
        assert res["click_y"] == 5
        assert res["element"]["id"] == "e1"

        # Not found case
        res_fail = click_element_by_name("ok")
        assert res_fail["success"] is False


def test_wait_for_element():
    elements = [
        ScreenElement(id="e1", element_type="button", text="Ready", bounds=(0,0,10,10), center=(5,5), clickable=True),
    ]

    with patch("uacc.core.element_finder._scan_elements", return_value=elements):
        res = wait_for_element("ready", timeout_ms=100, poll_interval_ms=10)
        assert res["success"] is True
        assert res["found"] is True
        assert res["element"]["name"] == "Ready"

        res_fail = wait_for_element("loading", timeout_ms=50, poll_interval_ms=10)
        assert res_fail["success"] is True
        assert res_fail["found"] is False


def test_artistic_painter():
    from uacc.actions.artistic_painter import ArtisticPainter

    mock_executor = MagicMock()
    mock_executor.execute.return_value = {"success": True, "message": "Simulated click/drag"}

    painter = ArtisticPainter(executor=mock_executor)

    # Test presets
    res_rose = painter.draw_preset("rose", (100, 100))
    assert res_rose["success"] is True
    assert res_rose["total_strokes"] > 0
    assert mock_executor.execute.called

    res_mountains = painter.draw_preset("mountains", (200, 200))
    assert res_mountains["success"] is True
    assert res_mountains["total_strokes"] > 0

    res_galaxy = painter.draw_preset("galaxy", (150, 150))
    assert res_galaxy["success"] is True
    assert res_galaxy["total_strokes"] > 0

    res_peacock = painter.draw_preset("peacock", (300, 300))
    assert res_peacock["success"] is True
    assert res_peacock["total_strokes"] > 0

    # Test painting from image file
    from PIL import Image
    import tempfile
    
    # Create a temporary simple image with white background and black square
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img = Image.new("RGB", (100, 100), "white")
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        draw.rectangle([20, 20, 80, 80], fill=None, outline="black")
        img.save(tmp.name)
        tmp_name = tmp.name

    try:
        res_image = painter.draw_image(tmp_name, (0, 0, 500, 500), max_strokes=50)
        assert res_image["success"] is True
        assert res_image["total_strokes"] > 0
    finally:
        os.remove(tmp_name)


import pytest


@pytest.mark.network
def test_specialists():
    from uacc.agent.specialists import JobFinder, LongFormResearcher

    finder = JobFinder()
    res_jobs = finder.run_search("Developer", "Boston", remote=True)
    assert res_jobs["success"] is True
    assert res_jobs["jobs_count"] > 0
    assert "Job Match" in res_jobs["report"]

    researcher = LongFormResearcher()
    res_res = researcher.run_research("Quantum Computing", depth_levels=2)
    assert res_res["success"] is True
    assert "Deep Research" in res_res["report"]


def test_resolve_chrome_profile():
    from uacc.core.window_manager import resolve_chrome_profile
    import tempfile
    import json
    import sys
    import builtins
    
    # Create mock Local State content
    mock_local_state = {
        "profile": {
            "info_cache": {
                "Default": {
                    "name": "Chris"
                },
                "Profile 2": {
                    "name": "Chess"
                },
                "Profile 3": {
                    "name": "Chris"
                }
            }
        }
    }
    
    real_open = builtins.open
    with tempfile.NamedTemporaryFile(mode="w", suffix="Local State", delete=False) as tmp:
        json.dump(mock_local_state, tmp)
        tmp_name = tmp.name
        
    try:
        # Patch paths to point to our mock file
        with patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.open", side_effect=lambda *args, **kwargs: real_open(tmp_name, "r", encoding="utf-8")):
            folder = resolve_chrome_profile("chrome", "chess")
            assert folder == "Profile 2"
            
            folder_chris = resolve_chrome_profile("chrome", "chris")
            assert folder_chris in ("Default", "Profile 3")
    finally:
        import os
        os.remove(tmp_name)


@patch("uacc.core.window_manager.resolve_chrome_profile")
@patch("subprocess.Popen")
def test_launch_application_profile_directory(mock_popen, mock_resolve):
    mock_resolve.return_value = "Profile2"
    mock_proc = MagicMock()
    mock_proc.pid = 7777
    mock_popen.return_value = mock_proc
    
    # Test mapping of --profile-name
    res = launch_application("chrome", arguments='--profile-name="Chess" https://google.com', wait_ms=0)
    assert res["success"] is True
    args = mock_popen.call_args[0][0]
    
    # Verify `--profile-directory="Profile2"` is in args
    assert any('--profile-directory="Profile2"' in arg for arg in args)
    assert "--force-renderer-accessibility" in args
    
    # Test fallback when profile cannot be resolved
    mock_resolve.return_value = None
    res = launch_application("chrome", arguments='--profile-name="Unknown" https://google.com', wait_ms=0)
    assert res["success"] is True
    args2 = mock_popen.call_args[0][0]
    assert not any('--profile-directory' in arg for arg in args2)
    assert not any('--profile-name' in arg for arg in args2)


@patch("uacc.core.window_manager.launch_application")
def test_open_url_with_profile(mock_launch_app):
    mock_launch_app.return_value = {"success": True, "message": "Launched"}
    res = open_url("google.com", profile_name="Chris")
    assert res["success"] is True
    mock_launch_app.assert_called_with("chrome", arguments='--profile-name="Chris" https://google.com')
