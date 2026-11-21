' Xinge SJTU launcher (ASCII only)
Option Explicit

Dim sh, fso, here, electron, py, env, i, cands

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set env = sh.Environment("PROCESS")

here = fso.GetParentFolderName(WScript.ScriptFullName)
electron = here & "\node_modules\electron\dist\electron.exe"

If Not fso.FileExists(electron) Then
    MsgBox "Electron 没装。先在 app\desktop 里跑 npm install。", 16, "信鸽 SJTU"
    WScript.Quit 1
End If

' 找 Python：先看环境变量，再看 PATH，最后猜常见位置
py = env("XINGE_PYTHON")

If py = "" Or Not fso.FileExists(py) Then
    py = ""
    cands = Array( _
        sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\python.exe"), _
        sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python311\python.exe"), _
        sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python313\python.exe"), _
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

' 都不在就用 PATH 里的 python
If py = "" Then py = "python"

' Remove (not blank) this var: a blank value may still count as "set"
On Error Resume Next
env.Remove "ELECTRON_RUN_AS_NODE"
Err.Clear
On Error GoTo 0

env("XINGE_PYTHON") = py

sh.CurrentDirectory = here
sh.Run """" & electron & """ """ & here & """", 1, False
