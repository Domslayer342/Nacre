; Inno Setup script: per-user install (no admin), Start menu entry, default-browser registration, uninstaller.
; Built by .github/workflows/windows.yml:  iscc /DVersion=1.2.3 packaging\nacre.iss
#ifndef Version
  #define Version "0.0.0"
#endif

[Setup]
AppId={{6B1E7F3A-5C2D-4E8B-9A71-3D0F2C9E4B15}
AppName=Nacre
AppVersion={#Version}
AppPublisher=Nacre
AppPublisherURL=https://github.com/Domslayer342/Nacre
DefaultDirName={localappdata}\Programs\Nacre
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Nacre-Setup-{#Version}
SetupIconFile=..\assets\nacre.ico
UninstallDisplayIcon={app}\Nacre.exe
UninstallDisplayName=Nacre
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\Nacre\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Nacre"; Filename: "{app}\Nacre.exe"
Name: "{userdesktop}\Nacre"; Filename: "{app}\Nacre.exe"; Tasks: desktopicon

[Registry]
; written by `Nacre.exe --install-desktop` below; listed here so the uninstaller removes them
Root: HKCU; Subkey: "Software\Classes\NacreHTML"; Flags: uninsdeletekey dontcreatekey
Root: HKCU; Subkey: "Software\Clients\StartMenuInternet\Nacre"; Flags: uninsdeletekey dontcreatekey
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "Nacre"; \
  ValueData: "Software\Clients\StartMenuInternet\Nacre\Capabilities"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Nacre.exe"; Parameters: "--install-desktop"; Flags: runhidden waituntilterminated; \
  StatusMsg: "Registering Nacre as a browser..."
Filename: "{app}\Nacre.exe"; Description: "Launch Nacre"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{userprograms}\Nacre Apps\*.lnk"
Type: dirifempty; Name: "{userprograms}\Nacre Apps"
