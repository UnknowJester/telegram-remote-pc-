@echo off
cd /d "%~dp0"
if not exist venv (
  python -m venv venv
)
call venv\Scripts\activate
pip install -r requirements.txt
pyinstaller --noconfirm --onefile --noconsole --name TelecomandoPC bot.py
echo.
echo EXE creato in dist\TelecomandoPC.exe
echo Copia accanto all'exe il file .env se non c'e':
if not exist dist\.env copy .env dist\.env
pause
