"""
UACC MCP Server — Model Context Protocol server for Universal AI Computer Control.

Exposes UACC's computer-control capabilities as MCP tools that any
AI agent (Claude Desktop, Cursor, Cline, etc.) can call directly.
"""

import sys
# Clean up any hermes paths from sys.path to prevent binary compatibility issues with Python 3.13
sys.path = [p for p in sys.path if 'hermes' not in p.lower() and 'hermes-agent' not in p.lower()]

__version__ = "0.2.0"
