' Dexter - one-click launcher with no console window.
'
' Runs backend\desktop_app.py under pythonw so nothing but the Dexter window
' appears. Preflight runs inside desktop_app.py and reports failures in a
' dialog. For a console with the full log, use start_dexter.bat instead.
'
' Interpreter: DEXTER_PYTHON (a pythonw.exe path) -> .venv\Scripts\pythonw.exe
' -> pythonw.exe on PATH.
Option Explicit
Dim sh, fso, env, root, py, args, i
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set env = sh.Environment("PROCESS")

root = fso.GetParentFolderName(WScript.ScriptFullName)

py = env("DEXTER_PYTHON")
If py = "" Or Not fso.FileExists(py) Then py = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then
    If fso.FileExists("C:\Python314\pythonw.exe") Then
        py = "C:\Python314\pythonw.exe"
    Else
        py = "pythonw.exe"
    End If
End If

args = ""
For i = 0 To WScript.Arguments.Count - 1
    args = args & " """ & WScript.Arguments(i) & """"
Next

sh.CurrentDirectory = root
' 0 = hidden window, False = do not wait.
sh.Run """" & py & """ -X utf8 """ & root & "\backend\desktop_app.py""" & args, 0, False

