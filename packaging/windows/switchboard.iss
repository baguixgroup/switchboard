; Inno Setup script for Switchboard (per-user install, no admin needed).
; Built by packaging\windows\build.ps1, which passes /DAppVersion=... /DSourceDir=... /DOutputDir=...
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1C2B4E-6A57-4C0B-9A2B-5B1D0C9E7A11}
AppName=Switchboard
AppVersion={#AppVersion}
AppPublisher=Switchboard
DefaultDirName={localappdata}\Programs\Switchboard
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=Switchboard-{#AppVersion}-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Switchboard.exe
UninstallDisplayName=Switchboard
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no
ChangesEnvironment=yes

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Switchboard"; Filename: "{app}\Switchboard.exe"
Name: "{userdesktop}\Switchboard"; Filename: "{app}\Switchboard.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; Flags: unchecked

[Registry]
; `switchboard-cli` in new terminals
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
; Stop an older running copy's tray before first launch of the new one is handled by CloseApplications.
Filename: "{app}\Switchboard.exe"; Description: "Open Switchboard"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\switchboard-cli.exe"; Parameters: "service uninstall --role hub"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveStartAtLogin"

[Code]
function NeedsAddPath(Dir: string): boolean;
var
  Paths: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Paths) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') = 0;
end;
