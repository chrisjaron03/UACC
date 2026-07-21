"""
UACC Application Agents — pre-built modules for common desktop applications.

Each agent knows the standard UI layout, keyboard shortcuts, and
accessibility patterns for a specific app, enabling the UACC agent
to interact with it more efficiently.

Available agents:
  - OutlookAgent — Microsoft Outlook
  - ExcelAgent — Microsoft Excel
  - FileExplorerAgent — Windows File Explorer
  - ChromeAgent — Google Chrome

Usage:
    from uacc.apps import get_app_agent
    agent = get_app_agent("Outlook")
    if agent:
        prompt = agent.get_context_prompt()
        shortcuts = agent.get_shortcuts()
"""

from uacc.apps.registry import AppAgent, get_app_agent, list_agents, register_agent
from uacc.apps import chrome, excel, explorer, outlook

# Register all application agents
chrome.register()
excel.register()
explorer.register()
outlook.register()

__all__ = ["AppAgent", "get_app_agent", "list_agents", "register_agent"]
