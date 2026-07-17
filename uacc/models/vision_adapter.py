"""
Vision Adapter — sends gridded screenshots + element legends to vision LLMs.

Works with GPT-4o, Claude (via OpenAI-compat proxy), Gemini, Llama-Vision, Qwen-VL.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from openai import OpenAI

from uacc.config import config
from uacc.models.base_adapter import SYSTEM_PROMPT, BaseAdapter

logger = logging.getLogger(__name__)


class VisionAdapter(BaseAdapter):
    """Adapter for vision-capable LLMs — sends screenshots with grid overlays."""

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
        """Build multimodal messages with screenshot + text context."""
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        # Build user message content parts (text + image)
        content_parts: List[Dict[str, Any]] = []

        # Task description
        task_text = f"## Current Task\n{task}\n"

        # Element legend (if markers are on the screenshot)
        legend = screen_state.get("marker_legend", "")
        if legend:
            task_text += f"\n## Element Legend\n```\n{legend}\n```\n"

        # Action history
        if action_history:
            history_str = "\n".join(
                f"- Action: {h.get('action', '?')} → {h.get('message', '?')}"
                for h in action_history[-10:]
            )
            task_text += f"\n## Recent Action History\n{history_str}\n"

        task_text += (
            "\n## Your Response\n"
            "Look at the screenshot and the element positions, then respond "
            "with the next action(s) as JSON. Use exact pixel coordinates."
        )

        content_parts.append({"type": "text", "text": task_text})

        # Screenshot (base64)
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

        # Multi-turn history
        if self._conversation_history:
            messages = [messages[0]] + self._conversation_history[-4:] + [messages[-1]]

        return messages

    def _call_llm(self, messages: List[Dict[str, Any]]) -> str:
        """Call the vision-capable API."""
        client = self._get_client()

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            content = response.choices[0].message.content or ""

            # Save text portion to history (skip images to save tokens)
            user_text = ""
            user_content = messages[-1].get("content", "")
            if isinstance(user_content, list):
                user_text = " ".join(
                    p.get("text", "") for p in user_content if p.get("type") == "text"
                )
            else:
                user_text = str(user_content)

            self._conversation_history.append(
                {"role": "user", "content": user_text[:500]}
            )
            self._conversation_history.append(
                {"role": "assistant", "content": content}
            )

            logger.debug("Vision LLM response (%d chars): %s", len(content), content[:200])
            return content

        except Exception as exc:
            logger.error("Vision LLM API call failed: %s", exc)
            raise
