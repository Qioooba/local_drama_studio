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
UninstallDisplayIcon={app}\host\local-drama-host.exe
VersionInfoVersion={#ReleaseVersion}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "service"; Description: "Run as a Windows service (server mode)"; GroupDescription: "Runtime mode:"; Flags: unchecked

[Dirs]
Name: "{commonappdata}\LocalDramaStudio"; Permissions: users-modify; Tasks: not service
Name: "{commonappdata}\LocalDramaStudio\config"
Name: "{commonappdata}\LocalDramaStudio\data"
Name: "{commonappdata}\LocalDramaStudio\projects"
Name: "{commonappdata}\LocalDramaStudio\backups"
Name: "{commonappdata}\LocalDramaStudio\logs"
Name: "{commonappdata}\LocalDramaStudio\runtime"

[Files]
Source: "{#PackageRoot}\host\local-drama-host.exe"; DestDir: "{app}\host"; Flags: ignoreversion
Source: "{#PackageRoot}\host\local-drama-launcher.exe"; DestDir: "{app}\host"; Flags: ignoreversion
Source: "{#PackageRoot}\payload\*"; DestDir: "{app}\versions\{#ReleaseVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PackageRoot}\active-release.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "{#SourcePath}\config.desktop.json"; DestDir: "{commonappdata}\LocalDramaStudio\config"; DestName: "config.json"; Flags: onlyifdoesntexist uninsneveruninstall; Tasks: not service
Source: "{#SourcePath}\config.server.json"; DestDir: "{commonappdata}\LocalDramaStudio\config"; DestName: "config.json"; Flags: onlyifdoesntexist uninsneveruninstall; Tasks: service

[Icons]
Name: "{group}\Local Drama Studio"; Filename: "{app}\host\local-drama-launcher.exe"; WorkingDir: "{app}"; Tasks: not service
Name: "{autodesktop}\Local Drama Studio"; Filename: "{app}\host\local-drama-launcher.exe"; WorkingDir: "{app}"; Tasks: desktopicon and not service
Name: "{group}\Runtime diagnostics"; Filename: "{app}\host\local-drama-host.exe"; Parameters: "doctor"

[Run]
Filename: "{app}\host\local-drama-host.exe"; Parameters: "upgrade --bundle ""{app}\versions\{#ReleaseVersion}"""; Description: "Verify release and upgrade database"; Flags: runhidden waituntilterminated
Filename: "{app}\host\local-drama-host.exe"; Parameters: "install-service"; Description: "Install Windows service"; Flags: runhidden waituntilterminated; Tasks: service
Filename: "{sys}\sc.exe"; Parameters: "start LocalDramaStudio"; Description: "Start Windows service"; Flags: runhidden waituntilterminated; Tasks: service
Filename: "{app}\host\local-drama-launcher.exe"; Description: "Start Local Drama Studio"; Flags: nowait postinstall skipifsilent; Tasks: not service

[UninstallRun]
Filename: "{app}\host\local-drama-host.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopRuntime"
Filename: "{app}\host\local-drama-host.exe"; Parameters: "uninstall-service"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "RemoveService"

[Code]
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
