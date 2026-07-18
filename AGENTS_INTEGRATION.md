# UACC AI Agents Integration Guide

Universal AI Computer Control (UACC) operates as a high-performance **Model Context Protocol (MCP)** server. Any AI agent supporting the MCP standard can connect to UACC to observe the screen, list and manage desktop windows, interact with the clipboard, and simulate mouse/keyboard actions.

This guide provides step-by-step configuration instructions for popular AI agents.

---

## 1. Claude Code (CLI)

[Claude Code](https://claude.ai) is Anthropic's official terminal-based agent. It can connect to local MCP servers to run tools.

### Add via CLI Command
Open your terminal and run the following command (substituting the absolute path to your UACC python virtual environment):

```bash
claude mcp add uacc C:\Users\chris\Desktop\UACC\.venv\Scripts\python.exe -m uacc.mcp
```
*(On macOS/Linux, use `.../bin/python` instead of `...\Scripts\python.exe`).*

### Configuration Location
Claude Code stores its global MCP configuration in:
- **Windows**: `~/.claude/settings.json` or project-local `.claude/settings.json`
- **macOS / Linux**: `~/.claude/settings.json`

To configure it manually by editing the JSON, add:

```json
{
  "mcpServers": {
    "uacc": {
      "command": "C:\\Users\\chris\\Desktop\\UACC\\.venv\\Scripts\\python.exe",
      "args": ["-m", "uacc.mcp"],
      "env": {}
    }
  }
}
```

---

## 2. Hermes Agent (Nous Research)

[Hermes Agent](https://github.com/NousResearch/Hermes) is an autonomous, self-improving agent that runs as a persistent daemon.

### Add via CLI Command
Configure the daemon to use UACC by running:

```bash
hermes mcp add uacc -- C:\Users\chris\Desktop\UACC\.venv\Scripts\python.exe -m uacc.mcp
```

### Restart Daemon
After modifying the tools, restart the Hermes MCP manager to refresh the tool list:

```bash
hermes mcp restart
```

Verify that the tools are successfully registered:

```bash
hermes mcp list
```

---

## 3. OpenCode

[OpenCode](https://opencode.ai) is an open-source, model-agnostic coding assistant with terminal-based TUI.

### Add via JSON/JSONC Configuration
OpenCode parses `opencode.json` (or `opencode.jsonc`) in the repository or user directory. Add UACC under the `"mcp"` block:

```jsonc
{
  "mcp": {
    "uacc": {
      "type": "local",
      "command": "C:\\Users\\chris\\Desktop\\UACC\\.venv\\Scripts\\python.exe",
      "args": ["-m", "uacc.mcp"],
      "enabled": true
    }
  }
}
```

### Add via CLI Command
Alternatively, run the interactive setup command:

```bash
opencode mcp add
```
Select **local**, enter name `uacc`, command `C:\Users\chris\Desktop\UACC\\.venv\Scripts\python.exe`, and arguments `-m uacc.mcp`.

---

## 4. Cursor / VS Code Extensions (Cline, Roo Code)

For IDE-based agents, UACC can be configured globally.

### Cursor IDE
1. Open Cursor Settings -> **Features** -> **MCP**.
2. Click **+ Add New MCP Server**.
3. Fill in the fields:
   - **Name**: `uacc`
   - **Type**: `command`
   - **Command**: `C:\Users\chris\Desktop\UACC\.venv\Scripts\python.exe -m uacc.mcp`

### Cline / Roo Code (VS Code Extensions)
Open your extension's global MCP settings file:
- **Windows**: `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`
- **macOS**: `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`

Add the server:

```json
{
  "mcpServers": {
    "uacc": {
      "command": "C:\\Users\\chris\\Desktop\\UACC\\.venv\\Scripts\\python.exe",
      "args": ["-m", "uacc.mcp"],
      "disabled": false
    }
  }
}
```

---

## 5. Advanced: SSE & Streamable HTTP Transports

If your agent is running in a remote Docker container, a different machine, or inside a browser-based sandboxed environment, UACC supports network-based transports.

### Server Side (Run UACC over Network)
Start UACC in SSE mode or streamable-http mode:

```bash
# Start SSE server on port 8765
C:\Users\chris\Desktop\UACC\.venv\Scripts\python.exe -m uacc.mcp --transport sse --port 8765

# Start Streamable-HTTP server on port 8765
C:\Users\chris\Desktop\UACC\.venv\Scripts\python.exe -m uacc.mcp --transport streamable-http --port 8765
```

### Client Side Configuration

#### OpenCode (Remote)
Configure it in `opencode.json`:
```json
{
  "mcp": {
    "uacc-remote": {
      "type": "remote",
      "url": "http://127.0.0.1:8765/mcp",
      "enabled": true
    }
  }
}
```

#### Claude Code (SSE)
```bash
claude mcp add uacc --transport sse http://127.0.0.1:8765/sse
```

---

## 6. Developer Optimization Tips

To get the most out of UACC when pair programming with an agent:

1. **Safety Controls (`safe_mode`)**:
   By default, `safe_mode` is enabled to block destructive actions. Keep this on so the AI agent does not accidentally execute system compromises or delete files.
2. **Coordinate Auto-Correction**:
   UACC has built-in coordinate validation. If the agent makes a minor mathematical error in mouse alignment, UACC automatically clamps the values to active screen boundaries.
3. **Ask Permission**:
   You can specify security constraints in agent configuration to prompt for approval on write actions (e.g. mouse clicks or typing) while allowing read actions (e.g. window listing, screenshotting) to run instantly in the background.
