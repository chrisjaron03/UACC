"""
UACC — Universal AI Computer Control
Let any LLM control a computer with pixel-precise UI interactions.
"""

__version__ = "0.2.0"

import sys
# Clean up any hermes paths from sys.path to prevent binary compatibility issues with Python 3.13
sys.path = [p for p in sys.path if 'hermes' not in p.lower() and 'hermes-agent' not in p.lower()]

from uacc.config import config  # noqa: F401
