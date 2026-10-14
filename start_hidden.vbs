' يشغّل خادم axis للمحاسبة في الخلفية بلا نافذة (يُستدعى من مهمة ويندوز المجدولة)
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName) & "\backend"
sh.Run "python -m uvicorn app:app --host 0.0.0.0 --port 8000", 0, False
