"""
Agent Controller — the main Observe → Think → Act → Verify loop.

Orchestrates screen capture, text map building, LLM calls, action
execution, and verification into a single coherent agent loop.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Literal, Optional

from PIL import Image

from uacc.actions.executor import ActionExecutor
from uacc.actions.schema import (
    Action,
    DoneAction,
    ScreenshotAction,
    WaitAction,
    action_to_dict,
)
from uacc.agent.memory import SessionMemory
from uacc.agent.verifier import ActionVerifier
from uacc.config import config
from uacc.core.accessibility import (
    UIElement,
    flatten_elements,
    get_interactive_elements,
    get_ui_tree,
)
from uacc.core.grid_encoder import (
    build_marker_legend,
    overlay_markers,
)
from uacc.core.screen_capture import capture_full, get_screen_size, image_to_base64
from uacc.core.text_map import TextMap, build_text_map
from uacc.models.base_adapter import BaseAdapter
from uacc.models.hybrid_adapter import HybridAdapter
from uacc.models.text_adapter import TextAdapter
from uacc.models.vision_adapter import VisionAdapter

logger = logging.getLogger(__name__)


class Agent:
    """The UACC Agent — controls a computer by observing, thinking, and acting.

    Usage:
        agent = Agent(mode="hybrid")
        result = agent.run("Open Notepad and type 'Hello World'")
    """

    def __init__(
        self,
        mode: Optional[Literal["text", "vision", "hybrid"]] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_iterations: Optional[int] = None,
        safe_mode: Optional[bool] = None,
        human_mimicry: Optional[bool] = None,
        verify_actions: bool = True,
    ):
        """Initialize the agent.

        Args:
            mode: Adapter mode — "text" (no vision), "vision", or "hybrid".
            model: LLM model name (e.g. "gpt-4o", "llama3.1:70b").
            api_key: API key for the LLM provider.
            base_url: Custom API base URL (for Ollama, vLLM, etc.).
            max_iterations: Max observe-act cycles per task.
            safe_mode: Block potentially destructive actions.
            human_mimicry: Use Bézier curve mouse movement.
            verify_actions: Enable pre/post action verification.
        """
        self.mode = mode or config.uacc.mode
        self.max_iterations = max_iterations or config.uacc.max_iterations

        # Build the LLM adapter
        adapter_kwargs = {}
        if model:
            adapter_kwargs["model"] = model
        if api_key:
            adapter_kwargs["api_key"] = api_key
        if base_url:
            adapter_kwargs["base_url"] = base_url

        self.adapter: BaseAdapter = self._create_adapter(self.mode, adapter_kwargs)

        # Build executor and verifier
        self.executor = ActionExecutor(
            human_mimicry=human_mimicry,
            safe_mode=safe_mode,
        )
        self.verifier = ActionVerifier(
            verify_pre=verify_actions,
            verify_post=verify_actions,
        )
        self.memory = SessionMemory()

        # State
        self._running = False
        self._current_task = ""
        self._iteration = 0

    def _create_adapter(
        self,
        mode: str,
        kwargs: Dict[str, Any],
    ) -> BaseAdapter:
        """Create the appropriate LLM adapter for the mode."""
        if mode == "text":
            return TextAdapter(**kwargs)
        elif mode == "vision":
            return VisionAdapter(**kwargs)
        elif mode == "hybrid":
            return HybridAdapter(**kwargs)
        else:
            raise ValueError(f"Unknown mode: {mode!r}. Use 'text', 'vision', or 'hybrid'.")

    def run(self, task: str) -> Dict[str, Any]:
        """Run the agent on a task until completion or max iterations.

        Args:
            task: Natural language description of what to do.
                  e.g. "Open Notepad and type 'Hello UACC!'"

        Returns:
            Result dict with: success, message, iterations, summary.
        """
        self._running = True
        self._current_task = task
        self._iteration = 0
        self.memory.reset()
        self.adapter.reset_history()

        logger.info("Agent starting task: %s (mode=%s)", task, self.mode)
        print(f"\n{'='*60}")
        print(f"  UACC Agent — {self.mode.upper()} mode")
        print(f"  Task: {task}")
        print(f"  Max iterations: {self.max_iterations}")
        print(f"{'='*60}\n")

        try:
            while self._running and self._iteration < self.max_iterations:
                self._iteration += 1
                print(f"\n--- Iteration {self._iteration}/{self.max_iterations} ---")

                # ── 1. OBSERVE ──────────────────────────────
                screen_state = self._observe()

                # ── 2. THINK ────────────────────────────────
                actions = self._think(task, screen_state)
                if not actions:
                    logger.warning("No actions returned — retrying")
                    continue

                # ── 3. ACT + VERIFY ─────────────────────────
                for action in actions:
                    if isinstance(action, DoneAction):
                        self._running = False
                        result = self.executor.execute(action)
                        self.memory.record_action(action_to_dict(action), result)
                        print(f"  ✅ Task complete: {action.result}")
                        return {
                            "success": action.success,
                            "message": action.result,
                            "iterations": self._iteration,
                            "summary": self.memory.get_summary(),
                        }

                    result = self._execute_and_verify(action, screen_state)

                    # If it's a screenshot request, loop back to observe
                    if isinstance(action, ScreenshotAction):
                        break

        except KeyboardInterrupt:
            logger.info("Agent interrupted by user")
            print("\n  ⚠️  Agent interrupted by user (Ctrl+C)")
            return {
                "success": False,
                "message": "Interrupted by user",
                "iterations": self._iteration,
                "summary": self.memory.get_summary(),
            }
        except Exception as exc:
            logger.error("Agent error: %s", exc, exc_info=True)
            print(f"\n  ❌ Agent error: {exc}")
            return {
                "success": False,
                "message": f"Error: {exc}",
                "iterations": self._iteration,
                "summary": self.memory.get_summary(),
            }

        # Reached max iterations
        print(f"\n  ⚠️  Max iterations ({self.max_iterations}) reached")
        return {
            "success": False,
            "message": f"Max iterations ({self.max_iterations}) reached without completion",
            "iterations": self._iteration,
            "summary": self.memory.get_summary(),
        }

    def _observe(self) -> Dict[str, Any]:
        """Capture the current screen state.

        Returns a dict with keys depending on mode:
          - "text_map": compact text representation (always)
          - "screenshot_base64": base64 PNG (vision/hybrid modes)
          - "marker_legend": badge number → element mapping (vision/hybrid)
          - "elements_raw": list of element dicts for verifier
        """
        print("  👁️  Observing screen...")

        screen_state: Dict[str, Any] = {}
        screen_w, screen_h = get_screen_size()

        # Capture screenshot
        screenshot = capture_full()
        self.verifier.capture_before()

        # Get accessibility tree
        ui_elements: List[UIElement] = []
        try:
            ui_elements = get_ui_tree()
        except Exception as exc:
            logger.warning("Accessibility tree extraction failed: %s", exc)

        # Build text map (always — needed for text mode and verification)
        active_window = ""
        if ui_elements and ui_elements[0].name:
            active_window = ui_elements[0].name

        text_map: Optional[TextMap] = None
        try:
            text_map = build_text_map(
                screen_width=screen_w,
                screen_height=screen_h,
                ui_elements=ui_elements,
                active_window=active_window,
            )
            screen_state["text_map"] = text_map.to_compact_text()

            # Cache elements in memory
            for el in text_map.all_elements:
                self.memory.cache_element(
                    el.id, el.text, el.element_type, el.center, el.bounds
                )

            # Record this screen
            key_names = [el.text for el in text_map.all_elements[:10] if el.text]
            self.memory.record_screen(active_window, key_names, len(text_map.all_elements))

            # Raw elements for the verifier
            screen_state["elements_raw"] = [el.to_dict() for el in text_map.all_elements]

        except Exception as exc:
            logger.warning("Text map build failed: %s", exc)
            screen_state["text_map"] = f"(Text map unavailable: {exc})"
            screen_state["elements_raw"] = []

        # Vision / hybrid: add screenshot + markers
        if self.mode in ("vision", "hybrid"):
            flat = flatten_elements(ui_elements)
            interactive = [
                el for el in flat if el.clickable or el.editable or el.expandable
            ]

            # Overlay numbered badges
            marked_screenshot = overlay_markers(screenshot, flat)
            screen_state["screenshot_base64"] = image_to_base64(
                marked_screenshot, fmt="PNG", quality=config.uacc.screenshot_quality
            )

            # Build legend
            screen_state["marker_legend"] = build_marker_legend(flat)

        print(f"  📊  Screen: {screen_w}×{screen_h} | Window: \"{active_window}\"")
        if text_map:
            interactive_count = sum(
                1 for el in text_map.all_elements
                if el.clickable or el.editable or el.expandable
            )
            print(f"  🔍  {len(text_map.all_elements)} elements ({interactive_count} interactive)")

        return screen_state

    def _think(
        self,
        task: str,
        screen_state: Dict[str, Any],
    ) -> List[Action]:
        """Ask the LLM what to do next.

        Returns a list of Action objects.
        """
        print("  🧠 Thinking...")

        history = self.memory.get_recent_history(10)
        actions = self.adapter.observe_and_act(task, screen_state, history)

        for action in actions:
            reasoning = getattr(action, "reasoning", "")
            action_name = getattr(action, "action", "?")
            print(f"  💡 {action_name}: {reasoning[:80]}")

        return actions

    def _execute_and_verify(
        self,
        action: Action,
        screen_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute an action with pre/post verification.

        Returns the execution result dict.
        """
        action_name = getattr(action, "action", "?")

        # ── Pre-verify ──────────────────────────────────
        elements_raw = screen_state.get("elements_raw", [])
        pre_result = self.verifier.pre_verify(action, elements_raw)
        if pre_result.corrected_x is not None:
            action = self.verifier.apply_correction(action, pre_result)
            print(f"  🔧 Auto-corrected coordinates → ({pre_result.corrected_x}, {pre_result.corrected_y})")

        # ── Execute ─────────────────────────────────────
        print(f"  ▶️  Executing: {action_name}")
        result = self.executor.execute(action)
        success = result.get("success", False)
        message = result.get("message", "")

        # Record in memory
        self.memory.record_action(action_to_dict(action), result)

        if success:
            print(f"  ✓  {message}")
        else:
            print(f"  ✗  {message}")

        # ── Post-verify ─────────────────────────────────
        if not isinstance(action, (WaitAction, ScreenshotAction)):
            post_result = self.verifier.post_verify(action, expected_change=True)
            if not post_result.passed:
                print(f"  ⚠️  Post-verify: {post_result.message}")

        return result

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent stop requested")
