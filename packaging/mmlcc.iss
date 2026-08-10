; MML Cloud Courier installer. Compile via packaging/build_release.ps1:
;   ISCC /DAppVersion=<v> /DDistDir=<repo>\dist\mml-cloud-courier /O<repo>\dist packaging\mmlcc.iss
; Service policy (spec): FIRST install registers (LocalSystem, auto-start);
; upgrades stop/replace/start WITHOUT re-registering so a configured
; log-on account survives; ImagePath mismatch triggers the one
; re-registration path ('update' — account preserved per Task 4 research).
; Uninstall removes the service + files but LEAVES the data dir.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DistDir
  #define DistDir "..\dist\mml-cloud-courier"
#endif

[Setup]
AppId={{9E7C1A76-52D4-4B7E-A870-1C3F2A6D9B58}}
AppName=MML Cloud Courier
AppVersion={#AppVersion}
AppPublisher=NOAA Fisheries Marine Mammal Laboratory
DefaultDirName={autopf}\MML Cloud Courier
DisableProgramGroupPage=yes
OutputBaseFilename=mml-cloud-courier-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\mmlcc-gui.exe
ChangesEnvironment=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; Flags: unchecked
Name: "addtopath"; Description: "Add the install folder to PATH (for the mmlcc command line)"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\MML Cloud Courier"; Filename: "{app}\mmlcc-gui.exe"
Name: "{autodesktop}\MML Cloud Courier"; Filename: "{app}\mmlcc-gui.exe"; Tasks: desktopicon

[Registry]
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; \
  ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
  Tasks: addtopath; Check: NeedsAddPath('{app}')

[Code]
const
  ServiceName = 'MMLCloudCourier';
  EnvKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';

var
  NeededRegistration: Boolean;

function NeedsAddPath(Param: String): Boolean;
var
  OrigPath, Dir: String;
begin
  { Check: clauses cannot call ExpandConstant; expand the parameter here. }
  Dir := ExpandConstant(Param);
  if not RegQueryStringValue(HKLM, EnvKey, 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result :=
    Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;

procedure RemoveFromPath(Dir: String);
var
  OrigPath, NewPath: String;
begin
  if not RegQueryStringValue(HKLM, EnvKey, 'Path', OrigPath) then
    exit;
  NewPath := OrigPath;
  StringChangeEx(NewPath, ';' + Dir, '', True);
  StringChangeEx(NewPath, Dir + ';', '', True);
  StringChangeEx(NewPath, Dir, '', True);
  if NewPath <> OrigPath then
    RegWriteExpandStringValue(HKLM, EnvKey, 'Path', NewPath);
end;

function ServiceExists(): Boolean;
var
  R: Integer;
begin
  Exec(ExpandConstant('{sys}\sc.exe'), 'query ' + ServiceName, '',
    SW_HIDE, ewWaitUntilTerminated, R);
  Result := (R = 0);
end;

function PackagedImagePath(): String;
begin
  Result := ExpandConstant('{app}\mmlcc-service.exe');
end;

function ImagePathIsCurrent(): Boolean;
var
  S: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM,
      'SYSTEM\CurrentControlSet\Services\' + ServiceName, 'ImagePath', S)
  then
    Result := CompareText(RemoveQuotes(Trim(S)), PackagedImagePath()) = 0;
end;

procedure StopService();
var
  R, I: Integer;
begin
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop ' + ServiceName, '',
    SW_HIDE, ewWaitUntilTerminated, R);
  for I := 1 to 30 do
  begin
    Exec(ExpandConstant('{cmd}'),
      '/c sc query ' + ServiceName + ' | find "STOPPED"', '',
      SW_HIDE, ewWaitUntilTerminated, R);
    if R = 0 then
      exit;
    Sleep(1000);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if ServiceExists() then
    StopService();
end;

function LastCsvField(Line: String): String;
var
  I: Integer;
begin
  Result := Line;
  for I := Length(Line) downto 1 do
    if Line[I] = ',' then
    begin
      Result := Copy(Line, I + 1, MaxInt);
      break;
    end;
  Result := RemoveQuotes(Trim(Result));
end;

function InstallingUserSid(): String;
var
  Tmp: String;
  R: Integer;
  Lines: TArrayOfString;
begin
  { By process token, never account name (6e45d4a): whoami emits the SID
    directly, no name mapping anywhere. Elevated, this is the elevating
    user's SID — docs say to run setup as the user who runs the GUI. }
  Result := '';
  Tmp := ExpandConstant('{tmp}\whoami-sid.txt');
  if not Exec(ExpandConstant('{cmd}'),
      '/c whoami /user /fo csv > "' + Tmp + '"', '',
      SW_HIDE, ewWaitUntilTerminated, R) then
    exit;
  if not LoadStringsFromFile(Tmp, Lines) then
    exit;
  if GetArrayLength(Lines) < 2 then
    exit;
  Result := LastCsvField(Lines[GetArrayLength(Lines) - 1]);
  if Pos('S-1-', Uppercase(Result)) <> 1 then
    Result := '';
end;

function DataDir(): String;
begin
  Result := ExpandConstant('{commonappdata}\MML Cloud Courier');
end;

procedure EnsureGuiUserSid(Sid: String);
var
  Path: String;
  Content: AnsiString;
  R: Integer;
begin
  if Sid = '' then
    exit;
  ForceDirectories(DataDir());
  Path := DataDir() + '\gui-users.sids';
  if not FileExists(Path) then
    SaveStringToFile(Path,
      '# SIDs granted read on api_token, one per line' + #13#10 +
      Sid + #13#10, False)
  else
  begin
    LoadStringFromFile(Path, Content);
    if Pos(Sid, Content) = 0 then
      SaveStringToFile(Path, Sid + #13#10, True);
  end;
  { Immediate grant on an existing token; regenerated tokens get it from
    the service via gui-users.sids. }
  if FileExists(DataDir() + '\api_token') then
    Exec(ExpandConstant('{sys}\icacls.exe'),
      '"' + DataDir() + '\api_token" /grant *' + Sid + ':(R)', '',
      SW_HIDE, ewWaitUntilTerminated, R);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  R: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    NeededRegistration := False;
    if not ServiceExists() then
    begin
      { First install: registers LocalSystem + auto-start + failure
        actions (the exe's install verb does all three). }
      Exec(PackagedImagePath(), 'install', '', SW_HIDE,
        ewWaitUntilTerminated, R);
      NeededRegistration := (R = 0);
      if R <> 0 then
        MsgBox('The MML Cloud Courier service failed to register '
          + '(exit code ' + IntToStr(R) + ').' + #13#10 + #13#10
          + 'From an elevated command prompt, run:' + #13#10
          + '"' + PackagedImagePath() + '" install', mbError, MB_OK);
    end
    else if not ImagePathIsCurrent() then
    begin
      { The one re-registration path: repoint ImagePath, account
        preserved (Task 4 research finding). }
      Exec(PackagedImagePath(), 'update', '', SW_HIDE,
        ewWaitUntilTerminated, R);
      NeededRegistration := (R = 0);
      if R <> 0 then
        MsgBox('The MML Cloud Courier service failed to update its '
          + 'registration (exit code ' + IntToStr(R) + ').' + #13#10 + #13#10
          + 'From an elevated command prompt, run:' + #13#10
          + '"' + PackagedImagePath() + '" update', mbError, MB_OK);
    end;
    { Upgrade contract: no re-registration, no ACL changes when the
      service already exists with a current ImagePath (NeededRegistration
      stays False from initialization above); only run when a
      registration was actually attempted AND succeeded. }
    if NeededRegistration then
      EnsureGuiUserSid(InstallingUserSid());
    Exec(ExpandConstant('{sys}\sc.exe'), 'start ' + ServiceName, '',
      SW_HIDE, ewWaitUntilTerminated, R);
  end;
  if (CurStep = ssDone) and NeededRegistration then
    MsgBox('The MML Cloud Courier service was registered.' + #13#10 + #13#10
      + 'Fresh registrations run as LocalSystem. If this machine uses a '
      + 'named service account (for example for user ADC credentials), '
      + 'open services.msc -> "MML Cloud Courier Service" -> Log On and '
      + 'set it now. Setting it there also grants the "log on as a '
      + 'service" right automatically.', mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  R: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    StopService();
    Exec(ExpandConstant('{sys}\sc.exe'), 'delete ' + ServiceName, '',
      SW_HIDE, ewWaitUntilTerminated, R);
  end;
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}'));
end;
