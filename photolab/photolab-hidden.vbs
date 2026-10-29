' Starts PhotoLab in the background (no console window). Used by the Windows start-up shortcut.
Set shell = CreateObject("WScript.Shell")
folder = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run """" & folder & "\start-photolab.bat"" --quiet", 0, False
