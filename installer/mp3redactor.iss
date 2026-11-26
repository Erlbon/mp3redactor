#if 0
; The Windows installer of The ɯP3 Redactor (Inno Setup 6): the one-folder build (dist\mp3redactor\, built with
; MP3REDACTOR_BUILD=folder; it starts faster than the one-file exe, which unpacks itself at every start) into the
; user's own programs folder, with a Start menu entry. The one-file dist\mp3redactor.exe stays the portable download.
; Like the release exe, it does not bundle the external tools (mp3val.exe, keyfinder-cli.exe): put them in
; {app}\tools, or on PATH (README.md).
;
; Build:   build_exe.bat (it builds both programs and passes /DUpdateContract), or by hand:
;          iscc /DAppBuild=2026-11-26#02 /DUpdateContract=<python -m redactor_common.release contract-path> installer\mp3redactor.iss
; Output:  dist\mp3redactor-setup.exe
;
; Per user, no administrator rights: %LOCALAPPDATA%\Programs\The MP3 Redactor. Updating goes through the family's
; update framework (redactor_common docs/update-framework.md): its installer contract, included at the END of this
; file, writes {app}\update-channel = windows-installer, so Help > Check for Updates runs the new
; mp3redactor-setup.exe the release's update-manifest.json names, silently with /relaunch=yes, and the contract
; starts the program again. The settings, crash log and logs sit beside the exe (the Redactor apps' convention):
; an update leaves them, and so does the uninstall.
;
; These notes sit in "#if 0", as in the shared contract.
#endif

#ifndef AppBuild
  #define AppBuild "0000-00-00#00"
#endif
#ifndef UpdateContract
  #error Pass /DUpdateContract (build_exe.bat does: python -m redactor_common.release contract-path)
#endif

; For the installer contract included at the end. The program has no --quit: Windows' close request ends it.
#define UpdateExe "mp3redactor.exe"

[Setup]
AppId={{D57E4002-ABA9-4F32-B4A8-C81DC24A5BBE}
AppName=The ɯP3 Redactor
AppVersion={#AppBuild}
AppVerName=The ɯP3 Redactor {#AppBuild}
AppPublisher=Erlbon
AppPublisherURL=https://github.com/Erlbon/mp3redactor
AppSupportURL=https://github.com/Erlbon/mp3redactor/issues
AppUpdatesURL=https://github.com/Erlbon/mp3redactor/releases
VersionInfoVersion=0.0.0.0
VersionInfoProductTextVersion={#AppBuild}
PrivilegesRequired=lowest
DefaultDirName={autopf}\The MP3 Redactor
DefaultGroupName=The ɯP3 Redactor
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\mp3redactor.exe
UninstallDisplayName=The ɯP3 Redactor
SetupIconFile=..\assets\icon.ico
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=mp3redactor-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "A shortcut on the desktop"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\mp3redactor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\The ɯP3 Redactor"; Filename: "{app}\mp3redactor.exe"
Name: "{autodesktop}\The ɯP3 Redactor"; Filename: "{app}\mp3redactor.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\mp3redactor.exe"; Description: "Start The ɯP3 Redactor"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; what Help > Check for Updates may leave beside the exe: the old exe after a swap, and a download that did not finish
Type: files; Name: "{app}\mp3redactor.exe.old"
Type: files; Name: "{app}\mp3redactor.exe.new"
Type: files; Name: "{app}\mp3redactor.exe.new.part"

#include UpdateContract
