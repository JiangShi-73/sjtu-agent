' Xinge SJTU launcher (ASCII only)
Option Explicit

Dim sh, fso, here, electron, py, env

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set env = sh.Environment("PROCESS")

here = fso.GetParentFolderName(WScript.ScriptFullName)
electron = here & "\node_modules\electron\dist\electron.exe"

If Not fso.FileExists(electron) Then
    MsgBox "Electron not found. Run 'npm install' in app/desktop first.", 16, "Xinge SJTU"
    WScript.Quit 1
End If

py = "C:\Users\21072\AppData\Local\Programs\Python\Python312\python.exe"
If Not fso.FileExists(py) Then py = "python"

' Remove (not blank) this var: a blank value may still count as "set"
On Error Resume Next
env.Remove "ELECTRON_RUN_AS_NODE"
Err.Clear
On Error GoTo 0

env("XINGE_PYTHON") = py

sh.CurrentDirectory = here
sh.Run """" & electron & """ """ & here & """", 1, False
