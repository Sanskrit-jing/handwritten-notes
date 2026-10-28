; 手写记录 - Inno Setup 安装包脚本
; 前置：先运行 pyinstaller 产出 dist\HandwrittenNotes\
; 产物：releases\HandwrittenNotes_Setup_1.0.0.exe

#define MyAppName "手写记录"
#define MyAppNameEn "HandwrittenNotes"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Sanskrit-jing"

[Setup]
AppId={{D2A1C9E0-5B4F-4E6A-9C7D-1F2E3A4B5C6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
OutputDir=releases
OutputBaseFilename={#MyAppNameEn}_Setup_{#MyAppVersion}
SetupIconFile=ico\icon.ico
UninstallDisplayIcon={app}\{#MyAppNameEn}.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务："

[Files]
Source: "dist\{#MyAppNameEn}\*"; Excludes: "config.json"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppNameEn}.exe"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppNameEn}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppNameEn}.exe"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent
