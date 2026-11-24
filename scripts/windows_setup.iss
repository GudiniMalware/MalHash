[Setup]
AppId={code:ApplicationId}
AppName=MalHash
AppVersion={#AppVersion}
AppPublisher=MalHash contributors
VersionInfoVersion={#AppVersion}
VersionInfoDescription=MalHash desktop installer
DefaultDirName={localappdata}\Programs\MalHash
PrivilegesRequired=lowest
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#if Ver >= EncodeVer(7, 0, 0)
SetupArchitecture=x64
#endif
SetupIconFile=..\assets\malhash.ico
UninstallDisplayIcon={app}\MalHash.exe
OutputBaseFilename=MalHash-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableDirPage=yes
DisableProgramGroupPage=yes
ShowLanguageDialog=no
UsePreviousLanguage=no
AllowNoIcons=yes
CreateUninstallRegKey=not IsBuildTest

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
Source: "{#GuiDir}\malhash-gui-windows-x64.exe"; DestDir: "{app}"; DestName: "MalHash.exe"; Flags: ignoreversion
Source: "{#GuiDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "compiler:license.txt"; DestDir: "{app}\_internal\notices\licenses"; DestName: "InnoSetup.txt"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\MalHash"; Filename: "{app}\MalHash.exe"; WorkingDir: "{app}"; Check: not IsBuildTest

[Run]
Filename: "{app}\MalHash.exe"; Description: "{cm:LaunchProgram,MalHash}"; Flags: nowait postinstall skipifsilent

[Code]
function IsBuildTest: Boolean;
begin
  Result := ExpandConstant('{param:MALHASHBUILDTEST|0}') = '1';
end;

function ApplicationId(Param: String): String;
begin
  Result := 'org.malhash.desktop';
  if IsBuildTest then
    Result := 'org.malhash.desktop.buildtest';
end;
