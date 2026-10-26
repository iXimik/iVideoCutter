Option Explicit

' Double-click this file from the project root to start the GUI.
' No PowerShell and no console window are required.

Dim fso, shell, projectDir, pythonwPath, mainFile, command
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonwPath = fso.BuildPath(projectDir, ".venv\Scripts\pythonw.exe")
mainFile = fso.BuildPath(projectDir, "main.py")

If Not fso.FileExists(mainFile) Then
    MsgBox "Cannot find main.py in: " & projectDir, vbCritical, "iVideoCutter"
    WScript.Quit 1
End If

If Not fso.FileExists(pythonwPath) Then
    MsgBox "Cannot find the virtual environment Python:" & vbCrLf & pythonwPath & vbCrLf & vbCrLf & "Create .venv and install requirements first.", vbCritical, "iVideoCutter"
    WScript.Quit 1
End If

shell.CurrentDirectory = projectDir
command = Chr(34) & pythonwPath & Chr(34) & " " & Chr(34) & mainFile & Chr(34)
shell.Run command, 1, False
