# UACC — AI Agent Instructions

## 🛜 MCP Tools Only — No Python Scripts
First read all the tools thoroughly before execution and mmake this as a skill for yourself.

UACC exposes **70 native MCP tools** (`mcp_uacc_*`) that you can call directly. **Do NOT write separate Python scripts** that wrap or re-implement UACC's functionality. Use the built-in MCP tools:

| What you want | Use this MCP tool |
|---|---|
| See the screen (visual + badges) | `mcp_uacc_screenshot(overlay="markers")` or `mcp_uacc_screenshot(overlay="grid")` |
| See the screen (text / UI tree) | `mcp_uacc_get_screen_info` |
| Click something by name / self-healing | `mcp_uacc_smart_click(target="...")` or `mcp_uacc_click(target="...")` or `mcp_uacc_click_element(name="...")` |
| Click at exact coordinates | `mcp_uacc_click(x, y)` |
| Type text | `mcp_uacc_type_text(text="...")` or `mcp_uacc_smart_type(target="...", text="...")` |
| Keyboard shortcuts | `mcp_uacc_hotkey(keys=["ctrl","s"])` |
| Launch an app | `mcp_uacc_launch_app(name_or_path="...")` |
| Focus a window | `mcp_uacc_focus_window(title="...")` |
| **Fetch an image to draw** | **`mcp_uacc_fetch_line_art(query="...")` ← MUST call before paint_image** |
| Fetch a general image | `mcp_uacc_fetch_image(query="...")` |
| Check cached images | `mcp_uacc_list_fetched_images()` |

## 🎨 Painting with UACC — Complete Agent Guide

UACC paints **inside Microsoft Paint** using an artistic stroke engine. You never click, drag, or draw with raw coordinates — you describe what to paint and UACC does the strokes. **This is mandatory for ALL drawing tasks** in any drawing/design app (Paint, Photoshop, Krita, Figma, Canva, Illustrator). The mandate does NOT apply to standard UI automation (clicking buttons, typing, navigating).

### The 3 painting tools

| Tool | Purpose |
|---|---|
| `fetch_image(query, output_path?, source?)` | Get a reference image **before** painting: `source="auto"` (default) picks Pollinations AI for generic scenes, Web search for named subjects/characters/monuments, direct download for URLs. Returns `image_path` — pass that path to `paint_image`. |
| `paint_image(image_path, max_strokes=500)` | Launch Paint, load the image, extract its **outline contours** (edge detection), and trace them as brush strokes. Uses `max_strokes` (default 500) to cap the drawing. |
| `paint_preset(preset_name)` | Launch Paint and draw a built-in vector design. Valid presets: `"rose"`, `"galaxy"`, `"mountains"`, `"house"`, `"peacock"`. No image needed. |

### Which tool to use — decision tree

```
Does the user want a known built-in design (rose, galaxy, mountains, house, peacock)?
├─ YES → paint_preset(preset_name)
└─ NO → do they want an outline sketch of a specific subject (character, monument, animal, scene)?
     ├─ YES → fetch_image(query) → paint_image(image_path)
     └─ NO / pure imagination → fetch_image("detailed description of the scene", source="pollinations")
            → paint_image(image_path)
```

### The mandatory workflow (5 steps)

1. **`uacc_planner(task_description=...)`** — ALWAYS call first. It computes canvas bounds, stroke caps, and tool sequence. (Planner is mandatory before ANY UACC interaction, drawing included.)
2. **Get the reference image** — `fetch_image(query="...")` for subjects/scenes; skip this step for presets.
3. **Paint** — call `paint_image(image_path=<path from fetch_image>)` or `paint_preset(preset_name="...")`. Both tools launch + maximize Paint automatically, compute the canvas area safely inside the window, and draw with human-like Bézier strokes.
4. **Verify** — `screenshot()` (or `screenshot(overlay="markers")`) to visually confirm the drawing rendered inside the Paint canvas. If it failed or looks wrong, check the tool's JSON `success` field and retry with adjusted parameters.
5. **Report** — tell the user what was painted and where (the Paint window stays open with the finished drawing).

### How `paint_image` works (so you can tune it)

