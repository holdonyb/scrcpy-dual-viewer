[Setup]
AppId={{852A30C8-45DD-4E3E-82BD-76BC262D5E36}
AppName=多屏录制
AppVersion={#AppVersion}
AppPublisher=MultiScreenRecorder
AppPublisherURL=https://github.com/holdonyb/scrcpy-dual-viewer
DefaultDirName={localappdata}\Programs\MultiScreenRecorder
DefaultGroupName=多屏录制
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=MultiScreenRecorder-{#AppVersion}-windows-x64-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\MultiScreenRecorder.exe
Compression=none
SolidCompression=no
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#Binary}"; DestDir: "{app}"; DestName: "MultiScreenRecorder.exe"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\多屏录制"; Filename: "{app}\MultiScreenRecorder.exe"
Name: "{autodesktop}\多屏录制"; Filename: "{app}\MultiScreenRecorder.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MultiScreenRecorder.exe"; Description: "Launch 多屏录制"; Flags: nowait postinstall skipifsilent
