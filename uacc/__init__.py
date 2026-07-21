"""
UACC — Universal AI Computer Control
Let any LLM control a computer with pixel-precise UI interactions.

When running inside an agent-hosted environment (e.g. Hermes) that injects
its own venv into sys.path, incompatible binary wheels (pydantic_core,
PIL._imaging, numpy C-extensions) can collide with UACC's own dependencies.

This module strips the host agent's site-packages from sys.path so that
UACC's pip-installed dependencies resolve correctly.
"""

__version__ = "1.0.0"

import sys as _sys

_host_prefixes = ("hermes-agent", "hermes")
_sys.path = [p for p in _sys.path if not any(h in p.lower() for h in _host_prefixes)]

from uacc.config import config  # noqa: F401
