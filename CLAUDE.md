# UACC — AI Agent Instructions

Use MCP tools (`mcp_uacc_*`) directly. Do NOT write separate Python scripts wrapping UACC.

## Workflow Memory
UACC has built-in persistent workflow memory. Save multi-step automations with `create_workflow`, list them with `list_workflows`, inspect with `get_workflow`, delete with `delete_workflow`, and replay with `run_workflow`. Workflows survive agent restarts (stored in `~/.uacc/workflows/`).

See AGENTS.md for full details and examples.
