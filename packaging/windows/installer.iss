#ifndef ReleaseOutput
  #error ReleaseOutput must be provided by build.ps1
#endif
#ifndef ReleaseVersion
  #error ReleaseVersion must be provided by build.ps1
#endif

#define ProductName "Local Drama Studio"
#define ProductPublisher "Local Drama Studio"
#define PackageRoot ReleaseOutput + "\LocalDramaStudio-" + ReleaseVersion + "-windows-amd64"

[Setup]
AppId={{A251613D-71E2-4AAC-A8E8-C79FB631BD57}
AppName={#ProductName}
AppVersion={#ReleaseVersion}
AppPublisher={#ProductPublisher}
DefaultDirName={autopf}\LocalDramaStudio
DefaultGroupName={#ProductName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputDir={#ReleaseOutput}\installer
OutputBaseFilename=LocalDramaStudio-{#ReleaseVersion}-windows-x64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UsePreviousTasks=yes
UninstallDisplayIcon={app}\host\local-drama-host.exe
VersionInfoVersion={#ReleaseVersion}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "service"; Description: "Trusted LAN server (Windows service, browser access, no application login)"; GroupDescription: "Runtime mode:"

[Dirs]
Name: "{commonappdata}\LocalDramaStudio"; Permissions: users-modify; Tasks: not service
Name: "{commonappdata}\LocalDramaStudio\config"
Name: "{commonappdata}\LocalDramaStudio\data"
Name: "{commonappdata}\LocalDramaStudio\projects"
Name: "{commonappdata}\LocalDramaStudio\backups"
Name: "{commonappdata}\LocalDramaStudio\logs"
Name: "{commonappdata}\LocalDramaStudio\runtime"
Name: "{commonappdata}\LocalDramaStudio\models"
Name: "{commonappdata}\LocalDramaStudio\models\downloads"
Name: "{commonappdata}\LocalDramaStudio\models\staging"
Name: "{commonappdata}\LocalDramaStudio\models\quarantine"
Name: "{commonappdata}\LocalDramaStudio\models\libraries\comfyui"
Name: "{commonappdata}\LocalDramaStudio\models\libraries\pytorch"
Name: "{commonappdata}\LocalDramaStudio\models\libraries\gguf"
Name: "{commonappdata}\LocalDramaStudio\models\libraries\audio"

[Files]
Source: "{#PackageRoot}\host\local-drama-host.exe"; DestDir: "{app}\host"; Flags: ignoreversion
Source: "{#PackageRoot}\host\local-drama-launcher.exe"; DestDir: "{app}\host"; Flags: ignoreversion
Source: "{#PackageRoot}\payload\*"; DestDir: "{app}\versions\{#ReleaseVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PackageRoot}\active-release.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "{#SourcePath}\config.desktop.json"; DestDir: "{app}\config-templates"; Flags: ignoreversion
Source: "{#SourcePath}\config.server.json"; DestDir: "{app}\config-templates"; Flags: ignoreversion

[Icons]
Name: "{group}\Local Drama Studio"; Filename: "{app}\host\local-drama-launcher.exe"; WorkingDir: "{app}"; Tasks: not service
Name: "{autodesktop}\Local Drama Studio"; Filename: "{app}\host\local-drama-launcher.exe"; WorkingDir: "{app}"; Tasks: desktopicon and not service
Name: "{group}\Runtime diagnostics"; Filename: "{app}\host\local-drama-host.exe"; Parameters: "doctor"

[Run]
; A first installation needs a config before release maintenance can create its
; database recovery set. Existing installations deliberately skip these two
; bootstrap entries: the candidate release migrates their config first.
Filename: "{app}\host\local-drama-host.exe"; Parameters: "configure-profile --template ""{app}\config-templates\config.desktop.json"""; Description: "Bootstrap desktop profile"; Flags: runhidden waituntilterminated; Tasks: not service; Check: MachineConfigMissing
Filename: "{app}\host\local-drama-host.exe"; Parameters: "configure-profile --template ""{app}\config-templates\config.server.json"""; Description: "Bootstrap trusted LAN server profile"; Flags: runhidden waituntilterminated; Tasks: service; Check: MachineConfigMissing
Filename: "{app}\host\local-drama-host.exe"; Parameters: "upgrade --bundle ""{app}\versions\{#ReleaseVersion}"""; Description: "Verify release and upgrade database"; Flags: runhidden waituntilterminated
; Apply only the profile-owned fields after config migration. User-owned
; settings are merged and preserved by the Host, with a pre-change backup.
Filename: "{app}\host\local-drama-host.exe"; Parameters: "configure-profile --template ""{app}\config-templates\config.desktop.json"""; Description: "Configure desktop profile"; Flags: runhidden waituntilterminated; Tasks: not service
Filename: "{app}\host\local-drama-host.exe"; Parameters: "configure-profile --template ""{app}\config-templates\config.server.json"""; Description: "Configure trusted LAN server profile"; Flags: runhidden waituntilterminated; Tasks: service
Filename: "{app}\host\local-drama-host.exe"; Parameters: "remove-firewall"; Description: "Remove LAN firewall access"; Flags: runhidden waituntilterminated; Tasks: not service
Filename: "{app}\host\local-drama-host.exe"; Parameters: "uninstall-service"; Description: "Remove Windows service for desktop mode"; Flags: runhidden waituntilterminated; Tasks: not service
Filename: "{app}\host\local-drama-host.exe"; Parameters: "configure-firewall"; Description: "Allow the configured service port from the configured trusted subnet"; Flags: runhidden waituntilterminated; Tasks: service
Filename: "{app}\host\local-drama-host.exe"; Parameters: "install-service"; Description: "Install Windows service"; Flags: runhidden waituntilterminated; Tasks: service
Filename: "{sys}\sc.exe"; Parameters: "start LocalDramaStudio"; Description: "Start Windows service"; Flags: runhidden waituntilterminated; Tasks: service
Filename: "{app}\host\local-drama-launcher.exe"; Description: "Start Local Drama Studio"; Flags: nowait postinstall skipifsilent; Tasks: not service

[UninstallRun]
Filename: "{app}\host\local-drama-host.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopRuntime"
Filename: "{app}\host\local-drama-host.exe"; Parameters: "remove-firewall"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "RemoveFirewall"
Filename: "{app}\host\local-drama-host.exe"; Parameters: "uninstall-service"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "RemoveService"

[Code]
function MachineConfigMissing(): Boolean;
begin
  Result := not FileExists(ExpandConstant('{commonappdata}\LocalDramaStudio\config\config.json'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  HostPath: String;
  LockPath: String;
  ResultCode: Integer;
  Attempt: Integer;
begin
  Result := '';
  HostPath := ExpandConstant('{app}\host\local-drama-host.exe');
  LockPath := ExpandConstant('{commonappdata}\LocalDramaStudio\runtime\host.lock');
  if FileExists(HostPath) then
  begin
    Exec(HostPath, 'stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    for Attempt := 1 to 180 do
    begin
      if not FileExists(LockPath) then
        break;
      Sleep(250);
    end;
    if FileExists(LockPath) then
      Result := 'The existing Local Drama Studio runtime did not stop within 45 seconds. Installation was not changed.';
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := MsgBox(
    'Application binaries will be removed. Projects, database, configuration, logs and backups under ProgramData will be preserved.',
    mbInformation, MB_OKCANCEL) = IDOK;
end;
