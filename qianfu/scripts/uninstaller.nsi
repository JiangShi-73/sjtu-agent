; 信鸽 SJTU —— 一键卸载
;
; 双击就跑，不是安装程序。做两件事：
;   1. 停掉正在跑的进程（信鸽后端 / AstrBot / Electron 窗口）
;   2. 删干净：安装目录、快捷方式、注册表、数据目录
;
; 编译：
;   makensis /DAPP_VERSION=0.1.0 scripts\uninstaller.nsi

Unicode true
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"

!define APP_NAME "信鸽 SJTU"
!define APP_ID "com.sjtu.xinge"
!ifndef APP_VERSION
  !define APP_VERSION "0.1.0"
!endif

Name "卸载 ${APP_NAME}"
OutFile "..\build\信鸽SJTU-卸载-${APP_VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\xinge-desktop"
RequestExecutionLevel user
ShowInstDetails show
XPStyle on
AllowRootDirInstall true

Var DeleteData
Var DataCheckbox
Var InstallDirFound

Page custom pgConfirmCreate pgConfirmLeave
Page instfiles

Function .onInit
  StrCpy $DeleteData "0"
  StrCpy $InstallDirFound "0"

  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "InstallLocation"
  ${If} $0 != ""
    StrCpy $INSTDIR $0
    StrCpy $InstallDirFound "1"
  ${ElseIf} ${FileExists} "$LOCALAPPDATA\Programs\xinge-desktop\信鸽 SJTU.exe"
    StrCpy $InstallDirFound "1"
  ${EndIf}
FunctionEnd

Function pgConfirmCreate
  !insertmacro MUI_HEADER_TEXT "卸载 ${APP_NAME}" "确认要清理的内容"

  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 32u "将从这台电脑上移除「${APP_NAME}」。卸载后可以随时重新安装。"
  Pop $0

  ${NSD_CreateLabel} 0 40u 100% 20u "安装目录："
  Pop $0

  ${If} $InstallDirFound == "1"
    ${NSD_CreateLabel} 0 56u 100% 20u "$INSTDIR"
  ${Else}
    ${NSD_CreateLabel} 0 56u 100% 20u "（没找到安装版，会跳过）"
  ${EndIf}
  Pop $0

  ${NSD_CreateCheckbox} 0 90u 100% 24u "同时删除我的数据（登录状态、公告、日程、模型 Key）"
  Pop $DataCheckbox
  ${NSD_Check} $DataCheckbox

  ${NSD_CreateLabel} 0 118u 100% 34u "数据在 %USERPROFILE%\.xinge$\r$\n取消勾选可以保留，重装后不用重新登录。"
  Pop $0

  nsDialogs::Show
FunctionEnd

Function pgConfirmLeave
  ${NSD_GetState} $DataCheckbox $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $DeleteData "1"
  ${Else}
    StrCpy $DeleteData "0"
  ${EndIf}
FunctionEnd

Section "清理"
  SetOutPath "$TEMP"

  DetailPrint "停止正在运行的进程..."
  nsExec::Exec 'taskkill /F /IM "信鸽 SJTU.exe"'
  Pop $0
  nsExec::Exec 'taskkill /F /IM electron.exe'
  Pop $0
  nsExec::Exec 'taskkill /F /IM wscript.exe'
  Pop $0
  Sleep 800

  ; 用 WMI 的 CommandLine 过滤，避免在 NSIS 里写 PowerShell 的 $_ 变量
  FileOpen $9 "$TEMP\xinge-kill.ps1" w
  FileWrite $9 "Get-CimInstance Win32_Process -Filter $\"Name='python.exe' AND CommandLine LIKE '%app.server%'$\" | Invoke-CimMethod -MethodName Terminate | Out-Null$\r$\n"
  FileWrite $9 "Get-CimInstance Win32_Process -Filter $\"Name='python.exe' AND CommandLine LIKE '%astrbot%'$\" | Invoke-CimMethod -MethodName Terminate | Out-Null$\r$\n"
  FileClose $9
  nsExec::Exec 'powershell -NoProfile -ExecutionPolicy Bypass -File "$TEMP\xinge-kill.ps1"'
  Pop $0
  Delete "$TEMP\xinge-kill.ps1"
  Sleep 1200

  DetailPrint "删除快捷方式..."
  Delete "$DESKTOP\信鸽 SJTU.lnk"
  Delete "$DESKTOP\信鸽 SJTU（命令行）.lnk"
  Delete "$SMPROGRAMS\信鸽 SJTU.lnk"
  Delete "$SMPROGRAMS\信鸽 SJTU\信鸽 SJTU.lnk"
  RMDir "$SMPROGRAMS\信鸽 SJTU"

  DetailPrint "删除注册表项..."
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}"
  DeleteRegKey HKCU "Software\${APP_ID}"
  DeleteRegKey HKCU "Software\Classes\${APP_ID}"

  DetailPrint "删除安装目录..."
  RMDir /r "$INSTDIR"

  DetailPrint "清理残留..."
  RMDir /r "$LOCALAPPDATA\xinge-desktop-updater"
  Delete "$TEMP\xinge-boot.log"

  ${If} $DeleteData == "1"
    DetailPrint "删除数据目录..."
    RMDir /r "$PROFILE\.xinge"
  ${Else}
    DetailPrint "保留数据目录：$PROFILE\.xinge"
  ${EndIf}

  DetailPrint "完成。"
SectionEnd