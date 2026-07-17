@echo off
REM UACC MCP Server launcher for Hermes / stdio-based MCP clients
REM Activates the project venv and runs the MCP server

cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m uacc_mcp.server %*
