Set objWS = CreateObject("WScript.Shell")
objWS.Run "cmd /c cd /d """ & CreateObject("WScript.Shell").CurrentDirectory & """ & START.bat", 0, False
