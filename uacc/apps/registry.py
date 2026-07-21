"""
App Agent Registry — discover and retrieve application agents.

Agents are registered by name and can be looked up for any
supported application.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Type

from uacc.apps.base import AppAgent

logger = logging.getLogger(__name__)

_AGENT_REGISTRY: Dict[str, Type[AppAgent]] = {}


def register_agent(name: str, agent_class: Type[AppAgent]) -> None:
    _AGENT_REGISTRY[name.lower()] = agent_class
    logger.debug("Registered app agent: %s", name)


def get_app_agent(app_name: str, executor: Any = None) -> Optional[AppAgent]:
    """Get an application agent by name.

    Args:
        app_name: Application name (case-insensitive).
        executor: Optional ActionExecutor for tool execution.

    Returns:
        AppAgent instance, or None if no agent exists for this app.
    """
    cls = _AGENT_REGISTRY.get(app_name.lower())
    if cls is None:
        return None
    return cls(executor=executor)


def list_agents() -> List[Dict[str, str]]:
    return [
        {"name": name, "description": cls.__doc__ or ""}
        for name, cls in _AGENT_REGISTRY.items()
    ]
