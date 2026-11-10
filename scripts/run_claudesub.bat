@echo off
REM 구독 경유 Claude 측정: 최신 코드 받기 - 측정 - 결과 저장 - 커밋 - 푸시
chcp 65001 >nul
cd /d "%~dp0.."
set ANTHROPIC_API_KEY=
python -m pip install -q -e ".[claudesub]"
python scripts\run_claudesub.py %*
pause
