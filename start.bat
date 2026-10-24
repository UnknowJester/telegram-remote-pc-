@echo off
cd /d "%~dp0"
if not exist .env (
  copy .env.example .env
  echo Ho creato il file .env: aprilo e incolla il token, poi rilancia start.bat
  pause
  exit /b
)
if not exist venv (
  python -m venv venv
)
call venv\Scripts\activate
pip install -r requirements.txt
python bot.py
pause
