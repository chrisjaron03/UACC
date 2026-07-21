from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from uacc.config import config
from uacc.models.base_adapter import SYSTEM_PROMPT, BaseAdapter

logger = logging.getLogger(__name__)


class AnthropicAdapter(BaseAdapter):
    """Adapter for Anthropic Claude models — uses the Anthropic API directly.

    Supports both text-only and vision modes. When a screenshot is provided
    in screen_state, it's sent as a base64 image to Claude.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs,
    ):
        _model = model or config.llm.anthropic_model
        _api_key = api_key or config.llm.anthropic_api_key
        super().__init__(model=_model, api_key=_api_key, base_url=base_url, **kwargs)
        self._client: Optional[Any] = None

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            kwargs: Dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def _build_messages(
        self,
        task: str,
        screen_state: Dict[str, Any],
        action_history: List[Dict[str, Any]],
    ) -> tuple[str, List[Dict[str, Any]]]:
        text_parts: List[str] = []

        task_text = f"## Current Task\n{task}\n"
        text_parts.append(task_text)

        legend = screen_state.get("marker_legend", "")
        if legend:
            text_parts.append(f"\n## Element Legend\n```\n{legend}\n```\n")

        text_map = screen_state.get("text_map", "")
        if text_map:
            text_parts.append(f"\n## Screen State\n{text_map}\n")

        if action_history:
            history_str = "\n".join(
                f"- Action: {h.get('action', '?')} → {h.get('message', '?')}"
                for h in action_history[-10:]
            )
            text_parts.append(f"\n## Recent Action History\n{history_str}\n")

        text_parts.append(
            "\n## Your Response\n"
            "Respond with the next action(s) as JSON. Use exact pixel coordinates."
        )

        user_content: List[Dict[str, Any]] = [
            {"type": "text", "text": "\n".join(text_parts)}
        ]

        screenshot_b64 = screen_state.get("screenshot_base64", "")
        if screenshot_b64:
            user_content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": screenshot_b64,
                    },
                }
            )

        return SYSTEM_PROMPT, [{"role": "user", "content": user_content}]

    def _call_llm(self, messages: List[Dict[str, Any]]) -> str:
        client = self._get_client()
        system_prompt, msgs = self._build_messages(
            messages[0].get("content", ""),
            messages[1].get("screen_state", {}) if len(messages) > 1 else {},
            [],
        )

        try:
            response = client.messages.create(
                model=self.model,
                system=system_prompt,
                messages=msgs,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )

            content = response.content[0].text if response.content else ""

            logger.debug("Anthropic response (%d chars): %s", len(content), content[:200])
            return content

        except Exception as exc:
            logger.error("Anthropic API call failed: %s", exc)
            raise

    def observe_and_act(
        self,
        task: str,
        screen_state: Dict[str, Any],
        action_history: List[Dict[str, Any]],
    ) -> list:
        system_prompt, messages = self._build_messages(task, screen_state, action_history)
        client = self._get_client()

        try:
            response = client.messages.create(
                model=self.model,
                system=system_prompt,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )

            raw_response = response.content[0].text if response.content else ""
            actions = self._parse_response(raw_response)

            self._conversation_history.append(
                {"role": "user", "content": task[:500]}
            )
            self._conversation_history.append(
                {"role": "assistant", "content": raw_response}
            )

            logger.info("Anthropic returned %d action(s)", len(actions))
            return actions

        except Exception as exc:
            logger.error("Anthropic observe_and_act failed: %s", exc)
            raise
