"""
Text Adapter — sends structured text maps to text-only LLMs.

Works with any OpenAI-compatible API: GPT-4, Llama, Mistral, Qwen,
Phi, local Ollama, vLLM, Together, Groq, etc.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from openai import OpenAI

from uacc.config import config
from uacc.models.base_adapter import SYSTEM_PROMPT, BaseAdapter

logger = logging.getLogger(__name__)


class TextAdapter(BaseAdapter):
    """Adapter for text-only LLMs — sends the text map representation."""

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs,
    ):
        _model = model or config.llm.local_model or config.llm.openai_model
        _api_key = api_key or config.llm.openai_api_key or "not-needed"
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
        """Build text-only messages for the LLM."""
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        # Build the user message with screen state
        user_parts = [f"## Current Task\n{task}\n"]

        # Add text map (the core screen representation)
        text_map = screen_state.get("text_map", "")
        if text_map:
            user_parts.append(f"## Current Screen State\n```\n{text_map}\n```\n")

        # Add action history context
        if action_history:
            history_str = "\n".join(
                f"- Action: {h.get('action', '?')} → {h.get('message', '?')}"
                for h in action_history[-10:]  # Last 10 actions
            )
            user_parts.append(f"## Recent Action History\n{history_str}\n")

        # Add the last action result
        if action_history:
            last = action_history[-1]
            user_parts.append(
                f"## Last Action Result\n"
                f"Action: {last.get('action', '?')}\n"
                f"Success: {last.get('success', '?')}\n"
                f"Message: {last.get('message', '?')}\n"
            )

        user_parts.append(
            "## Your Response\n"
            "Analyze the screen state and respond with the next action(s) as JSON."
        )

        messages.append({"role": "user", "content": "\n".join(user_parts)})

        # Include conversation history for multi-turn context
        if self._conversation_history:
            # Insert history between system and current user message
            messages = [messages[0]] + self._conversation_history[-6:] + [messages[-1]]

        return messages

    def _call_llm(self, messages: List[Dict[str, Any]]) -> str:
        """Call the OpenAI-compatible API."""
        client = self._get_client()

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            content = response.choices[0].message.content or ""

            # Save to conversation history
            self._conversation_history.append(
                {"role": "user", "content": messages[-1]["content"]}
            )
            self._conversation_history.append(
                {"role": "assistant", "content": content}
            )

            logger.debug("LLM response (%d chars): %s", len(content), content[:200])
            return content

        except Exception as exc:
            logger.error("LLM API call failed: %s", exc)
            raise
