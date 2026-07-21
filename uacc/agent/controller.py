"""
Agent Controller — the main Observe → Think → Act → Verify loop.

Orchestrates screen capture, text map building, LLM calls, action
execution, and verification into a single coherent agent loop.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional


from uacc.actions.executor import ActionExecutor
from uacc.actions.schema import (
    Action,
    DoneAction,
    ScreenshotAction,
    WaitAction,
    action_to_dict,
)
from uacc.agent.memory import SessionMemory
from uacc.agent.planner import Planner, SubtaskStatus
from uacc.agent.recovery import RecoveryEngine, RecoveryTier
from uacc.agent.reflector import ReflectionEngine
from uacc.agent.shortcuts import ShortcutDetector
from uacc.agent.verifier import ActionVerifier
from uacc.memory.semantic_graph import SemanticGraph
from uacc.safety.classifier import RiskClassifier, RiskLevel
from uacc.safety.gate import SafetyDecision, SafetyGate, SafetyPolicy
from uacc.config import config
from uacc.core.accessibility import (
    UIElement,
    flatten_elements,
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
from uacc.models.anthropic_adapter import AnthropicAdapter

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
        provider: Optional[str] = None,
        max_iterations: Optional[int] = None,
        safe_mode: Optional[bool] = None,
        human_mimicry: Optional[bool] = None,
        verify_actions: bool = True,
    ):
        """Initialize the agent.

        Args:
            mode: Adapter mode — "text" (no vision), "vision", or "hybrid".
            model: LLM model name (e.g. "gpt-4o", "claude-sonnet-4").
            api_key: API key for the LLM provider.
            base_url: Custom API base URL (for Ollama, vLLM, etc.).
            provider: LLM provider — "openai", "anthropic", or "auto".
            max_iterations: Max observe-act cycles per task.
            safe_mode: Block potentially destructive actions.
            human_mimicry: Use Bézier curve mouse movement.
            verify_actions: Enable pre/post action verification.
        """
        self.mode = mode or config.uacc.mode
        self.max_iterations = max_iterations or config.uacc.max_iterations
        self.provider = provider or "openai"

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
        self.shortcut_detector = ShortcutDetector(memory=self.memory)
        self.semantic_graph = SemanticGraph()
        self.recovery = RecoveryEngine()
        self.planner = Planner()
        self.reflector = ReflectionEngine(memory=self.memory)
        self.risk_classifier = RiskClassifier()
        self.safety_gate = SafetyGate(
            policy=SafetyPolicy(config.uacc.safety_policy)
        )

        # State
        self._running = False
        self._current_task = ""
        self._iteration = 0

    def _create_adapter(
        self,
        mode: str,
        kwargs: Dict[str, Any],
    ) -> BaseAdapter:
        """Create the appropriate LLM adapter for the mode and provider."""
        provider = self.provider or "openai"

        if provider == "anthropic":
            return AnthropicAdapter(**kwargs)

        if mode == "text":
            return TextAdapter(**kwargs)
        elif mode == "vision":
            return VisionAdapter(**kwargs)
        elif mode == "hybrid":
            return HybridAdapter(**kwargs)
        else:
            raise ValueError(f"Unknown mode: {mode!r}. Use 'text', 'vision', or 'hybrid'.")

    def run(self, task: str) -> Dict[str, Any]:
        """Run the agent on a task using a hybrid Plan-and-Execute + ReAct loop.

        Outer loop: Planner decomposes the task into subtask DAG.
        Inner loop: ReAct executes each subtask with observation, thought, action, verification.

        Args:
            task: Natural language description of what to do.

        Returns:
            Result dict with: success, message, iterations, plan.
        """
        import time as _time
        _start_time = _time.time()
        self._running = True
        self._current_task = task
        self._iteration = 0
        self.memory.reset()
        self.memory.set_goal(task)
        self.adapter.reset_history()

        logger.info("Agent run: task=%s mode=%s max_iter=%d", task, self.mode, self.max_iterations)

        # Load relevant past reflections for context
        past_reflections = self.memory.get_reflections(task_goal=task, max_results=3)
        if past_reflections:
            print(f"  📚 Loaded {len(past_reflections)} past reflections relevant to this goal")
            logger.info("Loaded %d past reflections for goal: %s", len(past_reflections), task)
            for ref in past_reflections:
                print(f"     ⚠️  Past: {ref.failed_action} — {ref.root_cause[:60]}")

        # Phase 1: Create plan
        plan = self.planner.create_plan(task)
        logger.info("Plan created: %d subtasks for: %s", len(plan.subtasks), task)
        print(f"\n{'='*60}")
        print(f"  UACC Agent — {self.mode.upper()} mode")
        print(f"  Task: {task}")
        print(f"  Plan: {len(plan.subtasks)} subtasks")
        for st in plan.subtasks:
            deps = f" (after: {', '.join(st.dependencies)})" if st.dependencies else ""
            print(f"    └─ {st.id}: {st.description}{deps}")
        print(f"  Max iterations: {self.max_iterations}")
        print(f"{'='*60}\n")

        try:
            # Phase 2: Execute subtasks
            while self._running and self._iteration < self.max_iterations and not plan.completed and not plan.failed:
                subtask = self.planner.get_next()
                if subtask is None:
                    logger.warning("No ready subtask — breaking")
                    break

                self.planner.mark_running(subtask.id)
                print(f"\n{'─'*50}")
                print(f"  📋 Subtask: {subtask.description}")
                print(f"  {plan.progress_str()}")
                print(f"{'─'*50}\n")

                # Inner ReAct loop for this subtask
                subtask_start = _time.time()
                subtask_actions_executed = 0
                subtask_done = False
                subtask_error = ""

                while (
                    self._running
                    and self._iteration < self.max_iterations
                    and not subtask_done
                ):
                    self._iteration += 1
                    print(f"\n--- Iteration {self._iteration}/{self.max_iterations} [{subtask.id}] ---")
                    iteration_start = _time.time()

                    # OBSERVE
                    screen_state = self._observe()

                    # THINK (with subtask context)
                    subtask_context = (
                        f"## Overall Goal\n{task}\n\n"
                        f"## Current Sub-task\n{subtask.description}\n\n"
                        f"## Progress\n{plan.progress_str()}"
                    )
                    actions = self._think(subtask_context, screen_state)
                    if not actions:
                        logger.warning("No actions returned — retrying")
                        continue

                    # ACT + VERIFY
                    for action in actions:
                        if isinstance(action, DoneAction):
                            subtask_done = True
                            result = self.executor.execute(action)
                            self.memory.record_action(action_to_dict(action), result)
                            self.planner.mark_success(subtask.id, action.result)
                            print(f"  ✅ Subtask complete: {action.result}")
                            break

                        iter_duration = _time.time() - iteration_start
                        logger.debug(
                            "Iteration %d [%s]: action=%s duration=%.2fs",
                            self._iteration, subtask.id,
                            getattr(action, "action", "?"), iter_duration,
                        )
                        result = self._execute_and_verify(action, screen_state)
                        subtask_actions_executed += 1

                        if isinstance(action, ScreenshotAction):
                            break

                    # Check if we've exceeded the outer iteration limit
                    if subtask_actions_executed > self.max_iterations // max(1, len(plan.subtasks)):
                        subtask_done = True
                        subtask_error = f"Subtask exceeded its iteration budget"
                        self.planner.mark_failed(subtask.id, subtask_error)

                if subtask_error and not self._running:
                    break

            total_duration = _time.time() - _start_time
            elapsed = f"{total_duration:.1f}s"

            # All subtasks processed — determine overall result
            if plan.completed:
                print(f"\n{'='*60}")
                print(f"  ✅ All subtasks complete!")
                print(f"{'='*60}")
                logger.info("Task completed: iterations=%d duration=%s", self._iteration, elapsed)
                self.semantic_graph.save()
                return {
                    "success": True,
                    "message": f"Task completed in {self._iteration} iterations ({elapsed})",
                    "iterations": self._iteration,
                    "plan": self.planner.get_status(),
                    "summary": self.memory.get_summary(),
                }
            elif plan.failed:
                failed_st = [s for s in plan.subtasks if s.status == SubtaskStatus.FAILED]
                errors = "; ".join(f"{s.id}: {s.error}" for s in failed_st)
                logger.warning("Task failed: iterations=%d duration=%s errors=%s", self._iteration, elapsed, errors)
                self.semantic_graph.save()
                return {
                    "success": False,
                    "message": f"Task failed — {errors}",
                    "iterations": self._iteration,
                    "plan": self.planner.get_status(),
                    "summary": self.memory.get_summary(),
                }

        except KeyboardInterrupt:
            total_duration = _time.time() - _start_time
            logger.info("Agent interrupted by user after %.1fs", total_duration)
            print("\n  ⚠️  Agent interrupted by user (Ctrl+C)")
            self.semantic_graph.save()
            return {
                "success": False,
                "message": "Interrupted by user",
                "iterations": self._iteration,
                "plan": self.planner.get_status(),
                "summary": self.memory.get_summary(),
            }
        except Exception as exc:
            total_duration = _time.time() - _start_time
            logger.error("Agent error after %.1fs: %s", total_duration, exc, exc_info=True)
            print(f"\n  ❌ Agent error: {exc}")
            self.semantic_graph.save()
            return {
                "success": False,
                "message": f"Error: {exc}",
                "iterations": self._iteration,
                "plan": self.planner.get_status(),
                "summary": self.memory.get_summary(),
            }

        # Reached max iterations
        total_duration = _time.time() - _start_time
        logger.warning("Max iterations (%d) reached after %.1fs", self.max_iterations, total_duration)
        self.semantic_graph.save()
        print(f"\n  ⚠️  Max iterations ({self.max_iterations}) reached ({total_duration:.1f}s)")
        return {
            "success": False,
            "message": f"Max iterations ({self.max_iterations}) reached without completion",
            "iterations": self._iteration,
            "plan": self.planner.get_status(),
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
        import time as _observe_time
        _obs_start = _observe_time.time()

        screen_state: Dict[str, Any] = {}
        screen_w, screen_h = get_screen_size()

        # Capture screenshot (diff reference stored after text map is built)
        screenshot = capture_full()

        # Get accessibility tree
        ui_elements: List[UIElement] = []
        try:
            ui_elements = get_ui_tree()
        except Exception as exc:
            logger.warning("Accessibility tree extraction failed: %s", exc)

        # Run OCR to find text not captured by accessibility tree
        ocr_results = None
        try:
            from uacc.core.ocr_engine import extract_text
            ocr_results = extract_text(screenshot, confidence_threshold=0.4)
        except Exception as exc:
            logger.debug("OCR extraction skipped: %s", exc)

        # Build text map (always — needed for text mode and verification)
        active_window = ""
        if ui_elements and ui_elements[0].name:
            active_window = ui_elements[0].name
        elif not ui_elements:
            try:
                from uacc.core.window_manager import get_active_window
                active_window = get_active_window()
            except Exception:
                pass

        text_map: Optional[TextMap] = None
        try:
            text_map = build_text_map(
                screen_width=screen_w,
                screen_height=screen_h,
                ui_elements=ui_elements,
                ocr_results=ocr_results,
                active_window=active_window,
            )
            compact = text_map.to_compact_text()
            screen_state["text_map"] = compact

            # Inject semantic memory context for the current app
            if active_window:
                app_patterns = self.semantic_graph.get_app_patterns(active_window)
                if app_patterns and app_patterns.get("patterns"):
                    pattern_lines = []
                    for action_type, elements in app_patterns["patterns"].items():
                        if elements:
                            pattern_lines.append(f"  {action_type}: {', '.join(elements[:5])}")
                    if pattern_lines:
                        compact += (
                            "\n\n─── Past Knowledge ───\n"
                            + f"  App: {app_patterns['name']}\n"
                            + "\n".join(pattern_lines)
                        )

            # Discover shortcuts from the text map
            discovered = self.shortcut_detector.discover_from_text_map(compact)
            if discovered:
                shortcuts_hint = self.shortcut_detector.get_relevant_shortcuts(
                    task_goal=self._current_task
                )
                if shortcuts_hint:
                    screen_state["text_map"] += (
                        "\n\n─── Known Shortcuts ───\n"
                        + "\n".join(f"  {s}" for s in shortcuts_hint)
                    )

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

            # Store diff reference with semantic context
            self.verifier.capture_before(
                text_map=screen_state.get("text_map"),
                window_title=active_window,
            )

        except Exception as exc:
            logger.warning("Text map build failed: %s", exc)
            # Fallback: try vision-based detection
            try:
                from uacc.core.vision_detector import full_vision_detect
                vision_elements = full_vision_detect(screenshot)
                if vision_elements:
                    from uacc.core.text_map import TextMap
                    from datetime import datetime, timezone
                    vm = TextMap(
                        screen_width=screen_w,
                        screen_height=screen_h,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                    vm.all_elements = vision_elements
                    compact = vm.to_compact_text()
                    screen_state["text_map"] = compact
                    screen_state["elements_raw"] = [e.to_dict() for e in vision_elements]
                    screen_state["_source"] = "vision"
                    logger.info("Vision fallback: %d elements detected", len(vision_elements))
            except Exception as vis_exc:
                logger.warning("Vision fallback also failed: %s", vis_exc)
                screen_state["text_map"] = f"(All detection methods unavailable)"
                screen_state["elements_raw"] = []

        screen_state["active_window"] = active_window

        # Vision / hybrid: add screenshot + markers
        if self.mode in ("vision", "hybrid"):
            flat = flatten_elements(ui_elements)

            # Overlay numbered badges
            marked_screenshot = overlay_markers(screenshot, flat)
            screen_state["screenshot_base64"] = image_to_base64(
                marked_screenshot, fmt="PNG", quality=config.uacc.screenshot_quality
            )

            # Build legend
            screen_state["marker_legend"] = build_marker_legend(flat)

        _obs_duration = _observe_time.time() - _obs_start
        logger.debug("Observe: %.2fs elements=%d window=%s", _obs_duration,
                      len(screen_state.get("elements_raw", [])), active_window)

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
        """Execute an action with pre/post verification and multi-tier recovery.

        Recovery tiers:
          1. Local refinement — retry with corrected coordinates (up to max_retries)
          2. Modality shift   — try a different approach (e.g. type→clipboard)
          3. Backtrack        — restore known-good state, try alternative path
          4. Escalate         — request user guidance

        Returns the execution result dict.
        """
        import time as _exec_time
        _exec_start = _exec_time.time()
        action_name = getattr(action, "action", "?")
        max_retries = self.verifier.max_retries
        used_modality_shift = False

        # ── Safety gate ────────────────────────────────────
        risk_level = self.risk_classifier.classify_action(action)
        safety_decision = self.safety_gate.decide(action, risk_level)
        logger.debug("Safety: action=%s risk=%s decision=%s", action_name, risk_level.name, safety_decision.decision.value)
        if safety_decision.is_blocked():
            print(f"  🛡️  BLOCKED by safety policy: {safety_decision.reason}")
            return {"success": False, "message": safety_decision.reason, "blocked": True}
        if safety_decision.needs_confirmation():
            if config.uacc.safety_ask_confirmation:
                print(f"  🛡️  Confirmation required: {safety_decision.reason}")
                print(f"     Action: {action_name} (risk: {risk_level.name})")
            else:
                print(f"  🛡️  {safety_decision.reason} — proceeding (auto-confirm)")

        for attempt in range(max_retries + 1):
            # ── Pre-verify ──────────────────────────────────
            elements_raw = screen_state.get("elements_raw", [])
            pre_result = self.verifier.pre_verify(action, elements_raw)
            if pre_result.corrected_x is not None:
                action = self.verifier.apply_correction(action, pre_result)
                if attempt == 0:
                    print(f"  🔧 Auto-corrected coordinates → ({pre_result.corrected_x}, {pre_result.corrected_y})")

            # ── Execute ─────────────────────────────────────
            if attempt == 0:
                print(f"  ▶️  Executing: {action_name}")
            else:
                print(f"  🔄 Retry {attempt}/{max_retries}: {action_name}")
            exec_start = _exec_time.time()

            result = self.executor.execute(action)
            exec_duration = _exec_time.time() - exec_start
            success = result.get("success", False)
            message = result.get("message", "")

            logger.debug("Executed: action=%s success=%s duration=%.2fs attempt=%d", action_name, success, exec_duration, attempt)
            self.memory.record_action(action_to_dict(action), result)

            if success:
                print(f"  ✓  {message}")
                # Record in semantic memory
                app_name = screen_state.get("active_window", "")
                element_label = getattr(action, "target_text", "") or getattr(action, "text", "")[:30]
                self.semantic_graph.record_action_sequence(
                    app_name=app_name,
                    action_name=action_name,
                    element_label=element_label,
                    result="success",
                )
            else:
                print(f"  ✗  {message}")

            # ── Post-verify ─────────────────────────────────
            if isinstance(action, (WaitAction, ScreenshotAction)):
                result["post_verified"] = True
                self.recovery.handle_success()
                return result

            post_result = self.verifier.post_verify(
                action,
                expected_change=True,
                text_map=screen_state.get("text_map"),
                window_title=screen_state.get("active_window", ""),
            )
            if post_result.passed:
                result["post_verified"] = True
                self.recovery.handle_success()
                return result

            # ── Handle failure ──────────────────────────────
            logger.info("Post-verify failed: action=%s reason=%s", action_name, post_result.message)
            print(f"  ⚠️  Post-verify: {post_result.message}")

            # Generate reflection for cross-session learning
            classification = self.reflector.analyze_failure(
                action, result, screen_state, self._current_task
            )
            self.reflector.generate_reflection(
                classification, action, screen_state, self._current_task
            )
            if classification.confidence > 0.5:
                print(f"  🔍 Failure analysis: {classification.root_cause}")
                logger.info("Failure classified: type=%s root=%s", classification.failure_class, classification.root_cause)

            # Consult recovery engine
            recovery = self.recovery.handle_failure(action, result, screen_state)
            logger.warning("Recovery: tier=%s desc=%s", recovery.tier.value, recovery.description)
            print(f"  🏥 Recovery: {recovery.description}")

            # Tier 2: modality shift (try once)
            if recovery.tier == RecoveryTier.MODALITY_SHIFT and not used_modality_shift:
                alt_action = recovery.action
                if alt_action and getattr(alt_action, "action", "") != action_name:
                    used_modality_shift = True
                    action = alt_action
                    action_name = getattr(action, "action", "?")
                    print(f"  🔀 Modality shift → {action_name}")
                    import time
                    time.sleep(0.3)
                    screen_state = self._observe()
                    continue

            # Tier 3+: backtrack or escalate — break out of retry loop
            if recovery.tier in (RecoveryTier.BACKTRACK, RecoveryTier.ESCALATE):
                print(f"  ⛔ {recovery.description}")
                break

            # Tier 1: retry with fresh observation
            if attempt < max_retries:
                import time
                time.sleep(0.5)
                screen_state = self._observe()

        return result

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent stop requested")
