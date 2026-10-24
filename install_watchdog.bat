@echo off
REM Watchdog con riavvio automatico. Prova l'Operazione Pianificata (serve admin),
REM altrimenti usa Esecuzione automatica (funziona senza admin).
cd /d "%~dp0"
set VBS=%~dp0watchdog.vbs
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup

echo Provo operazione pianificata (richiede admin)...
schtasks /create /tn "TelecomandoPC" /tr "wscript.exe \"%VBS%\"" /sc onlogon /rl limited /f >nul 2>&1
if errorlevel 1 (
  echo Niente admin: uso Esecuzione automatica come fallback...
  powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%STARTUP%\TelecomandoPC-watchdog.lnk'); $s.TargetPath='wscript.exe'; $s.Arguments='\"%VBS%\"'; $s.WorkingDirectory='%~dp0'; $s.Description='Telecomando PC Telegram watchdog'; $s.Save()"
  del "%STARTUP%\TelecomandoPC.lnk" 2>nul
) else (
  echo Operazione pianificata creata.
  del "%STARTUP%\TelecomandoPC-watchdog.lnk" 2>nul
  del "%STARTUP%\TelecomandoPC.lnk" 2>nul
)

echo Avvio watchdog ora...
wscript "%VBS%"
pause
