@echo off
REM UACC MCP Server — activates local venv then starts the server
set PYTHONPATH=
set VENV_DIR=%~dp0.venv
if exist "%VENV_DIR%\Scripts\activate.bat" (
    call "%VENV_DIR%\Scripts\activate.bat"
    python -m uacc_mcp.server %*
) else (
    echo No .venv found at %VENV_DIR%
    echo Run: python -m venv .venv ^&^& .venv\Scripts\activate ^&^& pip install -e .
    exit /b 1
)
