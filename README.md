<p align="center">
  <img src="assets/hero_banner.png" alt="UACC — Universal AI Computer Control" width="100%">
</p>

<h1 align="center">🖥️ UACC — Universal AI Computer Control</h1>

<p align="center">
  <strong>Give any AI Agent the power to control a computer with pixel-precise UI interactions via MCP.</strong><br>
  <em>Open-source • Pure MCP Server • Works with any AI Agent • Vision optional</em>
</p>

<p align="center">
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-Native-blue?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IndoaXRlIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTEyIDJMMiA3bDEwIDUgMTAtNS0xMC01eiIvPjxwYXRoIGQ9Ik0yIDE3bDEwIDUgMTAtNSIvPjxwYXRoIGQ9Ik0yIDEybDEwIDUgMTAtNSIvPjwvc3ZnPg==" alt="MCP Native"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="https://github.com/uacc-project/uacc/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/version-1.0.0-orange?style=for-the-badge" alt="Version"></a>
  <a href="https://github.com/uacc-project/uacc/stargazers"><img src="https://img.shields.io/github/stars/uacc-project/uacc?style=for-the-badge&color=yellow" alt="Stars"></a>
  <a href="CONTRIBUTING.md"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=for-the-badge" alt="PRs Welcome"></a>
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-mcp-server">MCP Tools</a> •
  <a href="#-agent-integrations">Agent Integrations</a> •
  <a href="#-architecture">Architecture</a> •
  <a href="#-examples">Examples</a> •
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

---

## 🔥 Pure MCP Server Architecture

UACC is a **pure Model Context Protocol (MCP) server**. It exposes 68 pixel-precise desktop control tools directly to any AI Agent (Claude Code, Hermes, Cursor, OpenCode, OpenClaw, Claude Desktop, etc.).

> **💡 Vision optional:** Text-only AI models can "see" the screen through structured UI accessibility text maps with exact coordinates (`get_screen_info`). Vision-capable models can also capture raw or grid-encoded screenshots (`screenshot`).

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| 🔌 **Pure MCP Server** | Works natively with Claude Code, Hermes, Cursor, OpenCode, OpenClaw, Claude Desktop (68 tools) |
| 🛡️ **Self-Healing Actions** | Auto-retry fallback chain (a11y → OCR → vision) with post-click verification (`smart_click`, `verify_action`) |
| 🎯 **Target-Based Clicking** | Call `click(target="...")` or `smart_click(target="...")` to automatically resolve UI element coordinates |
| 🖼️ **Visual Overlays** | Built-in Set-of-Mark badges (`screenshot(overlay="markers")`) and coordinate grids (`screenshot(overlay="grid")`) |
| ⚙️ **Windows DPI Aware** | Automatic Per-Monitor DPI Awareness initialization locks screen captures and cursor systems to 1:1 physical pixels |
| 🌐 **Browser DOM Bridge** | Chrome DevTools Protocol (CDP) integration for DOM-level CSS selector targeting (`browser_query`, `browser_click`) |
| 🌐 **Cross-Platform** | Native platform drivers for Windows, macOS, and Linux |
| 🤖 **Agent Agnostic** | Connect any MCP-compliant AI agent or custom MCP client |
| 👁️ **Vision Optional** | Structured text map feeds allow text-only models to navigate with exact coordinates |
| 🖱️ **Human Mimicry** | Bézier curve mouse paths, variable typing speeds, natural pauses |
| 💾 **Workflow Memory** | Create, save, inspect, and replay multi-step automations (`create_workflow`, `run_workflow`) |
| 🛡️ **Safe Mode** | Built-in pattern blocking for destructive system commands |
| ⚡ **Zero Heavy Infra** | Pure Python package. Run locally with `pip install -e .` |

---

## 🚀 Quick Start

Get UACC running as an MCP server in under 2 minutes:

```bash
# 1. Clone & install
git clone https://github.com/uacc-project/uacc.git
cd uacc
pip install -e .

# 2. Test CLI MCP launcher (stdio)
uacc-mcp

# Or via python module
python -m uacc
```

---

## 🔌 MCP Tools (68 Native Tools)

When an AI agent connects to UACC, it gets access to standard desktop automation tools:

### Screen Understanding, Grounding & Spatial (10 tools)
- `get_screen_info`: Returns a structured text map of interactive UI elements with exact coordinates.
- `get_screen_info_enhanced`: Deep accessibility tree + OCR + visual bounding box analysis.
- `screenshot`: Capture full screen or region image. Supports `overlay="markers"` (numbered Set-of-Mark badges and legend) or `overlay="grid"` (A1–Z27 coordinate grid).
- `list_monitors`: Enumerate connected displays with dimensions and DPI scales.
- `find_element` / `uacc_where_is`: Locate UI elements and exact coordinates by name/type.
- `find_element_relative` / `find_element_near`: Locate elements relative to labels ("button below Email") or near coordinates.
- `get_mouse_position`: Get current mouse coordinates.
- `wait_for_element`: Poll screen until a specific UI element appears.

### Mouse & Keyboard Control (9 tools)
- `smart_click`: Self-healing click — finds target via accessibility tree, OCR, and VLM with post-click verification.
- `smart_type`: Self-healing typing into input fields by name.
- `click`: Click at precise coordinates `(x, y)` OR by `target="Element Name"` (auto-resolves coordinates).
- `click_element`: Smart target and click an element by visible text/name.
- `type_text`: Type text via simulated human typing.
- `hotkey`: Trigger key combinations (e.g. `['ctrl', 's']`).
- `scroll` / `drag` / `hover`: Advanced mouse movement controls.

