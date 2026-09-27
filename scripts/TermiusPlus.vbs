' Double-click to open TermiusPlus in its own window. Closing the window stops the server.
Option Explicit
Dim fso, shell, root, command
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
shell.CurrentDirectory = root
command = "pyw -3 """ & root & "\platforms\windows\launcher.py"""
On Error Resume Next
shell.Run command, 0, False
If Err.Number <> 0 Then
  Err.Clear
  command = "pythonw """ & root & "\platforms\windows\launcher.py"""
  shell.Run command, 0, False
End If
If Err.Number <> 0 Then
  MsgBox "未找到 Python。请安装 Python 3.9 或更高版本（勾选 py launcher），也可以在项目目录运行 python app.py。", vbCritical, "TermiusPlus"
End If
