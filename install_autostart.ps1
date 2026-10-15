<#
.SYNOPSIS
    Installs Dexter AI Super Assistant to auto-start at Windows logon and run in the system tray.
.DESCRIPTION
    Mirrors the Sentinel installer architecture by adding DexterTray to the Current User Run registry key:
    HKCU:\Software\Microsoft\Windows\CurrentVersion\Run

    When Windows logs on, Dexter starts silently in the background (--tray mode) via Dexter.vbs,
    presenting an icon in the Windows notification area without intrusive popups.
#>

[CmdletBinding()]
param(
    [switch]$StartNow
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VbsPath = Join-Path $ScriptDir 'Dexter.vbs'
$WscriptExe = Join-Path $env:SystemRoot 'System32\wscript.exe'

if (-not (Test-Path $VbsPath)) {
    throw "Dexter launcher not found at: $VbsPath"
}

$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$ValueName = 'DexterTray'
$Command = '"{0}" "{1}" --tray' -f $WscriptExe, $VbsPath

Write-Host "Registering Dexter auto-start at Windows logon..." -ForegroundColor Cyan
New-ItemProperty -Path $RunKey -Name $ValueName -Value $Command -PropertyType String -Force | Out-Null

$registered = (Get-ItemProperty -Path $RunKey -Name $ValueName -ErrorAction SilentlyContinue).$ValueName
if ($registered -eq $Command) {
    Write-Host "[OK] DexterTray registered successfully in $RunKey" -ForegroundColor Green
    Write-Host "     Command: $Command" -ForegroundColor DarkGray
} else {
    throw "Registry verification failed. Expected '$Command' but got '$registered'"
}

if ($StartNow) {
    Write-Host "Starting Dexter in system tray mode..." -ForegroundColor Cyan
    Start-Process -FilePath $WscriptExe -ArgumentList "`"$VbsPath`" --tray"
    Write-Host "[OK] Dexter launched in system tray." -ForegroundColor Green
} else {
    Write-Host "Dexter is now configured to auto-start on every Windows login." -ForegroundColor Green
    Write-Host "To launch it immediately in tray mode, run: .\install_autostart.ps1 -StartNow" -ForegroundColor Yellow
}
