; Installer MCAP2MP4 (NSIS 3, Unicode). Build dari akar proyek, setelah `pyinstaller build/mcap2mp4.spec`:
;   makensis -DVERSION=0.2.0 build/installer.nsi        -> dist/installer/MCAP2MP4-Setup-<versi>.exe
; Pemasangan senyap:  MCAP2MP4-Setup-x.y.z.exe /S [/D=C:\Folder\Tujuan]
Unicode true
!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!define APP "MCAP2MP4"
!define UNKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"

!include "MUI2.nsh"
!include "x64.nsh"
!include "FileFunc.nsh"

Name "${APP} ${VERSION}"
OutFile "..\dist\installer\${APP}-Setup-${VERSION}.exe"
InstallDir "$PROGRAMFILES64\${APP}"
InstallDirRegKey HKLM "${UNKEY}" "InstallLocation"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
BrandingText "${APP} ${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP}"
VIAddVersionKey "FileDescription" "${APP} installer"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "Internal tool"

!define MUI_ICON "..\assets\icon.ico"
!define MUI_UNICON "..\assets\icon.ico"
!define MUI_ABORTWARNING
!define MUI_LANGDLL_ALLLANGUAGES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP}.exe"
!define MUI_FINISHPAGE_RUN_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "Indonesian"
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"

LangString SEC_MAIN    ${LANG_INDONESIAN}  "Program MCAP2MP4 (wajib)"
LangString SEC_MAIN    ${LANG_ENGLISH}     "MCAP2MP4 program (required)"
LangString SEC_MAIN    ${LANG_SIMPCHINESE} "MCAP2MP4 程序（必需）"
LangString SEC_START   ${LANG_INDONESIAN}  "Pintasan Menu Start"
LangString SEC_START   ${LANG_ENGLISH}     "Start Menu shortcuts"
LangString SEC_START   ${LANG_SIMPCHINESE} "开始菜单快捷方式"
LangString SEC_DESK    ${LANG_INDONESIAN}  "Pintasan Desktop"
LangString SEC_DESK    ${LANG_ENGLISH}     "Desktop shortcut"
LangString SEC_DESK    ${LANG_SIMPCHINESE} "桌面快捷方式"
LangString LNK_CLI     ${LANG_INDONESIAN}  "MCAP2MP4 (Baris Perintah)"
LangString LNK_CLI     ${LANG_ENGLISH}     "MCAP2MP4 (Command Line)"
LangString LNK_CLI     ${LANG_SIMPCHINESE} "MCAP2MP4（命令行）"
LangString LNK_UN      ${LANG_INDONESIAN}  "Hapus MCAP2MP4"
LangString LNK_UN      ${LANG_ENGLISH}     "Uninstall MCAP2MP4"
LangString LNK_UN      ${LANG_SIMPCHINESE} "卸载 MCAP2MP4"
LangString NEED64      ${LANG_INDONESIAN}  "MCAP2MP4 membutuhkan Windows 64-bit."
LangString NEED64      ${LANG_ENGLISH}     "MCAP2MP4 requires 64-bit Windows."
LangString NEED64      ${LANG_SIMPCHINESE} "MCAP2MP4 需要 64 位 Windows。"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "$(NEED64)"
    Abort
  ${EndIf}
  SetRegView 64
  !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Function un.onInit
  SetRegView 64
  !insertmacro MUI_UNGETLANGUAGE
FunctionEnd

Section "$(SEC_MAIN)" SecMain
  SectionIn RO
  SetRegView 64
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\_internal"                 ; bersihkan sisa versi lama (hanya folder milik aplikasi)
  File /r "..\dist\MCAP2MP4\*.*"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "${UNKEY}" "DisplayName" "${APP}"
  WriteRegStr HKLM "${UNKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNKEY}" "Publisher" "MCAP2MP4"
  WriteRegStr HKLM "${UNKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNKEY}" "DisplayIcon" "$INSTDIR\${APP}.exe"
  WriteRegStr HKLM "${UNKEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKLM "${UNKEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegDWORD HKLM "${UNKEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNKEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKLM "${UNKEY}" "EstimatedSize" "$0"
SectionEnd

Section "$(SEC_START)" SecStart
  SetShellVarContext all
  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortcut "$SMPROGRAMS\${APP}\${APP}.lnk" "$INSTDIR\${APP}.exe" "" "$INSTDIR\${APP}.exe" 0
  CreateShortcut "$SMPROGRAMS\${APP}\$(LNK_CLI).lnk" "$SYSDIR\cmd.exe" '/k cd /d "$INSTDIR" && mcap2mp4-cli.exe --help' "$INSTDIR\mcap2mp4-cli.exe" 0
  CreateShortcut "$SMPROGRAMS\${APP}\$(LNK_UN).lnk" "$INSTDIR\uninstall.exe"
SectionEnd

Section /o "$(SEC_DESK)" SecDesk
  SetShellVarContext all
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\${APP}.exe" "" "$INSTDIR\${APP}.exe" 0
SectionEnd

Section "Uninstall"
  SetRegView 64
  SetShellVarContext all
  Delete "$DESKTOP\${APP}.lnk"
  RMDir /r "$SMPROGRAMS\${APP}"
  RMDir /r "$INSTDIR\_internal"                 ; hanya berkas milik aplikasi; folder tujuan lain tidak disentuh
  Delete "$INSTDIR\${APP}.exe"
  Delete "$INSTDIR\mcap2mp4-cli.exe"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"                              ; hanya terhapus bila kosong
  DeleteRegKey HKLM "${UNKEY}"
  ; pengaturan pengguna di %APPDATA%\MCAP2MP4 sengaja dipertahankan
SectionEnd