- Loads the image, fits it inside Paint's canvas bounds with a 40px margin (aspect ratio preserved, auto-centered).
- Converts to grayscale → `FIND_EDGES` filter → binary thresholding → traces contiguous edge paths (DFS).
- Strokes follow the traced contours; short noise paths (<4 px) are dropped.
- `max_strokes` caps the total — raise it for detailed subjects (e.g. 1000–1500), lower it for quick simple sketches (faster).
- Works best with images that have **strong, clean outlines**: photos of people/objects, logos, cartoons, line art. Low-contrast or blurry images produce few edges.

### Key behaviors & gotchas

- `fetch_image` returns `{"success": true, "image_path": "C:\\Users\\...\\<name>.png", ...}` — **always extract and reuse that path**; the file is stored under `~/.uacc/images/` by default.
- Both paint tools are self-contained: they launch/focus `mspaint`, maximize it, draw, and leave Paint open. Do NOT pre-launch Paint or reposition windows yourself.
- While a paint operation runs, **don't move the mouse** — the painter uses the cursor; the safety sentinel halts automation if the mouse is pulled away (user override). If that happens, wait for the user to resume, then call `acknowledge_user_override()`.
- If `paint_image` fails to load a path (wrong/missing file), re-run `fetch_image` to get a fresh valid path.
- `paint_preset` with an unknown name returns `success: false` — only the 5 preset names are valid.
- Drawing is **outline/line-art style** — edges are traced, not filled. Manage expectations accordingly (it's a sketch, not a photoreal render).

### What agents must NEVER do when painting

- ❌ Never draw by hand — no `click`/`drag`/`execute_actions` with raw coordinates for strokes.
- ❌ Never guess `paint_image` paths — always go through `fetch_image` first.
- ❌ Never use `screenshot(overlay="grid")` coordinates to aim strokes at the canvas — the painter computes canvas bounds itself.
- ❌ Never write Python scripts to paint — use the MCP tools only.

## 🖼️ Image Fetching — Mandatory Before Drawing

**MANDATORY RULE**: Before calling `paint_image` or `paint_preset` to draw something, you **MUST first obtain a reference image** using the fetch tools. Do NOT skip this step. Do NOT guess file paths. Do NOT call `paint_image` without a valid `image_path`.

### Correct Drawing Workflow

```
Step 1: list_fetched_images()          ← Check if a suitable image is already cached
Step 2: fetch_line_art(query="cat")    ← Download line art if nothing cached matches
Step 3: Check result["success"] == True
Step 4: paint_image(image_path=result["image_path"])   ← Now draw it!
```

### Fetch Tools Reference

| Tool | When to use |
|---|---|
| `fetch_line_art(query="...")` | **BEFORE any drawing task.** Downloads line art optimised for tracing. Returns `image_path` to pass to `paint_image`. Supports styles: `"outline"`, `"coloring_page"`, `"sketch"`, `"silhouette"`, `"cartoon"`, `"realistic"`. |
| `fetch_image(query="...")` | For general images (photos, icons, logos) that keep original colors. NOT optimised for drawing. |
| `list_fetched_images()` | Check the cache before downloading. Each returned `path` is ready for `paint_image`. |

### Common Mistakes to Avoid

- ❌ Calling `paint_image` without an `image_path` → will fail
- ❌ Guessing a file path like `"C:\images\cat.png"` → use `fetch_line_art` to get a real path
- ❌ Skipping `fetch_line_art` and going straight to `paint_image` → no image to draw
- ❌ Not checking `result["success"]` after fetch → the download might have failed
- ✅ Always use `fetch_line_art` first, then pass `result["image_path"]` to `paint_image`

## ⚡ UACC Planner MC (Mandatory Tool Selector)

**MANDATORY FOR ALL AI AGENTS**: Before initiating any UACC interactions (`click`, `type_text`, `paint_image`, `paint_preset`, `execute_actions`, etc.), you **MUST call `uacc_planner` first** to determine the optimal tool sequence, safety parameters, and bounding constraints for your task:

1. **Drawing / Art**: Use `uacc_planner` to set canvas bounds, stroke caps, and tracing strategy before invoking `paint_image` or `paint_preset`.
2. **UI Navigation (PRECISION FIRST)**: Use `uacc_planner` then `get_screen_info` + `click_element` / `smart_click(target="...")` for fast textual targeting over raw vision.
   - **DO NOT guess raw (x, y) coordinates from screenshots**. Plain image coordinates cause off-target clicks (DPI scaling & visual estimation drift).
   - **Self-Healing Clicking**: You can pass `target="Element Name"` directly into `click(target="...")` or `smart_click(target="...")` to let UACC automatically resolve and verify exact screen coordinates.
   - **Custom Canvas UI (Filmora / Video Editors / Games)**: If controls lack accessibility tree entries, call `screenshot(overlay="markers")` to get numbered Set-of-Mark badges, or `screenshot(overlay="grid")` to get an A1–Z27 coordinate grid before clicking.
3. **Batch Actions**: Use `uacc_planner` to group mouse/keyboard events in `execute_actions` for single rapid transactions.
4. **App Launch & Control**: Use `uacc_planner` to schedule `launch_app` followed by `type_text` or `hotkey`.

## Why

- MCP tools work in any agent (Hermes, Claude Code, OpenCode, OpenClaw, Cursor)
- No dependency on the local Python venv
- No script maintenance burden
- The tools handle human-like mouse movement, Bézier curves, timing, and error recovery built-in
- Safe mode is already configured — destructive actions are blocked

## Hermes config

The UACC MCP server is already wired up in Hermes via `hermes mcp add uacc -- python -m uacc.mcp`. If the tools aren't appearing, run `hermes mcp restart` or check `hermes mcp list`.

## 💾 Workflow Memory — Persistent Automation

UACC can **remember** multi-step automation sequences as reusable workflows. Any agent can save, list, inspect, delete, and replay them.

### MCP Tools

| Tool | What it does |
|---|---|
| `create_workflow(name, steps, description?)` | Save a named sequence of tool calls |
| `list_workflows(tag?)` | List all saved workflows, optionally by tag |
| `get_workflow(name)` | Inspect a workflow's full step definitions |
| `delete_workflow(name)` | Remove a workflow |
| `run_workflow(name)` | Execute a workflow step-by-step (replays every tool call) |

### Example — Saving a workflow

```json
{
  "tool": "create_workflow",
  "params": {
    "name": "open_notepad_type_hello",
    "description": "Launch Notepad and type Hello World",
    "tags": ["notepad", "demo"],
    "steps": [
      {"tool": "launch_app", "params": {"name_or_path": "notepad"}},
      {"tool": "wait_for_element", "params": {"name": "Untitled - Notepad"}},
      {"tool": "type_text", "params": {"text": "Hello from UACC workflow!"}}
    ]
  }
}
```

### Example — Running a saved workflow

```json
{"tool": "run_workflow", "params": {"name": "open_notepad_type_hello"}}
```

### Storage

Workflows are stored as JSON files under `~/.uacc/workflows/`. They survive agent restarts and are shared across sessions. You can also edit them manually if needed.

### Tips

- Name workflows descriptively so other agents can discover them
- Use tags like `"office"`, `"browser"`, `"dev"`, `"setup"` for organisation
- Workflows can call any MCP tool (`click`, `type_text`, `hotkey`, `launch_app`, etc.)
- After running, the workflow's `run_count` is incremented (useful to see which workflows are most used)

## 🤖 Self-Healing, VLM & Grounding Tools

UACC features advanced self-healing and visual grounding tools for complex applications (games, video editors, custom canvas UIs):

| Tool | When to use |
|---|---|
| `smart_click(target="...", verify=True)` | Self-healing click with auto-retry across accessibility tree, OCR, and VLM. Automatically captures before/after snapshots to verify screen state changed. |
| `smart_type(target="...", text="...")` | Self-healing input field locator and typing. |
| `screenshot(overlay="markers")` | Captures screen with numbered Set-of-Mark badges overlaid on interactive elements and returns element legend. |
| `screenshot(overlay="grid")` | Captures screen with A1–Z27 coordinate grid overlay. |
| `vlm_locate_element(query="...")` | Uses Vision Language Model to locate bounding box of visual elements or icons that lack text or accessibility labels. |
| `detect_elements_visual(target="...")` | Computer vision contour + OCR detection for custom canvas UIs. |
| `get_screen_info_enhanced()` | Deep UI scan merging accessibility tree, OCR, and bounding box hierarchy. |
| `take_snapshot` / `verify_action` | Take state snapshots and verify whether an action caused visual changes. |

## ⏱️ Background Task Management (Async Operations)

For long-running or repetitive automation tasks (e.g., clicking through 50 dialogs, polling for downloads), UACC provides non-blocking background task tools:

| Tool | What it does |
|---|---|
| `start_task(name, action, params, iterations)` | Launch a background thread repeating an action non-blockingly. Returns `task_id`. |
| `get_task_status(task_id)` | Poll task progress percentage, status (`running`, `completed`, `failed`), and output. |
| `cancel_task(task_id)` | Gracefully terminate a background task. |
| `list_tasks(status_filter?)` | List all running, pending, or completed tasks. |

## 🌐 Browser DOM Bridge (CDP Automation)

For web automation, use direct Chrome DevTools Protocol tools over WebSocket instead of visual clicking:

| Tool | What it does |
|---|---|
| `browser_query(selector, ...)` | Query DOM elements via CSS selectors or XPath. |
| `browser_click(selector)` | Directly click a web DOM element without moving the OS mouse. |
| `browser_type(selector, text)` | Set value/type into a DOM input field. |
| `browser_navigate(url)` | Navigate the active browser tab to a URL. |
| `browser_get_page_info()` | Inspect current page title, URL, and DOM summary. |
| `browser_execute_js(script)` | Execute arbitrary JavaScript in the browser context. |
| `browser_wait_for(selector)` | Poll browser DOM until a CSS selector appears. |

## 🧠 Memory, Knowledge Graph & BAP Tools

UACC builds a semantic graph of cross-session automation patterns and supports Blind Agent Protocol (BAP) assertions:

| Tool | What it does |
|---|---|
| `remember_action` / `query_knowledge` | Store and search cross-session automation patterns and UI behaviors. |
| `recall_related_apps` / `app_action_history` | Query historical actions and learned patterns for a specific application. |
| `memory_summary` | Get an executive summary of all learned knowledge graph nodes. |
| `uacc_query` / `uacc_expect` / `uacc_where_is` | BAP semantic state querying, expectation assertions, and spatial locating. |

## 🖥️ System, Display & Spatial Inspection

| Tool | What it does |
|---|---|
| `list_monitors` / `get_system_info` / `list_processes` | Enumerate connected displays, CPU/RAM/disk metrics, and running OS processes. |
| `find_element_relative` / `find_element_near` | Locate UI elements relative to labels ("button below Email") or near coordinates. |
| `get_mouse_position` / `wait_for_element` | Get exact cursor coordinates or poll screen until an element appears. |

## 📋 Clipboard, Drawing, Batching & Verification

| Tool | What it does |
|---|---|
| `clipboard_read` / `clipboard_write` | Read from or write to the system clipboard. |
| `paint_preset` / `paint_image` | Execute vector drawing presets or trace images onto MS Paint canvases. **Must have `image_path` from `fetch_line_art` first!** |
| `fetch_line_art(query="...")` | **⚠️ MANDATORY before drawing.** Fetch line art from internet, auto-score, auto-crop, convert to B&W. Returns `image_path` for `paint_image`. |
| `fetch_image(query="...")` | Fetch general images (photos, clipart, icons) keeping original colors. |
| `list_fetched_images()` | List cached images. Check this first before fetching — reuse is faster. Each `path` works with `paint_image`. |
| `execute_actions` / `get_action_history` | Batch sequential mouse/keyboard events or inspect recent action logs. |
| `compare_snapshots` / `get_screen_diff` / `vlm_analyze` | Pixel/semantic diffing and general VLM scene analysis. |

## 🛡️ Safety & Override Controls

| Tool | What it does |
|---|---|
| `acknowledge_user_override()` | Acknowledge user resume confirmation and reset mouse pull-away kill flag. |

## ⚙️ Windows Per-Monitor DPI Awareness

UACC automatically initializes Windows Per-Monitor DPI Awareness (`SetProcessDpiAwareness(2)`) at import time. This guarantees that screen captures (`mss`) and mouse coordinate systems (`pyautogui`/Win32) operate in 1:1 physical pixels without scaling drift on high-DPI or scaled displays (125%, 150%, 200%).

## Environment

- UACC root: `C:\Users\chris\Desktop\UACC`
- Venv: `C:\Users\chris\Desktop\UACC\.venv`
