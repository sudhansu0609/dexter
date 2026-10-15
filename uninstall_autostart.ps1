<#
.SYNOPSIS
    Removes Dexter AI Super Assistant from Windows auto-start.
.DESCRIPTION
    Removes the DexterTray entry from:
    HKCU:\Software\Microsoft\Windows\CurrentVersion\Run
#>

[CmdletBinding()]
param()

$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$ValueName = 'DexterTray'

$existing = (Get-ItemProperty -Path $RunKey -Name $ValueName -ErrorAction SilentlyContinue).$ValueName
if ($existing) {
    Remove-ItemProperty -Path $RunKey -Name $ValueName -ErrorAction SilentlyContinue
    Write-Host "[OK] DexterTray removed from Windows auto-start." -ForegroundColor Green
} else {
    Write-Host "DexterTray was not registered in Windows auto-start." -ForegroundColor Yellow
}
