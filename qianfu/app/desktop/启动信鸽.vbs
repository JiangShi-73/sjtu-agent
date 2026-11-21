' Xinge SJTU launcher -- ASCII only (VBScript reads this file as ANSI)
Option Explicit

Dim sh, fso, here, electron, py, env, i, cands

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set env = sh.Environment("PROCESS")

here = fso.GetParentFolderName(WScript.ScriptFullName)
electron = here & "\node_modules\electron\dist\electron.exe"

If Not fso.FileExists(electron) Then
    MsgBox "Electron not found. Run 'npm install' inside app\desktop first.", 16, "Xinge SJTU"
    WScript.Quit 1
End If

' Find Python: XINGE_PYTHON first, then common install paths, then PATH.
py = env("XINGE_PYTHON")

If py = "" Or Not fso.FileExists(py) Then
    py = ""
    cands = Array( _
        sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\python.exe"), _
        sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python313\python.exe"), _
        sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python311\python.exe"), _
        "C:\Python312\python.exe", _
        "C:\Python311\python.exe" _
    )
    For i = 0 To UBound(cands)
        If fso.FileExists(cands(i)) Then
            py = cands(i)
            Exit For
        End If
    Next
End If

If py = "" Then py = "python"

' Remove (not blank) this var: a blank value may still count as "set"
On Error Resume Next
env.Remove "ELECTRON_RUN_AS_NODE"
Err.Clear
On Error GoTo 0

env("XINGE_PYTHON") = py

sh.CurrentDirectory = here
sh.Run """" & electron & """ """ & here & """", 1, False
