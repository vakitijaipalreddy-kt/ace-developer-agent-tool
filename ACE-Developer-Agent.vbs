' Launch the ACE Developer Agent with no console window at all.
' WScript.Shell .Run with intWindowStyle 0 starts pythonw hidden, so not even
' a momentary black box appears - which a .cmd wrapper cannot avoid.
Option Explicit

Dim shell, fso, here, pyw, script
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
script = here & "\run.py"

' Prefer pythonw (no console); fall back to python if it is missing.
pyw = "pythonw.exe"
On Error Resume Next
shell.Run """" & pyw & """ """ & script & """", 0, False
If Err.Number <> 0 Then
    Err.Clear
    shell.Run "python.exe """ & script & """", 0, False
    If Err.Number <> 0 Then
        MsgBox "Could not start Python." & vbCrLf & vbCrLf & _
               "Make sure Python is installed and on your PATH.", _
               vbExclamation, "ACE Developer Agent"
    End If
End If
On Error Goto 0
