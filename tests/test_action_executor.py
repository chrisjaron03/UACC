"""
Tests for the Action schema and executor logic (unit tests only — no real mouse/keyboard).
"""


from uacc.actions.schema import (
    ClickAction,
    DoneAction,
    DragAction,
    HotkeyAction,
    HoverAction,
    MouseButton,
    ScrollAction,
    ScrollDirection,
    TypeAction,
    WaitAction,
    action_to_dict,
    is_potentially_destructive,
    parse_action,
    parse_actions,
)


class TestParseAction:
    """Test JSON → Action parsing."""

    def test_click(self):
        data = {"action": "click", "x": 100, "y": 200, "button": "left", "count": 2}
        action = parse_action(data)
        assert isinstance(action, ClickAction)
        assert action.x == 100
        assert action.y == 200
        assert action.button == MouseButton.LEFT
        assert action.count == 2

    def test_drag(self):
        data = {
            "action": "drag",
            "start_x": 10,
            "start_y": 20,
            "end_x": 300,
            "end_y": 400,
            "duration_ms": 600,
        }
        action = parse_action(data)
        assert isinstance(action, DragAction)
        assert action.start_x == 10
        assert action.end_x == 300
        assert action.duration_ms == 600

    def test_type(self):
        data = {"action": "type", "text": "Hello!", "reasoning": "Typing greeting"}
        action = parse_action(data)
        assert isinstance(action, TypeAction)
        assert action.text == "Hello!"
        assert action.reasoning == "Typing greeting"

    def test_hotkey(self):
        data = {"action": "hotkey", "keys": ["ctrl", "s"]}
        action = parse_action(data)
        assert isinstance(action, HotkeyAction)
        assert action.keys == ["ctrl", "s"]

    def test_scroll(self):
        data = {"action": "scroll", "x": 500, "y": 500, "direction": "down", "amount": 5}
        action = parse_action(data)
        assert isinstance(action, ScrollAction)
        assert action.direction == ScrollDirection.DOWN
        assert action.amount == 5

    def test_hover(self):
        data = {"action": "hover", "x": 200, "y": 300, "duration_ms": 1000}
        action = parse_action(data)
        assert isinstance(action, HoverAction)
        assert action.duration_ms == 1000

    def test_wait(self):
        data = {"action": "wait", "duration_ms": 2000, "condition": "dialog appears"}
        action = parse_action(data)
        assert isinstance(action, WaitAction)
        assert action.condition == "dialog appears"

    def test_done(self):
        data = {"action": "done", "result": "Task completed successfully", "success": True}
        action = parse_action(data)
        assert isinstance(action, DoneAction)
        assert action.success is True
        assert action.result == "Task completed successfully"

    def test_unknown_action_raises(self):
        try:
            parse_action({"action": "fly"})
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Unknown action" in str(e)

    def test_extra_fields_ignored(self):
        """Unknown fields should be silently ignored."""
        data = {"action": "click", "x": 1, "y": 2, "unknown_field": "ignored"}
        action = parse_action(data)
        assert isinstance(action, ClickAction)
        assert action.x == 1


class TestParseActions:
    def test_list(self):
        data = [
            {"action": "click", "x": 10, "y": 20},
            {"action": "type", "text": "hi"},
            {"action": "hotkey", "keys": ["ctrl", "s"]},
        ]
        actions = parse_actions(data)
        assert len(actions) == 3
        assert isinstance(actions[0], ClickAction)
        assert isinstance(actions[1], TypeAction)
        assert isinstance(actions[2], HotkeyAction)


class TestActionToDict:
    def test_roundtrip(self):
        original = ClickAction(x=100, y=200, button=MouseButton.RIGHT, count=1)
        d = action_to_dict(original)
        assert d["action"] == "click"
        assert d["x"] == 100
        assert d["button"] == "right"

        # Parse back
        reparsed = parse_action(d)
        assert isinstance(reparsed, ClickAction)
        assert reparsed.x == 100
        assert reparsed.button == MouseButton.RIGHT


class TestSafety:
    def test_destructive_detected(self):
        action = TypeAction(text="rm -rf /", reasoning="Delete everything")
        assert is_potentially_destructive(action) is True

    def test_safe_action(self):
        action = TypeAction(text="Hello World", reasoning="Typing a greeting")
        assert is_potentially_destructive(action) is False

    def test_destructive_in_reasoning(self):
        action = ClickAction(x=100, y=200, reasoning="Click the delete button")
        assert is_potentially_destructive(action) is True

    def test_done_not_destructive(self):
        action = DoneAction(result="All good")
        assert is_potentially_destructive(action) is False
