# Compiles Dexter.exe native Windows launcher
$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$CscPaths = @(
    "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)

$Csc = $CscPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Csc) {
    throw "Microsoft .NET C# compiler (csc.exe) not found on system."
}

$IconPath = Join-Path $ScriptDir "assets\icon.ico"
$SourcePath = Join-Path $ScriptDir "launcher\DexterLauncher.cs"
$OutExe = Join-Path $ScriptDir "Dexter.exe"

if (-not (Test-Path $IconPath)) {
    Write-Host "Generating icon first..." -ForegroundColor Cyan
    python (Join-Path $ScriptDir "scripts\generate_icon.py")
}

Write-Host "Compiling Dexter.exe with $Csc..." -ForegroundColor Cyan

$CompileArgs = @(
    "/nologo",
    "/target:winexe",
    "/platform:anycpu",
    "/optimize+",
    "/r:System.dll",
    "/r:System.Windows.Forms.dll",
    "/r:System.Drawing.dll",
    "/win32icon:`"$IconPath`"",
    "/out:`"$OutExe`"",
    "`"$SourcePath`""
)

& $Csc $CompileArgs
if ($LASTEXITCODE -ne 0) {
    throw "Compilation of Dexter.exe failed."
}

Write-Host "[OK] Dexter.exe created successfully at: $OutExe" -ForegroundColor Green
