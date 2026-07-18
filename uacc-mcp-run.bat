@echo off
set PYTHONPATH=
cd /d "C:\Users\chris\Desktop\UACC"
call "C:\Users\chris\Desktop\UACC\.venv\Scripts\activate.bat"
python -m uacc_mcp.server %*
