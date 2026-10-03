; Inno Setup. BELUM DIUJI. Prasyarat: jalankan PyInstaller dulu (dist\mcap2mp4).
[Setup]
AppName=MCAP2MP4
AppVersion=0.1.0
DefaultDirName={autopf}\MCAP2MP4
DefaultGroupName=MCAP2MP4
OutputBaseFilename=mcap2mp4-setup
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2

[Files]
Source: "..\dist\mcap2mp4\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\MCAP2MP4"; Filename: "{app}\mcap2mp4.exe"
