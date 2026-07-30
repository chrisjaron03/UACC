import pytest
from unittest.mock import MagicMock, patch

from uacc.safety.mouse_sentinel import MouseSentinel
from uacc.actions.executor import ActionExecutor
from uacc.actions.schema import ClickAction, DragAction, MouseButton
from uacc.actions.artistic_painter import ArtisticPainter
from uacc.config import config


def test_mouse_sentinel_default_kill_distance():
    sentinel = MouseSentinel()
    assert sentinel.get_kill_distance() == 40
    assert sentinel.check_killed() is False


def test_mouse_sentinel_detects_drift():
    sentinel = MouseSentinel(kill_distance_px=40)
    sentinel.start()
    try:
        sentinel.set_expected_position(100, 100)
        # Mock cursor position to be 100px away
        with patch.object(sentinel, "_get_cursor", return_value=(200, 100)):
            # Force monitor loop iteration or check logic
            sentinel.set_expected_position(100, 100)
            import time
            time.sleep(0.15)
            assert sentinel.check_killed() is True
    finally:
        sentinel.stop()


def test_action_executor_blocks_on_sentinel_kill():
    import time
    sentinel = MouseSentinel()
    sentinel._killed = True  # simulate kill flag raised
    sentinel._last_uacc_call = time.time()

    executor = ActionExecutor(safe_mode=False, sentinel=sentinel)
    action = ClickAction(x=100, y=100)
    res = executor.execute(action)

    assert res["success"] is False
    assert res["killed"] is True
    assert "User override detected" in res["message"]


def test_artistic_painter_halts_on_mouse_drag_drift():
    import time
    mock_executor = MagicMock()
    mock_executor.execute.return_value = {"success": True, "message": "Click ok"}

    sentinel = MouseSentinel(kill_distance_px=40)
    sentinel._killed = True
    sentinel._last_uacc_call = time.time()

    painter = ArtisticPainter(executor=mock_executor, sentinel=sentinel)

    strokes = [
        DragAction(start_x=100, start_y=100, end_x=110, end_y=110, duration_ms=10),
    ]

    with patch("pyautogui.moveTo"), \
         patch("pyautogui.mouseDown"), \
         patch("pyautogui.mouseUp"):

        res = painter._execute_strokes(strokes)
        assert res["success"] is False
        assert res["killed"] is True
        assert "user override" in res["message"].lower()
