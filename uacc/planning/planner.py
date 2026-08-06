"""
Goal Decomposer — LLM-based task decomposition with cross-session memory context.

Accepts a natural language goal and produces a structured execution plan:
- Decomposes into atomic steps
- Maps each step to the optimal UACC tool
- Includes verification checkpoints
- Uses the semantic knowledge graph for app-specific context
- Falls back to heuristic decomposition when no LLM is configured
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from uacc.config import config
from uacc.memory.semantic_graph import SemanticGraph

logger = logging.getLogger(__name__)

TOOL_CATALOG = {
    "screenshot": "Capture the screen (full or region)",
    "get_screen_info": "Get structured text map of all UI elements on screen",
    "click": "Click at exact screen coordinates",
    "click_element": "Click a UI element by name (fuzzy matched)",
    "type_text": "Type text via keyboard",
    "type_clipboard": "Paste text via clipboard (for unicode/special chars)",
    "hotkey": "Press key combinations (e.g. Ctrl+S)",
    "scroll": "Scroll at a position",
    "drag": "Drag from point A to B",
    "hover": "Move mouse to position",
    "find_element": "Search for UI elements by name/type",
    "get_mouse_position": "Get current cursor coordinates",
    "get_active_window": "Get info about the focused window",
    "list_windows": "List all open windows",
    "focus_window": "Bring a window to front",
    "launch_app": "Launch an application",
    "open_url": "Open a URL in the default browser",
    "clipboard_read": "Read text from clipboard",
    "clipboard_write": "Write text to clipboard",
    "wait_for_element": "Poll until an element appears on screen",
    "smart_click": "Self-healing click with multi-strategy fallback (a11y -> OCR -> vision)",
    "smart_type": "Self-healing type with field targeting + verification",
    "get_action_history": "Review recent actions",
    "execute_actions": "Execute multiple actions in sequence as one call",
    "verify_action": "Verify the last action had the expected effect",
    "take_snapshot": "Save a named screenshot for later comparison",
    "compare_snapshots": "Compare two named snapshots (before/after)",
    "browser_query": "Find DOM elements by CSS selector in browser",
    "browser_click": "Click a DOM element in browser",
    "browser_type": "Type into a DOM element in browser",
    "browser_navigate": "Navigate to a URL in browser",
    "browser_get_page_info": "Get page metadata from browser",
    "browser_execute_js": "Execute JavaScript in browser context",
    "focus_window": "Focus an application window",
}


class GoalDecomposer:
    """Decomposes natural language goals into executable step sequences.

    Two modes:
    1. LLM mode — calls OpenAI/Anthropic to intelligently decompose the goal
    2. Heuristic mode — keyword-based fallback when no LLM is available
    """

    def __init__(self):
        self._semantic_graph = SemanticGraph()
        self._tools_available = list(TOOL_CATALOG.keys())
        self._plan_cache: Dict[tuple, Dict[str, Any]] = {}
        self._plan_cache_ttl: float = 60.0  # seconds

    def _get_cached_plan(self, key: tuple) -> Optional[Dict[str, Any]]:
        entry = self._plan_cache.get(key)
        if entry is not None:
            age = time.monotonic() - entry["ts"]
            if age < self._plan_cache_ttl:
                logger.debug("Plan cache hit (%.1f s old)", age)
                return entry["plan"]
            del self._plan_cache[key]
        return None

    def _store_plan(self, key: tuple, plan: Dict[str, Any]) -> None:
        self._plan_cache[key] = {"plan": plan, "ts": time.monotonic()}
        # Evict oldest entry if cache exceeds limit
        if len(self._plan_cache) > 64:
            oldest = min(self._plan_cache, key=lambda k: self._plan_cache[k]["ts"])
            del self._plan_cache[oldest]

    def decompose(
        self,
        task_description: str,
        target_app: str = "",
        speed_mode: str = "fast",
    ) -> Dict[str, Any]:
        """Decompose a task into an executable plan.

        Results are cached with a 60-second TTL so repeated calls with the
        same task description avoid redundant LLM round-trips.

        Args:
            task_description: Natural language description of the goal.
            target_app: Optional target application name.
            speed_mode: "fast" (direct) or "thorough" (with verification).

        Returns:
            Plan dict with steps, tool recommendations, and reasoning.
        """
        cache_key = (task_description, target_app, speed_mode)
        cached = self._get_cached_plan(cache_key)
        if cached is not None:
            return cached

        app_context = self._get_app_context(target_app)

        plan = self._try_llm_decompose(task_description, target_app, speed_mode, app_context)
        if plan is not None:
            self._store_plan(cache_key, plan)
            return plan

        plan = self._heuristic_decompose(task_description, target_app, speed_mode, app_context)
        self._store_plan(cache_key, plan)
        return plan

    def _get_app_context(self, target_app: str) -> Dict[str, Any]:
        """Query the semantic graph for known app patterns."""
        if not target_app:
            return {"known": False}

        patterns = self._semantic_graph.get_app_patterns(target_app)
        if patterns:
            related = self._semantic_graph.find_similar_apps(target_app)
            return {
                "known": True,
                "app": target_app,
                "patterns": patterns.get("patterns", {}),
                "related_apps": related,
                "last_seen": patterns.get("last_seen", ""),
            }
        return {"known": False, "app": target_app}

    def _try_llm_decompose(
        self,
        task: str,
        target_app: str,
        speed: str,
        app_context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Try to use a configured LLM to decompose the goal."""
        provider = self._detect_provider()

        if provider == "openai":
            return self._call_openai(task, target_app, speed, app_context)
        elif provider == "anthropic":
            return self._call_anthropic(task, target_app, speed, app_context)
        elif provider == "local":
            return self._call_local(task, target_app, speed, app_context)

        return None

    def _detect_provider(self) -> str:
        """Detect which LLM provider is configured."""
        llm = config.llm
        if llm.openai_api_key:
            return "openai"
        if llm.anthropic_api_key:
            return "anthropic"
        if llm.local_model:
            return "local"
        return ""

    def _build_system_prompt(self, speed: str, app_context: Dict[str, Any]) -> str:
        """Build the system prompt for LLM-based decomposition."""
        context_section = ""
        if app_context.get("known"):
            patterns = app_context.get("patterns", {})
            context_section = f"""
## Known Application Context
The agent has previously interacted with **{app_context['app']}**.
Known UI patterns:
{json.dumps(patterns, indent=2)}
Related applications: {', '.join(app_context.get('related_apps', []))}
Last seen: {app_context.get('last_seen', 'unknown')}
"""
        verification_instruction = ""
        if speed == "thorough":
            verification_instruction = """
- Include verification checkpoints AFTER every action (use `wait_for_element` or `screenshot`)
- Use `verify_action` to confirm expected changes
"""

        return f"""You are a computer control planner. Given a task, emit a JSON plan.

## Available Tools
{json.dumps(TOOL_CATALOG, indent=2)}
{context_section}
## Rules
1. Break the task into 1-8 atomic steps
2. Each step uses ONE tool from the catalog above
3. Output ONLY valid JSON — no markdown, no explanation
4. Use `smart_click` over `click` for reliability
5. Use `smart_type` over `type_text` when targeting a field
6. Prefer `hotkey` (Ctrl+S, Ctrl+C) over clicking menu items when possible
7. After launching an app, always include a `wait_for_element` step
8. {verification_instruction or "For speed: minimize verification steps, use direct tool calls"}

## Output Format
```json
{{
    "reasoning": "Brief explanation of the plan",
    "steps": [
        {{"step": 1, "tool": "...", "params": {{...}}, "reasoning": "Why this step"}}
    ],
    "estimated_duration_ms": <int>,
    "risk_level": "low|medium|high"
}}
```"""

    def _call_openai(
        self,
        task: str,
        target_app: str,
        speed: str,
        app_context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Call OpenAI to decompose the task."""
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=config.llm.openai_api_key,
                base_url=config.llm.openai_base_url,
            )

            system_prompt = self._build_system_prompt(speed, app_context)
            user_prompt = f"Task: {task}\nTarget app: {target_app or 'any'}"

            resp = client.chat.completions.create(
                model=config.llm.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
                response_format={"type": "json_object"},
            )

            content = resp.choices[0].message.content
            if content:
                plan = json.loads(content)
                plan["provider"] = "openai"
                plan["model"] = config.llm.openai_model
                return plan

        except ImportError:
            logger.info("openai package not installed — falling back to heuristic")
        except Exception as exc:
            logger.warning("OpenAI decomposition failed: %s", exc)

        return None

    def _call_anthropic(
        self,
        task: str,
        target_app: str,
        speed: str,
        app_context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Call Anthropic to decompose the task."""
        try:
            from anthropic import Anthropic

            client = Anthropic(api_key=config.llm.anthropic_api_key)

            system_prompt = self._build_system_prompt(speed, app_context)

            resp = client.messages.create(
                model=config.llm.anthropic_model,
                system=system_prompt,
                messages=[{"role": "user", "content": f"Task: {task}\nTarget app: {target_app or 'any'}"}],
                temperature=0.3,
                max_tokens=2000,
            )

            content = resp.content[0].text
            if content:
                # Strip possible markdown fences
                cleaned = content.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1]
                if cleaned.endswith("```"):
                    cleaned = cleaned.rsplit("\n", 1)[0]
                cleaned = cleaned.strip().removeprefix("json").strip()

                plan = json.loads(cleaned)
                plan["provider"] = "anthropic"
                plan["model"] = config.llm.anthropic_model
                return plan

        except ImportError:
            logger.info("anthropic package not installed — falling back to heuristic")
        except Exception as exc:
            logger.warning("Anthropic decomposition failed: %s", exc)

        return None

    def _call_local(
        self,
        task: str,
        target_app: str,
        speed: str,
        app_context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Call a local LLM (Ollama, etc.) via OpenAI-compatible API."""
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key="not-needed",
                base_url=config.llm.openai_base_url or "http://localhost:11434/v1",
            )

            system_prompt = self._build_system_prompt(speed, app_context)

            resp = client.chat.completions.create(
                model=config.llm.local_model or "qwen2.5:7b",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Task: {task}\nTarget app: {target_app or 'any'}"},
                ],
                temperature=0.3,
                max_tokens=2000,
            )

            content = resp.choices[0].message.content
            if content:
                plan = json.loads(content)
                plan["provider"] = "local"
                plan["model"] = config.llm.local_model
                return plan

        except ImportError:
            logger.info("openai package not installed for local LLM — falling back")
        except Exception as exc:
            logger.warning("Local LLM decomposition failed: %s", exc)

        return None

    def _heuristic_decompose(
        self,
        task: str,
        target_app: str,
        speed: str,
        app_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Keyword-based fallback decomposition."""
        desc_lower = task.lower()
        target_lower = target_app.lower()
        is_thorough = speed == "thorough"

        steps: List[Dict[str, Any]] = []
        recommended_tools: List[str] = []
        reasoning = ""
        estimated_ms = 0
        risk = "low"

        if any(w in desc_lower for w in ["browser", "web", "website", "url", "http", "internet", "page", "chrome", "firefox", "edge", "safari"]):
            recommended_tools = ["launch_app", "browser_navigate", "browser_query", "smart_click"]
            steps.append({"step": 1, "tool": "launch_app", "params": {"name_or_path": target_app or "chrome"}, "reasoning": "Launch the browser"})
            if any(w in desc_lower for w in ["navigate", "go to", "open", "visit", "load", "github", "google"]):
                url = _extract_url(task) or "https://example.com"
                steps.append({"step": 2, "tool": "browser_navigate", "params": {"url": url}, "reasoning": "Navigate to the target URL"})
                if is_thorough:
                    steps.append({"step": 3, "tool": "wait_for_element", "params": {"name": "loaded", "timeout_ms": 5000}, "reasoning": "Wait for page to load"})
            estimated_ms = 2000
            risk = "low"

        elif any(w in desc_lower for w in ["draw", "paint", "sketch", "art", "canvas", "picture"]):
            recommended_tools = ["list_fetched_images", "fetch_line_art", "launch_app", "paint_image", "screenshot"]
            steps.append({"step": 1, "tool": "list_fetched_images", "params": {}, "reasoning": "Check if a suitable image was already downloaded (reuse is faster)"})
            steps.append({"step": 2, "tool": "fetch_line_art", "params": {"query": "<subject>", "style": "outline"}, "reasoning": "MANDATORY — Download line art from internet if no cached image matches. You MUST have an image_path before calling paint_image."})
            steps.append({"step": 3, "tool": "launch_app", "params": {"name_or_path": target_app or "mspaint"}, "reasoning": "Open drawing application"})
            steps.append({"step": 4, "tool": "paint_image", "params": {"image_path": "<path from fetch_line_art result>", "max_strokes": 150}, "reasoning": "Trace the fetched image outline. Use image_path from Step 2's result."})
            if is_thorough:
                steps.append({"step": 5, "tool": "screenshot", "params": {}, "reasoning": "Capture the result"})
            estimated_ms = 5000
            risk = "low"

        elif any(w in desc_lower for w in ["launch", "open app", "start", "run program"]):
            recommended_tools = ["launch_app", "wait_for_element", "get_active_window"]
            app_name = target_app or _extract_app_name(task)
            steps.append({"step": 1, "tool": "launch_app", "params": {"name_or_path": app_name}, "reasoning": f"Launch {app_name}"})
            if is_thorough:
                steps.append({"step": 2, "tool": "wait_for_element", "params": {"name": app_name, "timeout_ms": 5000}, "reasoning": "Wait for app window to appear"})
                steps.append({"step": 3, "tool": "get_active_window", "params": {}, "reasoning": "Confirm app is focused"})
            estimated_ms = 1500
            risk = "low"

        elif any(w in desc_lower for w in ["click", "press", "select", "choose", "hit"]):
            recommended_tools = ["get_screen_info", "smart_click"]
            element = _extract_element_name(task)
            steps.append({"step": 1, "tool": "get_screen_info", "params": {}, "reasoning": "Scan current screen"})
            steps.append({"step": 2, "tool": "smart_click", "params": {"target": element}, "reasoning": f"Click on '{element}'"})
            if is_thorough:
                steps.append({"step": 3, "tool": "screenshot", "params": {}, "reasoning": "Verify the click result"})
            estimated_ms = 800
            risk = "low"

        elif any(w in desc_lower for w in ["type", "write", "enter text", "input", "fill"]):
            recommended_tools = ["smart_type"]
            text = _extract_text_to_type(task)
            field = _extract_field_name(task)
            params: Dict[str, Any] = {"text": text}
            if field:
                params["target_field"] = field
            if "clear" in desc_lower or "overwrite" in desc_lower or "replace" in desc_lower:
                params["clear_first"] = True
            steps.append({"step": 1, "tool": "smart_type", "params": params, "reasoning": f"Type text{f' into {field}' if field else ''}"})
            estimated_ms = 500
            risk = "low"

        elif any(w in desc_lower for w in ["copy", "cut", "paste", "clipboard"]):
            recommended_tools = ["hotkey", "clipboard_read", "clipboard_write"]
            if "copy" in desc_lower:
                steps.append({"step": 1, "tool": "hotkey", "params": {"keys": ["ctrl", "c"]}, "reasoning": "Copy selection"})
            elif "paste" in desc_lower:
                steps.append({"step": 1, "tool": "hotkey", "params": {"keys": ["ctrl", "v"]}, "reasoning": "Paste clipboard"})
            estimated_ms = 200
            risk = "low"

        elif any(w in desc_lower for w in ["find", "search", "look up", "locate"]):
            recommended_tools = ["get_screen_info", "find_element"]
            search_term = _extract_element_name(task)
            steps.append({"step": 1, "tool": "get_screen_info", "params": {}, "reasoning": "Scan screen"})
            steps.append({"step": 2, "tool": "find_element", "params": {"name": search_term}, "reasoning": f"Find '{search_term}' on screen"})
            estimated_ms = 600
            risk = "low"

        elif any(w in desc_lower for w in ["save", "export", "download"]):
            recommended_tools = ["hotkey", "smart_click", "wait_for_element"]
            if "save" in desc_lower:
                steps.append({"step": 1, "tool": "hotkey", "params": {"keys": ["ctrl", "s"]}, "reasoning": "Save via Ctrl+S"})
                if is_thorough:
                    steps.append({"step": 2, "tool": "wait_for_element", "params": {"name": "save", "timeout_ms": 3000}, "reasoning": "Wait for save dialog"})
            estimated_ms = 500
            risk = "low"

        elif any(w in desc_lower for w in ["close", "exit", "quit"]):
            recommended_tools = ["hotkey", "smart_click"]
            steps.append({"step": 1, "tool": "hotkey", "params": {"keys": ["alt", "f4"]}, "reasoning": "Close window via Alt+F4"})
            estimated_ms = 200
            risk = "low"

        else:
            recommended_tools = ["get_screen_info", "execute_actions"]
            steps.append({"step": 1, "tool": "get_screen_info", "params": {}, "reasoning": "Inspect the current screen state"})
            steps.append({"step": 2, "tool": "execute_actions", "params": {"actions": []}, "reasoning": "Execute actions based on screen state"})
            estimated_ms = 600
            risk = "medium"
            reasoning = "General computer control — inspect screen first, then act based on what's visible"

        # Add app context knowledge if available
        if app_context.get("known"):
            reasoning = (
                f"UACC has prior knowledge of '{app_context['app']}'. "
                f"Known patterns: {list(app_context.get('patterns', {}).keys())}. "
                f"{reasoning}"
            )

        return {
            "reasoning": reasoning or f"Heuristic decomposition of: {task}",
            "steps": steps,
            "recommended_tools": recommended_tools,
            "estimated_duration_ms": estimated_ms,
            "risk_level": risk,
            "provider": "heuristic",
        }


# ── Helper extractors ──────────────────────────────────────

def _extract_url(text: str) -> str:
    """Extract a URL-like pattern from text."""
    import re
    urls = re.findall(r'https?://[^\s]+', text)
    if urls:
        return urls[0]
    # Check for domain-like patterns
    domains = re.findall(r'\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b', text)
    if domains:
        return f"https://{domains[0]}"
    return ""


def _extract_app_name(text: str) -> str:
    """Extract a probable app name from task text."""
    stop_words = {"the", "a", "an", "my", "open", "launch", "start", "run", "app", "application", "program"}
    words = text.lower().split()
    for i, w in enumerate(words):
        if w in ("open", "launch", "start", "run") and i + 1 < len(words):
            candidate = words[i + 1].strip("'\",.!?;:")
            if candidate not in stop_words:
                return candidate
    for i, w in enumerate(words):
        if w not in stop_words and len(w) > 2:
            return w
    return "notepad"


def _extract_element_name(text: str) -> str:
    """Extract a probable element name from task text."""
    stop_words = {"the", "a", "an", "on", "in", "at", "click", "press", "select", "choose", "hit", "tap", "to", "and", "or", "for", "with", "into"}
    # Try to find quoted text first
    import re
    quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', text)
    if quoted:
        return quoted[0][0] or quoted[0][1]
    # Find after trigger words, skipping stop words
    trigger_words = {"click", "press", "select", "choose", "hit", "tap", "find", "open", "launch"}
    words = text.split()
    for i, w in enumerate(words):
        if w.lower() in trigger_words and i + 1 < len(words):
            # Skip through stop words to find the real target
            for j in range(i + 1, len(words)):
                candidate = words[j].strip("'\",.!?;:")
                if candidate.lower() not in stop_words and len(candidate) > 1:
                    return candidate
    # Fallback: last non-stop word
    for w in reversed(words):
        wc = w.strip("'\",.!?;:")
        if wc.lower() not in stop_words and len(wc) > 1:
            return wc
    return ""


def _extract_text_to_type(text: str) -> str:
    """Extract text to type from a task description."""
    import re
    quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', text)
    if quoted:
        return quoted[0][0] or quoted[0][1]
    # Fallback: return text after "type" or "write"
    words = text.split()
    for i, w in enumerate(words):
        if w.lower() in ("type", "write", "enter", "text") and i + 1 < len(words):
            return " ".join(words[i + 1:]).strip("'\",.!?;:")
    return text


def _extract_field_name(text: str) -> str:
    """Extract a field name from task description."""
    import re
    # "type X into Y" or "type in Y field"
    into_match = re.search(r'(?:into|in|to)\s+(?:the\s+)?["\']?([a-zA-Z\s]+?)["\']?\s*(?:field|input|box|textbox)?\s*(?:\.|,|$)', text, re.IGNORECASE)
    if into_match:
        return into_match.group(1).strip()
    field_match = re.search(r'["\']([^"\']+)["\']\s*(?:field|input|box)', text, re.IGNORECASE)
    if field_match:
        return field_match.group(1)
    return ""
