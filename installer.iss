; ملف تثبيت «AXIS للمحاسبة» لويندوز — يُبنى بـ Inno Setup 6:  ISCC installer.iss
; يثبَّت للمستخدم الحالي بلا صلاحيات مدير، والبيانات تبقى في %LOCALAPPDATA%\SmartAccounting عند التحديث أو الإزالة.

#define AppName "AXIS للمحاسبة"
#define AppVersion "1.0.0"
#define AppExe "SmartAccounting.exe"

[Setup]
AppId={{6E0C5B1A-7D3F-4B8E-9A21-5A1C0A0C0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=AXIS
DefaultDirName={localappdata}\Programs\AXIS Accounting
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=release
OutputBaseFilename=AXIS-Accounting-Setup-{#AppVersion}
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "desktopicon"; Description: "إنشاء اختصار على سطح المكتب"; GroupDescription: "اختصارات:"

[Files]
Source: "dist\SmartAccounting\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\إزالة {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "تشغيل {#AppName} الآن"; Flags: nowait postinstall skipifsilent
