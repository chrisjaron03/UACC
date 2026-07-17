"""
Hybrid Adapter — sends BOTH screenshot + text map for maximum accuracy.

Cross-validates the visual and textual understanding to resolve
ambiguities and improve coordinate precision.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from openai import OpenAI

from uacc.config import config
from uacc.models.base_adapter import SYSTEM_PROMPT, BaseAdapter

logger = logging.getLogger(__name__)

HYBRID_SYSTEM_ADDENDUM = """

## Hybrid Mode — Dual-Channel Input
You receive TWO representations of the screen:
1. **Screenshot** — visual image with numbered element badges
2. **Text Map** — structured text listing every element with exact coordinates

Use BOTH to make decisions:
- The screenshot shows you what things look like (colors, layout, visual state)
- The text map gives you EXACT coordinates and element metadata

When coordinates from the legend differ from what you visually estimate, \
**trust the text map coordinates** — they come from the OS accessibility tree \
and are always pixel-accurate.
"""


class HybridAdapter(BaseAdapter):
    """Sends both screenshot + text map for maximum accuracy."""

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs,
    ):
        _model = model or config.llm.openai_model
        _api_key = api_key or config.llm.openai_api_key
        _base_url = base_url or config.llm.openai_base_url
        super().__init__(model=_model, api_key=_api_key, base_url=_base_url, **kwargs)
        self._client: Optional[OpenAI] = None

    def _get_client(self) -> OpenAI:
        if self._client is None:
            kwargs: Dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def _build_messages(
        self,
        task: str,
        screen_state: Dict[str, Any],
        action_history: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Build multimodal messages with BOTH screenshot AND text map."""
        system_content = SYSTEM_PROMPT + HYBRID_SYSTEM_ADDENDUM

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content},
        ]

        content_parts: List[Dict[str, Any]] = []

        # ── Text Context ────────────────────────────────────
        text_sections = [f"## Current Task\n{task}\n"]

        # Text map
        text_map = screen_state.get("text_map", "")
        if text_map:
            text_sections.append(f"## Screen State (Text Map)\n```\n{text_map}\n```\n")

        # Element legend from markers
        legend = screen_state.get("marker_legend", "")
        if legend:
            text_sections.append(f"## Element Legend (Badge Numbers)\n```\n{legend}\n```\n")

        # Action history
        if action_history:
            history_str = "\n".join(
                f"- Action: {h.get('action', '?')} → {h.get('message', '?')}"
                for h in action_history[-10:]
            )
            text_sections.append(f"## Recent Action History\n{history_str}\n")

        # Last result
        if action_history:
            last = action_history[-1]
            text_sections.append(
                f"## Last Action Result\n"
                f"Success: {last.get('success', '?')} | "
                f"Message: {last.get('message', '?')}\n"
            )

        text_sections.append(
            "## Your Response\n"
            "Use BOTH the screenshot and the text map to determine the next "
            "action(s). Respond with JSON."
        )

        content_parts.append({"type": "text", "text": "\n".join(text_sections)})

        # ── Screenshot ──────────────────────────────────────
        screenshot_b64 = screen_state.get("screenshot_base64", "")
        if screenshot_b64:
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{screenshot_b64}",
                        "detail": "high",
                    },
                }
            )

        messages.append({"role": "user", "content": content_parts})

        # Multi-turn history (text only to save tokens)
        if self._conversation_history:
            messages = [messages[0]] + self._conversation_history[-4:] + [messages[-1]]

        return messages

    def _call_llm(self, messages: List[Dict[str, Any]]) -> str:
        """Call the vision-capable API with hybrid input."""
        client = self._get_client()

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            content = response.choices[0].message.content or ""

            # Save abbreviated context to history
            user_content = messages[-1].get("content", "")
            if isinstance(user_content, list):
                user_text = " ".join(
                    p.get("text", "")[:200]
                    for p in user_content
                    if p.get("type") == "text"
                )
            else:
                user_text = str(user_content)[:500]

            self._conversation_history.append(
                {"role": "user", "content": user_text}
            )
            self._conversation_history.append(
                {"role": "assistant", "content": content}
            )

            logger.debug("Hybrid LLM response (%d chars): %s", len(content), content[:200])
            return content

        except Exception as exc:
            logger.error("Hybrid LLM API call failed: %s", exc)
            raise
