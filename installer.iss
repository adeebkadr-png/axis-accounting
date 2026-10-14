; ملف تثبيت «AXIS للمحاسبة» لويندوز — يُبنى بـ Inno Setup 6:  ISCC installer.iss
; يثبَّت للمستخدم الحالي بلا صلاحيات مدير، والبيانات تبقى في %LOCALAPPDATA%\SmartAccounting عند التحديث أو الإزالة.
; يعمل على ويندوز 10 و 11 (64 بت). إن نقص الجهاز محرك Edge WebView2 يثبّته تلقائياً (يلزمه إنترنت)،
; وإن تعذّر ذلك يفتح البرنامج نفسه في المتصفح بدل النافذة المستقلة.

#define AppName "AXIS للمحاسبة"
#define AppVersion "1.0.1"
#define AppExe "SmartAccounting.exe"
#define WV2Key "Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

[Setup]
AppId={{6E0C5B1A-7D3F-4B8E-9A21-5A1C0A0C0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=AXIS
DefaultDirName={localappdata}\Programs\AXIS Accounting
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=release
OutputBaseFilename=AXIS-Accounting-Setup-{#AppVersion}
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
CloseApplications=yes
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "desktopicon"; Description: "إنشاء اختصار على سطح المكتب"; GroupDescription: "اختصارات:"

[Files]
Source: "dist\SmartAccounting\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2

[InstallDelete]
; حذف ملفات الإصدار السابق حتى لا تختلط مكتبات قديمة بالجديدة (البيانات في مجلد آخر ولا تُمس)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\إزالة {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "تثبيت محرك العرض Microsoft Edge WebView2…"; Flags: waituntilterminated; Check: NeedsWebView2
Filename: "{app}\{#AppExe}"; Description: "تشغيل {#AppName} الآن"; Flags: nowait postinstall skipifsilent

[Code]
function HasWV2(Root: Integer; Key: String): Boolean;
var
  V: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', V) and (V <> '') and (V <> '0.0.0.0');
end;

function NeedsWebView2: Boolean;
begin
  Result := not (HasWV2(HKLM, 'SOFTWARE\WOW6432Node\{#WV2Key}') or HasWV2(HKLM, 'SOFTWARE\{#WV2Key}')
                 or HasWV2(HKCU, 'Software\{#WV2Key}'));
end;
