Unicode True
RequestExecutionLevel user
SetCompressor /SOLID lzma
AllowSkipFiles off

!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "${PAYLOAD_MANIFEST}"

!define APP_SHORTCUT "Organizator plików"
!define APP_EXE "FileOrganizer.exe"
!define APP_INSTALL_DIR "$LOCALAPPDATA\Programs\FileOrganizer"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\FileOrganizer"

Name "${APP_NAME}"
OutFile "${SETUP_OUTPUT}"
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=1045 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=1045 "FileDescription" "Instalator programu ${APP_NAME}"
VIAddVersionKey /LANG=1045 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1045 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1045 "LegalCopyright" " "

!ifdef SMOKE_TEST
    InstallDir "$TEMP\FileOrganizer-SmokeTest"
!else
    InstallDir "${APP_INSTALL_DIR}"
!endif

!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Polish"

Function .onInit
    ${IfNot} ${IsNativeAMD64}
        MessageBox MB_OK|MB_ICONSTOP "Ten instalator wymaga systemu Windows w wersji x64." /SD IDOK
        SetErrorLevel 2
        Quit
    ${EndIf}
    ${IfNot} ${AtLeastWin10}
        MessageBox MB_OK|MB_ICONSTOP "Ten instalator wymaga systemu Windows 10 lub nowszego." /SD IDOK
        SetErrorLevel 2
        Quit
    ${EndIf}
    !ifndef SMOKE_TEST
        StrCpy $INSTDIR "${APP_INSTALL_DIR}"
        SetRegView 64
    !endif
FunctionEnd

Section "Organizator plików"
    ClearErrors
    SetOutPath "$INSTDIR"
    File /r "${PAYLOAD_DIR}\*"
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    !ifndef SMOKE_TEST
        CreateShortcut "$SMPROGRAMS\${APP_SHORTCUT}.lnk" "$INSTDIR\${APP_EXE}"
        WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
        WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
        WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
        WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
        WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
        WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
        WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" ${PAYLOAD_KIB}
        WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
        WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
    !endif
    ${If} ${Errors}
        MessageBox MB_OK|MB_ICONSTOP "Instalacja nie została ukończona. Zamknij program i spróbuj ponownie." /SD IDOK
        SetErrorLevel 2
        Abort
    ${EndIf}
SectionEnd

Function un.onInit
    !ifndef SMOKE_TEST
        ${If} $INSTDIR != "${APP_INSTALL_DIR}"
            MessageBox MB_OK|MB_ICONSTOP "Nie można odnaleźć folderu instalacji." /SD IDOK
            SetErrorLevel 2
            Quit
        ${EndIf}
        SetRegView 64
    !endif
FunctionEnd

Section "Uninstall"
    SetOutPath "$TEMP"
    ClearErrors
    !insertmacro DeletePayloadFiles
    ${If} ${Errors}
        MessageBox MB_OK|MB_ICONSTOP "Nie udało się usunąć wszystkich plików programu. Zamknij aplikację i spróbuj ponownie." /SD IDOK
        SetErrorLevel 2
        Abort
    ${EndIf}
    !insertmacro RemovePayloadDirectories
    !ifndef SMOKE_TEST
        Delete "$SMPROGRAMS\${APP_SHORTCUT}.lnk"
        DeleteRegKey HKCU "${UNINSTALL_KEY}"
    !endif
    Delete "$INSTDIR\Uninstall.exe"
    RMDir "$INSTDIR"
SectionEnd
