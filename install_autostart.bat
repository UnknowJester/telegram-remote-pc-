@echo off
REM Installa avvio automatico in background + collegamento Desktop
cd /d "%~dp0"
set SCRIPT=%~dp0avvia_nascosto.vbs
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set DESKTOP=%USERPROFILE%\Desktop

echo Creo collegamento in Esecuzione automatica...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%STARTUP%\TelecomandoPC.lnk'); $s.TargetPath='%SCRIPT%'; $s.WorkingDirectory='%~dp0'; $s.Description='Telecomando PC Telegram (background)'; $s.Save()"

echo Creo collegamento sul Desktop...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%DESKTOP%\TelecomandoPC.lnk'); $s.TargetPath='%SCRIPT%'; $s.WorkingDirectory='%~dp0'; $s.Description='Telecomando PC Telegram'; $s.Save()"

echo Fatto. Il bot partira' da solo all'accensione, senza finestra.
echo Per avviarlo ora: doppio clic su TelecomandoPC sul Desktop.
pause
