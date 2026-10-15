# Compiles Dexter.exe launcher and Dexter-Setup.exe installer
$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host " Building Dexter Windows Executable & Installer" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan

# 1. Build icon if missing
$IconPath = Join-Path $ScriptDir "assets\icon.ico"
if (-not (Test-Path $IconPath)) {
    Write-Host "`n[1/4] Generating application icon..." -ForegroundColor Yellow
    python (Join-Path $ScriptDir "scripts\generate_icon.py")
} else {
    Write-Host "`n[1/4] Icon asset present: $IconPath" -ForegroundColor Green
}

# 2. Build frontend if needed
$DistIndex = Join-Path $ScriptDir "dist\index.html"
if (-not (Test-Path $DistIndex)) {
    Write-Host "`n[2/4] Building UI bundle (npm run build)..." -ForegroundColor Yellow
    npm run build
} else {
    Write-Host "`n[2/4] UI bundle present: $DistIndex" -ForegroundColor Green
}

# 3. Build launcher Dexter.exe
Write-Host "`n[3/4] Compiling native launcher (Dexter.exe)..." -ForegroundColor Yellow
& (Join-Path $ScriptDir "build_launcher.ps1")

# 4. Build setup installer Dexter-Setup.exe
Write-Host "`n[4/4] Compiling setup installer (Dexter-Setup.exe)..." -ForegroundColor Yellow
$CscPaths = @(
    "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)

$Csc = $CscPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Csc) {
    throw "csc.exe compiler not found."
}

$SetupSource = Join-Path $ScriptDir "installer\DexterSetup.cs"
$SetupOut = Join-Path $ScriptDir "Dexter-Setup.exe"

$CompileArgs = @(
    "/nologo",
    "/target:winexe",
    "/platform:anycpu",
    "/optimize+",
    "/r:System.dll",
    "/r:System.Windows.Forms.dll",
    "/r:System.Drawing.dll",
    "/r:Microsoft.CSharp.dll",
    "/r:System.Core.dll",
    "/win32icon:`"$IconPath`"",
    "/out:`"$SetupOut`"",
    "`"$SetupSource`""
)

& $Csc $CompileArgs
if ($LASTEXITCODE -ne 0) {
    throw "Compilation of Dexter-Setup.exe failed."
}

Write-Host "[OK] Dexter-Setup.exe compiled successfully: $SetupOut" -ForegroundColor Green

# Create Uninstall-Dexter.exe as an alias/uninstaller copy
$UninstallOut = Join-Path $ScriptDir "Uninstall-Dexter.exe"
Copy-Item -Path $SetupOut -Destination $UninstallOut -Force
Write-Host "[OK] Uninstall-Dexter.exe created: $UninstallOut" -ForegroundColor Green

Write-Host "`n===================================================" -ForegroundColor Cyan
Write-Host " Build Finished Successfully!" -ForegroundColor Green
Write-Host "  - Executable:       $ScriptDir\Dexter.exe" -ForegroundColor White
Write-Host "  - Setup Installer:  $ScriptDir\Dexter-Setup.exe" -ForegroundColor White
Write-Host "  - Uninstaller:      $ScriptDir\Uninstall-Dexter.exe" -ForegroundColor White
Write-Host "===================================================" -ForegroundColor Cyan
