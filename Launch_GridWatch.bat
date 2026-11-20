@echo off
title GridWatch - Transmission Monitoring
cd /d "%~dp0"
".venv\Scripts\python.exe" -m streamlit run app.py
pause
