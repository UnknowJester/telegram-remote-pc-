' Watchdog TelecomandoPC: avvia il bot e lo riavvia se crasha/chiude.
' Eseguito dall'Operazione Pianificata "TelecomandoPC" al login.
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
ScriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

Dim exePath, venvPy, cmd
exePath = ScriptDir & "\dist\TelecomandoPC.exe"
venvPy = ScriptDir & "\venv\Scripts\pythonw.exe"

If fso.FileExists(exePath) Then
  cmd = Chr(34) & exePath & Chr(34)
ElseIf fso.FileExists(venvPy) Then
  cmd = Chr(34) & venvPy & Chr(34) & " " & Chr(34) & ScriptDir & "\bot.py" & Chr(34)
Else
  cmd = "pythonw """ & ScriptDir & "\bot.py"""
End If

Do
  ' True = aspetta che il bot termini; se crasha, dopo 10s riparte
  WshShell.Run cmd, 0, True
  WScript.Sleep 10000
Loop
