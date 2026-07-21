from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    """Definition of a registered tool."""

    name: str
    description: str
    handler: Callable[..., Any]
    params_schema: Dict[str, Any] = field(default_factory=dict)
    side_effects: List[str] = field(default_factory=list)
    risk_level: str = "medium"


class ToolRegistry:
    _tools: Dict[str, ToolDef] = {}

    @classmethod
    def register(cls, tool_def: ToolDef) -> None:
        cls._tools[tool_def.name] = tool_def
        logger.debug("Registered tool: %s (risk=%s)", tool_def.name, tool_def.risk_level)

    @classmethod
    def get(cls, name: str) -> Optional[ToolDef]:
        return cls._tools.get(name)

    @classmethod
    def list(cls) -> List[ToolDef]:
        return list(cls._tools.values())

    @classmethod
    def clear(cls) -> None:
        cls._tools.clear()


def tool(
    name: str | None = None,
    *,
    risk_level: str = "medium",
    side_effects: List[str] | None = None,
) -> Callable:
    """Decorator that registers an MCP tool function in the ToolRegistry.

    Usage:
        @tool(risk_level="write")
        def my_tool(param: str) -> str: ...

    Args:
        name: Override for the tool name (defaults to function name).
        risk_level: One of \"read\", \"navigate\", \"write\".
        side_effects: e.g. [\"mouse_move\", \"keyboard_input\", \"clipboard\"].

    Returns:
        The decorated function (unchanged).
    """
    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__
        sig = inspect.signature(fn)

        schema: Dict[str, Any] = {
            "name": tool_name,
            "description": fn.__doc__ or "",
            "parameters": {
                pname: {
                    "annotation": str(param.annotation) if param.annotation is not inspect.Parameter.empty else "Any",
                    "default": (param.default if param.default is not inspect.Parameter.empty else None),
                }
                for pname, param in sig.parameters.items()
            },
        }

        ToolRegistry.register(ToolDef(
            name=tool_name,
            description=fn.__doc__ or "",
            handler=fn,
            params_schema=schema,
            side_effects=side_effects or [],
            risk_level=risk_level,
        ))
        return fn

    return decorator
