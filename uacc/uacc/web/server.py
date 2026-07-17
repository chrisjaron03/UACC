"""
UACC Web UI Server — FastAPI backend for UACC Standalone Agent Dashboard.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(_ROOT))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from uacc.config import config
from uacc.agent.controller import Agent
from uacc.actions.artistic_painter import ArtisticPainter
from uacc.agent.specialists import JobFinder, LongFormResearcher
from uacc.core.screen_capture import capture_full, image_to_base64

logger = logging.getLogger("uacc.web")

app = FastAPI(title="UACC Standalone Agent Dashboard")

# Global Agent State
agent_state = {
    "running": False,
    "task": "",
    "iteration": 0,
    "max_iterations": 30,
    "logs": [],
    "last_result": None,
    "job_report": "",
    "research_report": "",
    "jobs_list": [],
}

agent_lock = threading.Lock()
agent_thread: Optional[threading.Thread] = None


class UILogHandler(logging.Handler):
    """Logging handler that redirects logs to our global state for UI streaming."""
    def emit(self, record):
        log_entry = self.format(record)
        agent_state["logs"].append(log_entry)
        if len(agent_state["logs"]) > 200:
            agent_state["logs"] = agent_state["logs"][-200:]


# Register log handler
formatter = logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s", datefmt="%H:%M:%S")
handler = UILogHandler()
handler.setFormatter(formatter)
logging.getLogger("uacc").addHandler(handler)
logging.getLogger("uacc").setLevel(logging.INFO)


def run_agent_in_background(task: str, mode: str):
    """Background worker that runs the UACC agent."""
    global agent_state
    with agent_lock:
        agent_state["running"] = True
        agent_state["task"] = task
        agent_state["logs"].append(f"[UI] Starting task: '{task}' in {mode} mode")

    try:
        agent = Agent(mode=mode, max_iterations=agent_state["max_iterations"])
        
        # Capture stdout prints by patching prints or just writing logs
        # Run agent
        res = agent.run(task)
        
        with agent_lock:
            agent_state["last_result"] = res
            agent_state["logs"].append(f"[UI] Task finished: {res.get('message', 'Completed')}")
    except Exception as exc:
        logger.exception("Agent thread crashed")
        with agent_lock:
            agent_state["logs"].append(f"[UI] Error executing task: {exc}")
    finally:
        with agent_lock:
            agent_state["running"] = False


# ── API Endpoints ────────────────────────────────────────────

@app.post("/api/run")
async def run_task(req: Request):
    """Start a new agent automation task."""
    global agent_thread
    data = await req.json()
    task = data.get("task", "")
    mode = data.get("mode", "hybrid")

    if not task:
        return JSONResponse({"success": False, "message": "Task cannot be empty"})

    with agent_lock:
        if agent_state["running"]:
            return JSONResponse({"success": False, "message": "Agent is already running a task"})
        
        agent_state["logs"] = [f"[UI] Initializing agent for task: '{task}'"]
        agent_state["last_result"] = None

    agent_thread = threading.Thread(target=run_agent_in_background, args=(task, mode), daemon=True)
    agent_thread.start()

    return {"success": True, "message": "Task started successfully"}


@app.post("/api/stop")
async def stop_task():
    """Stop the running task."""
    # Since python threads are not easily killable, we set flag
    with agent_lock:
        if not agent_state["running"]:
            return JSONResponse({"success": False, "message": "No active task to stop"})
        agent_state["running"] = False
        agent_state["logs"].append("[UI] Abort requested by user.")
    return {"success": True, "message": "Stop request sent to agent"}


@app.get("/api/status")
async def get_status():
    """Get the current agent status, screenshot, and logs."""
    # Capture screen for UI feed
    img_b64 = ""
    try:
        screen = capture_full()
        # Downsample for network speed
        screen.thumbnail((1024, 768))
        img_b64 = image_to_base64(screen, fmt="JPEG", quality=60)
    except Exception as exc:
        img_b64 = ""
        logger.debug("Failed to grab screenshot for UI: %s", exc)

    return {
        "running": agent_state["running"],
        "task": agent_state["task"],
        "logs": agent_state["logs"],
        "screenshot": img_b64,
        "last_result": agent_state["last_result"],
        "job_report": agent_state["job_report"],
        "research_report": agent_state["research_report"],
        "jobs_list": agent_state["jobs_list"],
    }


@app.post("/api/preset")
async def run_preset(req: Request):
    """Draw a preset pattern directly in Paint."""
    data = await req.json()
    preset = data.get("preset", "rose")

    def worker():
        try:
            agent_state["logs"].append(f"[UI] Starting painter preset: '{preset}'")
            # Launch paint first
            from uacc.core.window_manager import launch_application, get_screen_size
            launch_application("mspaint", wait_ms=2000)
            
            screen_w, screen_h = get_screen_size()
            cx, cy = screen_w // 2, screen_h // 2 + 80
            
            painter = ArtisticPainter()
            res = painter.draw_preset(preset, (cx, cy))
            agent_state["logs"].append(f"[UI] Painter result: {res.get('message')}")
        except Exception as exc:
            agent_state["logs"].append(f"[UI] Painter failed: {exc}")

    threading.Thread(target=worker, daemon=True).start()
    return {"success": True, "message": f"Started painting preset '{preset}'"}


@app.post("/api/jobs")
async def run_jobs_search(req: Request):
    """Run a job search automation query."""
    data = await req.json()
    title = data.get("title", "Python Developer")
    location = data.get("location", "New York")
    remote = data.get("remote", True)

    def worker():
        try:
            agent_state["logs"].append(f"[UI] Starting job search: {title} in {location}")
            finder = JobFinder()
            res = finder.run_search(title, location, remote)
            
            with agent_lock:
                agent_state["job_report"] = res.get("report", "")
                agent_state["jobs_list"] = res.get("jobs", [])
                agent_state["logs"].append(f"[UI] Job search complete. Found {len(agent_state['jobs_list'])} jobs.")
        except Exception as exc:
            agent_state["logs"].append(f"[UI] Job search failed: {exc}")

    threading.Thread(target=worker, daemon=True).start()
    return {"success": True, "message": "Job search task launched"}


@app.post("/api/research")
async def run_research_topic(req: Request):
    """Run deep research query."""
    data = await req.json()
    topic = data.get("topic", "Agentic MCP Servers 2026")
    depth = data.get("depth", 3)

    def worker():
        try:
            agent_state["logs"].append(f"[UI] Starting long-form research: '{topic}' (depth {depth})")
            researcher = LongFormResearcher()
            res = researcher.run_research(topic, depth)
            
            with agent_lock:
                agent_state["research_report"] = res.get("report", "")
                agent_state["logs"].append(f"[UI] Deep research complete.")
        except Exception as exc:
            agent_state["logs"].append(f"[UI] Research task failed: {exc}")

    threading.Thread(target=worker, daemon=True).start()
    return {"success": True, "message": "Research task launched"}


@app.post("/api/config")
async def update_config(req: Request):
    """Update config options on the fly."""
    data = await req.json()
    safe_mode = data.get("safe_mode", True)
    human_mimicry = data.get("human_mimicry", True)
    max_iter = data.get("max_iterations", 30)

    config.uacc.safe_mode = safe_mode
    config.uacc.human_mimicry = human_mimicry
    agent_state["max_iterations"] = max_iter

    agent_state["logs"].append(
        f"[UI] Config updated: SafeMode={safe_mode}, Mimicry={human_mimicry}, MaxIter={max_iter}"
    )
    return {"success": True, "message": "Configuration updated successfully"}


# ── Mount static files ───────────────────────────────────────

static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
else:
    @app.get("/")
    def index_fallback():
        return HTMLResponse("<h1>Static directory missing</h1><p>Please build the dashboard frontend</p>")


def main():
    parser = argparse.ArgumentParser(description="UACC Standalone Agent Web UI Launcher")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    args = parser.parse_args()

    print(f"=======================================================")
    print(f"  Starting UACC Standalone Agent Web UI")
    print(f"  Address: http://{args.host}:{args.port}")
    print(f"=======================================================")
    
    # Run uvicorn server
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
