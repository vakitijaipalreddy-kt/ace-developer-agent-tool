<#
    ACE Developer Agent - installer

    Copies the app to the user's local programs folder, creates Start Menu
    and Desktop shortcuts, and reports which required command-line tools are
    missing.

    Per-user by default, so it needs no administrator rights.

    Usage (from the extracted folder):
        Right-click Install.cmd  ->  Run
    or:
        powershell -ExecutionPolicy Bypass -File Install.ps1
#>

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$AllUsers,          # requires admin; installs to Program Files
    [switch]$NoShortcuts,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$AppName = 'ACE Developer Agent'
$ExeName = 'ACE Developer Agent.exe'

function Say($msg, $color = 'Gray') {
    if (-not $Quiet) { Write-Host $msg -ForegroundColor $color }
}

function Get-InstallRoot {
    if ($AllUsers) {
        return Join-Path $env:ProgramFiles $AppName
    }
    return Join-Path $env:LOCALAPPDATA "Programs\$AppName"
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function New-Shortcut($linkPath, $target, $workDir, $desc) {
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($linkPath)
    $sc.TargetPath = $target
    $sc.WorkingDirectory = $workDir
    $sc.Description = $desc
    $sc.IconLocation = "$target,0"
    $sc.Save()
}

$root = Get-InstallRoot
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$startLink = Join-Path $startMenu "$AppName.lnk"
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) "$AppName.lnk"

# ---------------------------------------------------------------- uninstall
if ($Uninstall) {
    Say "Removing $AppName..." Yellow
    foreach ($lnk in @($startLink, $desktopLink)) {
        if (Test-Path $lnk) { Remove-Item $lnk -Force; Say "  removed shortcut" }
    }
    $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ACEDeveloperAgent"
    if (Test-Path $key) { Remove-Item $key -Recurse -Force }
    if (Test-Path $root) {
        # the app may still be running
        Get-Process -Name ($ExeName -replace '\.exe$','') -ErrorAction SilentlyContinue |
            Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
        Remove-Item $root -Recurse -Force
        Say "  removed $root"
    }
    Say ''
    Say "Uninstalled. Your settings in $env:USERPROFILE\.ace-pr-agent were kept." Green
    Say "Delete that folder too if you want the saved tokens gone."
    if (-not $Quiet) { Read-Host "`nPress Enter to close" }
    return
}

# ---------------------------------------------------------------- install
if ($AllUsers -and -not (Test-Admin)) {
    Write-Host "-AllUsers needs an administrator PowerShell. Re-run elevated, or install per-user (the default)." -ForegroundColor Red
    exit 1
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $here $ExeName
if (-not (Test-Path $source)) {
    Write-Host "Cannot find '$ExeName' next to this script." -ForegroundColor Red
    Write-Host "Extract the whole zip and run Install.cmd from inside it." -ForegroundColor Red
    exit 1
}

Say ''
Say "  $AppName" Cyan
Say "  ========================" Cyan
Say ''
Say "Installing to: $root"

# stop a running copy so the file is not locked
Get-Process -Name ($ExeName -replace '\.exe$','') -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400

New-Item -ItemType Directory -Force -Path $root | Out-Null
Copy-Item $source (Join-Path $root $ExeName) -Force
foreach ($extra in @('README.md', 'LICENSE.txt')) {
    $p = Join-Path $here $extra
    if (Test-Path $p) { Copy-Item $p $root -Force }
}
Say "  copied the application" Green

$exePath = Join-Path $root $ExeName

if (-not $NoShortcuts) {
    New-Shortcut $startLink $exePath $root $AppName
    Say "  Start Menu shortcut created" Green
    New-Shortcut $desktopLink $exePath $root $AppName
    Say "  Desktop shortcut created" Green
}

# Add/Remove Programs entry (per-user)
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ACEDeveloperAgent"
New-Item -Path $key -Force | Out-Null
Set-ItemProperty $key DisplayName   $AppName
Set-ItemProperty $key DisplayVersion '1.0.0'
Set-ItemProperty $key Publisher     'Kroger POS Team'
Set-ItemProperty $key InstallLocation $root
Set-ItemProperty $key DisplayIcon   $exePath
Set-ItemProperty $key NoModify      1
Set-ItemProperty $key NoRepair      1
$uninstallScript = Join-Path $root 'Uninstall.ps1'
Copy-Item (Join-Path $here 'Install.ps1') $uninstallScript -Force
Set-ItemProperty $key UninstallString `
    "powershell -ExecutionPolicy Bypass -File `"$uninstallScript`" -Uninstall"
Say "  registered in Add/Remove Programs" Green

# ---------------------------------------------------------------- deps
Say ''
Say 'Checking the command-line tools this app drives:' Cyan

$tools = @(
    @{ Name = 'git';    Cmd = 'git';    Hint = 'https://git-scm.com/download/win  (or: winget install Git.Git)' },
    @{ Name = 'gh';     Cmd = 'gh';     Hint = 'https://cli.github.com  (or: winget install GitHub.cli)' },
    @{ Name = 'claude'; Cmd = 'claude'; Hint = 'https://claude.com/claude-code' }
)

$missing = @()
foreach ($t in $tools) {
    $found = Get-Command $t.Cmd -ErrorAction SilentlyContinue
    if ($found) {
        Say ("  {0,-8} found   {1}" -f $t.Name, $found.Source) Green
    } else {
        Say ("  {0,-8} MISSING" -f $t.Name) Red
        $missing += $t
    }
}

Say ''
if ($missing.Count -gt 0) {
    Say 'Install these before using the app:' Yellow
    foreach ($m in $missing) { Say ("  {0,-8} {1}" -f $m.Name, $m.Hint) Yellow }
    Say ''
}

Say 'Installed.' Green
Say ''
Say 'Next steps:'
Say "  1. Launch '$AppName' from the Start Menu or Desktop."
Say '  2. Click Settings and fill in your Jira email + API token.'
Say '  3. Point "Repository folder" at your local git checkout.'
Say '  4. Either paste a GitHub token, or use the Terminal tab to sign in.'
Say ''
Say 'Settings are saved per-user in:'
Say "  $env:USERPROFILE\.ace-pr-agent\config.json"
Say ''

if (-not $Quiet) { Read-Host 'Press Enter to close' }
