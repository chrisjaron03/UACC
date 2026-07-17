"""
Base Model Adapter — abstract interface that all LLM adapters implement.

Defines the observe → think → act contract and shared prompt engineering.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from uacc.actions.schema import Action, parse_action, parse_actions

logger = logging.getLogger(__name__)


# ── System Prompt ────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are UACC Agent — a computer-controlling AI assistant. You interact with a \
real computer by observing the screen state and issuing precise UI actions.

## Your Capabilities
You can perform these actions by responding with JSON:

1. **click** — Click at exact coordinates
   `{"action": "click", "x": 960, "y": 540, "button": "left", "count": 1, "reasoning": "..."}`

2. **drag** — Click-hold-move-release
   `{"action": "drag", "start_x": 100, "start_y": 200, "end_x": 500, "end_y": 200, "duration_ms": 500, "reasoning": "..."}`

3. **type** — Type text via keyboard
   `{"action": "type", "text": "Hello World", "reasoning": "..."}`

4. **hotkey** — Press key combination
   `{"action": "hotkey", "keys": ["ctrl", "s"], "reasoning": "..."}`

5. **scroll** — Scroll at a position
   `{"action": "scroll", "x": 960, "y": 540, "direction": "down", "amount": 3, "reasoning": "..."}`

6. **hover** — Move mouse and wait (trigger tooltips/menus)
   `{"action": "hover", "x": 200, "y": 100, "duration_ms": 500, "reasoning": "..."}`

7. **wait** — Wait for UI to update
   `{"action": "wait", "duration_ms": 1000, "reasoning": "..."}`

8. **screenshot** — Request a fresh screenshot to verify current state
   `{"action": "screenshot", "reasoning": "..."}`

9. **done** — Signal task completion
   `{"action": "done", "result": "description of what was accomplished", "success": true}`

## Rules
- ALWAYS respond with valid JSON. Your response must be either a single action object or an array of action objects.
- ALWAYS include a "reasoning" field explaining WHY you're performing each action.
- Use EXACT coordinates from the screen information provided.
- If you're unsure about coordinates, request a screenshot first.
- Prefer keyboard shortcuts (hotkey) for common operations — they're faster and more reliable.
- After performing actions that change the screen, request a screenshot to verify the result.
- When the task is fully complete, respond with a "done" action.

## Response Format
Single action:
```json
{"action": "click", "x": 100, "y": 200, "reasoning": "Clicking the File menu"}
```

Multiple sequential actions:
```json
[
  {"action": "click", "x": 100, "y": 200, "reasoning": "Click the File menu"},
  {"action": "click", "x": 120, "y": 250, "reasoning": "Click 'New File' option"}
]
```
"""


class BaseAdapter(ABC):
    """Abstract base for all LLM adapters.

    Subclasses must implement `_build_messages()` and `_call_llm()`.
    """

    def __init__(
        self,
        model: str = "",
        api_key: str = "",
        base_url: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._conversation_history: List[Dict[str, Any]] = []

    @abstractmethod
    def _build_messages(
        self,
        task: str,
        screen_state: Dict[str, Any],
        action_history: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Build the messages array to send to the LLM.

        Args:
            task: The user's task description.
            screen_state: Current screen information (text map, screenshot, etc.).
            action_history: List of previous actions and their results.

        Returns:
            List of message dicts in OpenAI format.
        """
        ...

    @abstractmethod
    def _call_llm(self, messages: List[Dict[str, Any]]) -> str:
        """Call the LLM API and return the raw response text."""
        ...

    def observe_and_act(
        self,
        task: str,
        screen_state: Dict[str, Any],
        action_history: List[Dict[str, Any]],
    ) -> List[Action]:
        """Main entry point: observe screen state → decide actions.

        Args:
            task: What the user wants done.
            screen_state: Current screen representation.
            action_history: Previous actions and results.

        Returns:
            List of Action objects to execute.
        """
        messages = self._build_messages(task, screen_state, action_history)
        raw_response = self._call_llm(messages)
        actions = self._parse_response(raw_response)

        logger.info("Model returned %d action(s)", len(actions))
        return actions

    def _parse_response(self, raw: str) -> List[Action]:
        """Parse the LLM's raw text response into typed Action objects."""
        # Try to extract JSON from the response
        json_str = self._extract_json(raw)
        if json_str is None:
            logger.warning("No JSON found in response: %s", raw[:200])
            return []

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.error("JSON parse error: %s\nRaw: %s", exc, json_str[:500])
            return []

        # Single action or list of actions
        if isinstance(data, dict):
            return [parse_action(data)]
        elif isinstance(data, list):
            return parse_actions(data)
        else:
            logger.error("Unexpected JSON type: %s", type(data))
            return []

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        """Extract JSON from LLM response (handles markdown code blocks, etc.)."""
        # Try extracting from ```json ... ``` blocks
        pattern = r"```(?:json)?\s*\n?([\s\S]*?)\n?\s*```"
        matches = re.findall(pattern, text)
        if matches:
            return matches[0].strip()

        # Try finding raw JSON (object or array)
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start_idx = text.find(start_char)
            if start_idx == -1:
                continue
            # Find matching closing bracket
            depth = 0
            for i in range(start_idx, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        return text[start_idx : i + 1]

        return None

    def reset_history(self) -> None:
        """Clear conversation history for a new task."""
        self._conversation_history.clear()
