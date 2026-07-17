# Contributing to UACC

First off — **thank you!** Whether it's a bug report, a feature idea, or a pull request, every contribution makes UACC better for everyone. 🎉

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Pull Request Process](#pull-request-process)
- [Good First Issues](#good-first-issues)
- [Architecture Overview](#architecture-overview)

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior via a GitHub issue.

## Getting Started

1. **Fork** the repository
2. **Clone** your fork:
   ```bash
   git clone https://github.com/yourusername/uacc.git
   cd uacc
   ```
3. **Create a branch** for your work:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Setup

```bash
# Create a virtual environment
python -m venv .venv
source .venv/bin/activate    # Linux/macOS
.venv\Scripts\activate       # Windows

# Install in development mode with dev dependencies
pip install -e ".[dev]"

# Verify everything works
python -m pytest tests/ -v
```

### Requirements
- Python 3.10+
- Windows (for full functionality — pywinauto + accessibility APIs)
- GPU recommended for EasyOCR (but not required)

## Making Changes

### Code Style

We use **[Ruff](https://github.com/astral-sh/ruff)** for linting and formatting:

```bash
# Check for issues
ruff check .

# Auto-fix what's possible
ruff check --fix .

# Format code
ruff format .
```

**Guidelines:**
- Line length: **100 characters**
- Target: **Python 3.10+**
- Use **type hints** on all public functions
- Write **docstrings** for all public classes and functions
- Keep functions focused — if it does too much, split it

### Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=uacc --cov-report=term-missing

# Run a specific test file
python -m pytest tests/test_text_map.py -v
```

**When adding new features:**
- Add unit tests in `tests/`
- Test file naming: `test_<module_name>.py`
- Aim for meaningful coverage — don't just chase numbers

### Commit Messages

Use clear, descriptive commit messages:

```
feat: add keyboard shortcut composition support
fix: correct Bézier curve endpoint jitter
docs: update MCP server configuration examples
test: add grid encoder edge case tests
refactor: simplify text map element merging
```

Prefixes: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `chore`

## Pull Request Process

1. **Update tests** — ensure your changes have test coverage
2. **Run the full test suite** — `python -m pytest tests/ -v`
3. **Run the linter** — `ruff check .`
4. **Update documentation** if you've changed APIs or added features
5. **Fill out the PR template** with a clear description
6. **Request a review** — we aim to review PRs within 48 hours

### PR Title Format

```
feat: Short description of the change
fix: What was broken and how it's fixed
```

## Good First Issues

Look for issues labeled [`good first issue`](../../labels/good%20first%20issue) — these are specifically curated for newcomers. Great starting points:

- 📝 **Documentation improvements** — typo fixes, better examples, clearer explanations
- 🧪 **Adding test coverage** — write tests for existing modules
- 🐛 **Small bug fixes** — edge cases in text map building, OCR result merging
- 🎨 **Demo scripts** — new example scripts showing UACC capabilities

## Architecture Overview

Understanding the codebase structure will help you contribute effectively:

```
uacc/
├── core/                  ← Screen understanding layer
│   ├── screen_capture.py  ← Fast screenshot capture (mss)
│   ├── accessibility.py   ← Windows UI Automation tree
│   ├── ocr_engine.py      ← Text extraction (EasyOCR)
│   ├── text_map.py        ← Structured screen representation
│   ├── grid_encoder.py    ← Coordinate grid overlays
│   └── screen_diff.py     ← Screen change detection
│
├── actions/               ← Action execution layer
│   ├── schema.py          ← Action type definitions
│   ├── executor.py        ← Real input execution
│   └── human_mimicry.py   ← Natural mouse/typing simulation
│
├── models/                ← LLM adapter layer
│   ├── base_adapter.py    ← Abstract interface
│   ├── text_adapter.py    ← Text-only models
│   ├── vision_adapter.py  ← Vision models
│   └── hybrid_adapter.py  ← Combined text + vision
│
├── agent/                 ← Agent orchestration layer
│   ├── controller.py      ← Observe → Think → Act → Verify loop
│   ├── memory.py          ← Session memory & element cache
│   └── verifier.py        ← Pre/post action verification
│
uacc_mcp/                  ← MCP Server (separate package)
│   ├── server.py          ← MCP tools & resources
│   └── utils.py           ← Image encoding, session state
```

### Key Design Decisions

- **OpenAI-compatible API everywhere** — all LLM calls go through the OpenAI client library, making it trivial to swap models
- **Lazy loading** — EasyOCR and other heavy dependencies are loaded on first use, not at import time
- **Structured text maps** — the core innovation: a compact, coordinate-annotated representation of the screen that any text-only LLM can understand
- **Action schema** — all actions are typed dataclasses with validation, making them safe to serialize and log

---

**Questions?** Open an issue or start a discussion. We're friendly! 🙂
