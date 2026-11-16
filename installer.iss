; Script Inno Setup : crée MonLauncher-Setup.exe (installateur Windows)
; Il installe le petit démarreur (MonLauncher.exe), qui télécharge ensuite le vrai launcher depuis GitHub.

[Setup]
AppName=Mon Launcher
AppVersion=1.0
AppPublisher=Mon Launcher
DefaultDirName={autopf}\MonLauncher
DefaultGroupName=Mon Launcher
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=MonLauncher-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\MonLauncher.exe
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le bureau"; GroupDescription: "Raccourcis :"

[Files]
Source: "dist\MonLauncher-Windows.exe"; DestDir: "{app}"; DestName: "MonLauncher.exe"; Flags: ignoreversion

[Icons]
Name: "{group}\Mon Launcher"; Filename: "{app}\MonLauncher.exe"
Name: "{autodesktop}\Mon Launcher"; Filename: "{app}\MonLauncher.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MonLauncher.exe"; Description: "Lancer Mon Launcher"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Supprime le launcher téléchargé (jamais le dossier du jeu dans Documents)
Type: filesandordirs; Name: "{localappdata}\MonLauncher"
