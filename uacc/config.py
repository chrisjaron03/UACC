"""
UACC Configuration — central settings loaded from environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


@dataclass
class LLMConfig:
    """LLM provider settings."""

    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o"))
    openai_base_url: str | None = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL"))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    )
    local_model: str = field(default_factory=lambda: os.getenv("LOCAL_MODEL", ""))


@dataclass
class UACCConfig:
    """Framework-level settings."""

    mode: Literal["text", "vision", "hybrid"] = field(
        default_factory=lambda: os.getenv("UACC_MODE", "hybrid")  # type: ignore[arg-type]
    )
    grid_mode: Literal["coarse", "medium", "fine", "micro"] = field(
        default_factory=lambda: os.getenv("UACC_GRID_MODE", "medium")  # type: ignore[arg-type]
    )
    safe_mode: bool = field(
        default_factory=lambda: os.getenv("UACC_SAFE_MODE", "true").lower() == "true"
    )
    max_iterations: int = field(
        default_factory=lambda: int(os.getenv("UACC_MAX_ITERATIONS", "30"))
    )
    human_mimicry: bool = field(
        default_factory=lambda: os.getenv("UACC_HUMAN_MIMICRY", "true").lower() == "true"
    )
    action_delay_ms: int = field(
        default_factory=lambda: int(os.getenv("UACC_ACTION_DELAY_MS", "150"))
    )
    screenshot_quality: int = field(
        default_factory=lambda: int(os.getenv("UACC_SCREENSHOT_QUALITY", "80"))
    )
    pyautogui_failsafe: bool = field(
        default_factory=lambda: os.getenv("UACC_FAILSAFE", "true").lower() == "true"
    )

    # Safety policy
    safety_policy: str = field(
        default_factory=lambda: os.getenv("UACC_SAFETY_POLICY", "balanced")
    )
    safety_ask_confirmation: bool = field(
        default_factory=lambda: os.getenv("UACC_SAFETY_ASK_CONFIRMATION", "true").lower() == "true"
    )

    # Grid dimensions for each mode  (columns × rows)
    GRID_SIZES: dict = field(
        default_factory=lambda: {
            "coarse": (20, 12),
            "medium": (48, 27),
            "fine": (192, 108),
            "micro": (384, 216),
        },
        repr=False,
    )


@dataclass
class Config:
    """Top-level configuration container."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    uacc: UACCConfig = field(default_factory=UACCConfig)
    project_root: Path = _PROJECT_ROOT


# Singleton instance — import this everywhere
config = Config()
