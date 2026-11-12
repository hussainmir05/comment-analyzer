; Inno Setup script for Comment Analyzer. "scripts\build_exe.ps1 -Installer" compiles it.
; AppId is "Comment Analyzer", the previous version's (implicit) AppId, so this installer
; upgrades the old app in place. Installs per user: no admin rights needed.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Comment Analyzer"

[Setup]
AppId={#AppName}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=Comment-Analyzer-{#AppVersion}-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppName}.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"; Flags: unchecked

[InstallDelete]
; the previous version's files, so no stale libraries are left next to the new ones
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "{#NoticesFile}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Registry]
; "Start with Windows" (set from the app's Settings) is removed with the app
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "CommentAnalyzer"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox('Also delete your Comment Analyzer data: collected comments, settings, the downloaded model and the saved API key?',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      DelTree(ExpandConstant('{localappdata}\CommentAnalyzer'), True, True, True);
      Exec('cmdkey', '/delete:CommentAnalyzer:youtube_api_key', '', SW_HIDE, ewWaitUntilTerminated, Code);
      Exec('cmdkey', '/delete:CommentAnalyzer:author_hash_key', '', SW_HIDE, ewWaitUntilTerminated, Code);
    end;
end;
