; Inno Setup script: turns dist\RRShortsBuilder into one Rebels-Revolt-Shorts-Setup.exe
; Built automatically by build_installer.bat

#define AppName "Rebels Revolt Shorts"
#define AppVersion "2.9.3"
#define AppPublisher "EagleEye Codes"

[Setup]
AppId={{3F8A1C52-9B7E-4D21-A6C4-5E0B7D9F2A13}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppVerName={#AppName} {#AppVersion}
; per-user install: no admin prompt, and Windows' protected-folder rules never block it
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\RRShortsBuilder
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=Rebels-Revolt-Shorts-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\RRShortsBuilder.exe
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\RRShortsBuilder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\RRShortsBuilder.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\RRShortsBuilder.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RRShortsBuilder.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
