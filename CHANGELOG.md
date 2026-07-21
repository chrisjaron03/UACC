# Changelog

All notable changes to UACC will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-07-21

### Added

- **App Adapters** — Specialized adapters for Notepad, browser, and common Windows apps
- **Safety Gates** — Enhanced safety layer with configurable destructive action filtering
- **Agent Controller** — Production-grade Observe → Think → Act → Verify loop with session memory
- **Clipboard Utilities** — Cross-platform clipboard read/write support
- **Window Management** — Windows-specific window services (move, resize, focus, enumerate)
- **CI/CD Pipeline** — GitHub Actions workflows for testing, linting, and publishing
- **AGENTS.md / CLAUDE.md** — Agent configuration files for AI-assisted development

### Changed

- Version bump to 1.0.0 (stable release)
- MCP server now uses `mcp[cli]>=1.0` with proper lifecycle management
- Improved error handling and recovery across all modules
- Enhanced documentation with integration guides

### Fixed

- `pyproject.toml` metadata corrected for PyPI publishing compatibility
- Various stability improvements across screen capture and input automation

## [0.2.0] — 2026-07-18

### Added

- **MCP Server** — Full MCP server with stdio, SSE, and Streamable HTTP transports
  - 9 tools: `screenshot`, `get_screen_info`, `click`, `type_text`, `hotkey`, `scroll`, `drag`, `hover`, `find_element`
  - 2 resources: `uacc://screen/text-map`, `uacc://config`
- **Workflow Memory** — Persistent multi-step automation sequences as reusable workflows
  - `create_workflow`, `list_workflows`, `get_workflow`, `delete_workflow`, `run_workflow`
- **Comprehensive Test Suite** — Full test coverage for MCP server and core infrastructure

## [0.1.0] — 2026-07-12

### 🎉 Initial Release

UACC — Universal AI Computer Control. Let any LLM control a computer with pixel-precise UI interactions.

### Added

- **MCP Server** — Full MCP server with stdio, SSE, and Streamable HTTP transports
  - 9 tools: `screenshot`, `get_screen_info`, `click`, `type_text`, `hotkey`, `scroll`, `drag`, `hover`, `find_element`
  - 2 resources: `uacc://screen/text-map`, `uacc://config`
  - Compatible with Claude Code, Claude Desktop, Hermes, OpenCode, OpenClaw, Cursor

- **Three Adapter Modes**
  - **Text mode** — structured text maps for text-only LLMs (Llama, Mistral, Phi, etc.)
  - **Vision mode** — screenshot + numbered badge overlays for vision LLMs (GPT-4o, Claude, Gemini)
  - **Hybrid mode** — combines both for maximum accuracy

- **Screen Understanding**
  - Fast screenshot capture via `mss`
  - Windows UI Automation accessibility tree parsing
  - EasyOCR-powered text extraction with spatial merging
  - Structured text map generation with coordinate annotations
  - Grid overlay encoding with numbered element badges
  - Screen diff detection for change verification

- **Action System**
  - Click, double-click, right-click with modifier keys
  - Keyboard typing with special character support
  - Hotkey combinations (Ctrl+S, Alt+F4, etc.)
  - Scroll in four directions
  - Drag-and-drop with configurable duration
  - Hover with tooltip triggering

- **Human Mimicry**
  - Bézier curve mouse movement with ease-in-out
  - Variable keystroke delays mimicking human typing
  - Random jitter and "thinking" pauses
  - Speed profiles: slow, normal, fast

- **Safety**
  - Safe mode blocks destructive patterns (delete, format, rm -rf)
  - pyautogui failsafe (move mouse to corner to abort)
  - Action logging and session history

- **Agent Controller**
  - Observe → Think → Act → Verify loop
  - Session memory with element caching
  - Pre/post action verification with automatic coordinate correction
  - Configurable max iterations

- **Examples**
  - `demo_text_map.py` — see what text-only LLMs see
  - `demo_grid.py` — see what vision LLMs see
  - `open_notepad.py` — full agent demo
  - `web_search.py` — web search automation
  - `draw_in_paint.py` — creative drawing demo
