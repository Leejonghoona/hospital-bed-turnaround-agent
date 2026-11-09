@echo off
chcp 65001 >nul
cd /d "%~dp0.."
python scripts\fetch_yt_transcripts.py
pause
