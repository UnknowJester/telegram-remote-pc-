Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
ScriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

Dim exePath, venvPy, cmd
exePath = ScriptDir & "\dist\TelecomandoPC.exe"
venvPy = ScriptDir & "\venv\Scripts\pythonw.exe"

If fso.FileExists(exePath) Then
  WshShell.Run Chr(34) & exePath & Chr(34), 0, False
ElseIf fso.FileExists(venvPy) Then
  cmd = Chr(34) & venvPy & Chr(34) & " " & Chr(34) & ScriptDir & "\bot.py" & Chr(34)
  WshShell.Run cmd, 0, False
Else
  cmd = "pythonw """ & ScriptDir & "\bot.py"""
  WshShell.Run cmd, 0, False
End If
