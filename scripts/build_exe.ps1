<#
.SYNOPSIS
Builds the one-folder Windows app: dist\Comment Analyzer\Comment Analyzer.exe

.DESCRIPTION
Run from the project folder with the .venv active. After building, it starts the built app
once with --smoke-test (main window plus the built-in browser, in a throwaway data folder)
and fails if that doesn't work.

.EXAMPLE
scripts\build_exe.ps1
scripts\build_exe.ps1 -Clean -SkipSmokeTest
#>
param(
    [switch]$Clean,          # rebuild from scratch instead of reusing PyInstaller's cache
    [switch]$SkipSmokeTest,
    [switch]$Installer       # also make dist\Comment-Analyzer-<version>-Setup.exe (Inno Setup 6)
)
$ErrorActionPreference = "Stop"

function Invoke-Native([string]$What, [scriptblock]$Command) {
    # Tools like PyInstaller log to stderr. Under "Stop", Windows PowerShell 5.1 treats
    # redirected stderr as a failure, so judge native tools by their exit code only.
    $ErrorActionPreference = "Continue"
    & $Command
    if ($LASTEXITCODE) { throw "$What failed (exit code $LASTEXITCODE)." }
}

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Invoke-Native "Drawing the icon" { python -m comment_analyzer.ui.icon "build\icon.ico" }

    $pyinstallerArgs = @("--noconfirm", "--distpath", "dist", "--workpath", "build\pyinstaller")
    if ($Clean) { $pyinstallerArgs += "--clean" }
    Invoke-Native "PyInstaller" { python -m PyInstaller @pyinstallerArgs "scripts\comment_analyzer.spec" }

    $exe = Join-Path $root "dist\Comment Analyzer\Comment Analyzer.exe"
    if (-not $SkipSmokeTest) {
        $smokeHome = Join-Path ([IO.Path]::GetTempPath()) "comment-analyzer-smoke"
        Remove-Item -Recurse -Force $smokeHome -ErrorAction SilentlyContinue
        $env:COMMENT_ANALYZER_HOME = $smokeHome
        try {
            $run = Start-Process -FilePath $exe -ArgumentList "--smoke-test" -Wait -PassThru
        } finally {
            Remove-Item Env:COMMENT_ANALYZER_HOME
        }
        if ($run.ExitCode -ne 0) {
            throw "Smoke test failed (exit code $($run.ExitCode)). Log: $smokeHome\logs\app.log"
        }
        Write-Host "Smoke test passed."
    }
    Write-Host "Built $exe"

    if ($Installer) {
        $iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
        if (-not $iscc) {
            $iscc = @(
                "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
                "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
            ) | Where-Object { Test-Path $_ } | Select-Object -First 1
        }
        if (-not $iscc) { throw "Inno Setup 6 isn't installed. Get it with: winget install JRSoftware.InnoSetup" }
        $version = python -c "import comment_analyzer; print(comment_analyzer.__version__)"
        Invoke-Native "Inno Setup" {
            & $iscc /Q "/DAppVersion=$version" "/DSourceDir=$root\dist\Comment Analyzer" `
                "/DOutputDir=$root\dist" "/DIconFile=$root\build\icon.ico" `
                "/DNoticesFile=$root\THIRD-PARTY-NOTICES.md" "scripts\installer.iss"
        }
        Write-Host "Installer: $root\dist\Comment-Analyzer-$version-Setup.exe"
    }
} finally {
    Pop-Location
}
