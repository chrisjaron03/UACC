@echo off
REM UACC MCP Server launcher — uses PATH python or local venv
set PYTHONPATH=
set VENV_DIR=%~dp0.venv
if exist "%VENV_DIR%\Scripts\python.exe" (
    "%VENV_DIR%\Scripts\python.exe" -m uacc_mcp.server %*
) else (
    python -m uacc_mcp.server %*
)
