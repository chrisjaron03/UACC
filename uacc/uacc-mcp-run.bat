@echo off
REM UACC MCP Server launcher for Hermes / stdio-based MCP clients
REM Activates the project venv and runs the MCP server

cd /d "C:\Users\chris\Desktop\New folder\uacc"
call .venv\Scripts\activate.bat
python -m uacc_mcp.server %*