### Window Management & App Control (9 tools)
- `get_active_window`: Inspect focused window title, bounds, process.
- `list_windows`: List all open desktop windows.
- `focus_window`: Bring target window to foreground.
- `resize_window`, `move_window`, `minimize_maximize`: Manage window size & state.
- `launch_app` / `open_url`: Launch application by executable name or URL.
- `execute_actions`: Batch multiple sequential keyboard/mouse actions in a single rapid transaction.

### Browser DOM Bridge — CDP (7 tools)
- `browser_query`, `browser_click`, `browser_type`: Query, click, and type into DOM elements via CSS selectors over WebSocket.
- `browser_navigate`, `browser_get_page_info`, `browser_execute_js`, `browser_wait_for`: DOM navigation, page inspection, script execution, and element polling.

### Screen Diff, VLM & Action Verification (8 tools)
- `take_snapshot`, `compare_snapshots`, `get_screen_diff`: Save snapshots, compare pixel/semantic changes, and get visual change overlays.
- `verify_action`: Confirm whether the last action had its intended visual effect.
- `vlm_analyze`, `vlm_locate_element`, `detect_elements_visual`: Use Vision Language Models or OpenCV contours for custom canvas/game UIs.
- `get_action_history`: Retrieve recent action logs and verification results.

### Memory, Knowledge Graph & BAP (7 tools)
- `remember_action`, `query_knowledge`, `recall_related_apps`: Store and query cross-session automation patterns in a semantic graph.
- `memory_summary`, `app_action_history`: Retrieve learned knowledge summaries and app-specific histories.
- `uacc_query`, `uacc_expect`: Blind Agent Protocol (BAP) semantic state querying and expectation assertions.

### System Inspection, Clipboard & Painting (6 tools)
- `get_system_info`, `list_processes`: Inspect CPU, RAM, disk, display metrics, and running system processes.
- `clipboard_read`, `clipboard_write`: Read/write system clipboard text.
- `paint_preset`, `paint_image`, `fetch_image`: Vector drawing, reference image fetching (Pollinations AI for generic art, Web search for characters/monuments), and image sketching in MS Paint.

### Workflow, Planning & Task Management (10 tools)
- `uacc_planner`: **MANDATORY FIRST STEP** — Decomposer & tool selector for any UI task.
- `create_workflow`, `list_workflows`, `get_workflow`, `delete_workflow`, `run_workflow`: Persistent workflow memory stored in `~/.uacc/workflows/`.
- `start_task`, `get_task_status`, `cancel_task`, `list_tasks`: Background async task runner.

### Safety & Override (1 tool)
- `acknowledge_user_override`: Reset kill flag after user confirms resuming automation.

---

## 🔌 Agent Integrations

Detailed configuration guides for AI agents are available in [AGENTS_INTEGRATION.md](AGENTS_INTEGRATION.md).

### Quick Integration Snippets:

#### Claude Code (CLI)
```bash
claude mcp add uacc python -m uacc.mcp
```

#### Hermes Agent
```bash
hermes mcp add uacc -- python -m uacc.mcp
hermes mcp restart
```

#### OpenCode
Add to `opencode.json`:
```json
{
  "mcp": {
    "uacc": {
      "type": "local",
      "command": ["python", "-m", "uacc.mcp"],
      "enabled": true
    }
  }
}
```

#### Cursor / Claude Desktop
Add to your MCP configuration JSON:
```json
{
  "mcpServers": {
    "uacc": {
      "command": "uacc-mcp",
      "args": []
    }
  }
}
```

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────┐
│                  AI Agent / Client                   │
│   Claude Code │ Hermes │ OpenCode │ OpenClaw │ Cursor │
├──────────────────────────────────────────────────────┤
│                     MCP Protocol                     │
│               stdio │ SSE │ Streamable HTTP          │
├──────────────────────────────────────────────────────┤
│                   UACC MCP Server                    │
│   68 Tools │ Screen Resources │ Workflow Memory      │
├──────────────────────────────────────────────────────┤
│                   UACC Core Engine                   │
│   Text Map │ Grid Encoder │ Accessibility Tree       │
│   OCR Engine │ Human Mimicry │ Safe Mode Guard       │
└──────────────────────────────────────────────────────┘
```

---

## 📁 Project Structure

```
uacc/
├── uacc/
├── __main__.py          # python -m uacc entrypoint
│   ├── config.py              # Central configuration
│   ├── mcp.py                 # python -m uacc.mcp forwarding
│   ├── core/                  # Screen capture, accessibility tree, text map, grid
│   ├── actions/               # Human mimicry mouse paths, action executor
│   ├── safety/                # Command safety filtering
│   ├── workflows/             # Persistent JSON workflow memory
│   ├── tasks/                 # Async task runner
│   └── tools/                 # Tool registry & uacc_planner
├── uacc_mcp/                  # FastMCP Server definition & tool handlers
├── examples/                  # Demo & client integration scripts
├── tests/                     # Unit & integration tests
├── AGENTS.md                  # Comprehensive agent rules & workflow docs
├── AGENTS_INTEGRATION.md      # Per-agent setup instructions
└── pyproject.toml
```

---

## 🧪 Testing

Run pytest suite to verify core engines and tool executors:

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

---

## 📄 License

[MIT](LICENSE) — Open source software.
