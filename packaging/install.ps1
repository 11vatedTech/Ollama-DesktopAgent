<#
.SYNOPSIS
  Per-user install or upgrade of 11vated Intelligence from a release folder or zip.

.DESCRIPTION
  No administrator rights are needed. The application is copied to
  %LOCALAPPDATA%\Programs\11vated-Intelligence, a Start Menu shortcut is created,
  and an uninstall entry is registered under HKCU. User data (settings, the
  workstation database, qualifications, memory) lives separately in
  %LOCALAPPDATA%\11vatedTech\11vated-Ollama-Workstation and is never touched by
  install, upgrade or uninstall unless -RemoveUserData is given to uninstall.ps1.

  Upgrade is atomic at the folder level: the new build is staged beside the old
  one, verified, then swapped in; on any failure the previous build is restored.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1 -Source .\11vated-Intelligence-0.2.0-win64.zip
#>
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Programs\11vated-Intelligence"),
    [switch]$NoShortcut
)
$ErrorActionPreference = "Stop"
$appName = "11vated Intelligence"
# The runtime contains nested Qt paths; Windows PowerShell 5.1 extraction fails
# beyond MAX_PATH (260), so refuse an install root that leaves too little room.
if ($InstallRoot.Length -gt 120) {
    throw "Install path is too long ($($InstallRoot.Length) characters; maximum 120): $InstallRoot"
}
$staging = "$InstallRoot.staging"
$backup = "$InstallRoot.previous"

foreach ($path in @($staging)) { if (Test-Path $path) { Remove-Item -Recurse -Force $path } }
New-Item -ItemType Directory -Force -Path $staging | Out-Null

if ((Get-Item $Source).PSIsContainer) {
    Copy-Item -Recurse -Force (Join-Path $Source "*") $staging
} else {
    $checksumFile = "$Source.sha256"
    if (Test-Path $checksumFile) {
        $expected = (Get-Content $checksumFile -Raw).Split(" ")[0].Trim().ToLower()
        $actual = (Get-FileHash -Algorithm SHA256 $Source).Hash.ToLower()
        if ($expected -ne $actual) { throw "Checksum mismatch for $Source" }
        Write-Host "Checksum verified."
    }
    Expand-Archive -Path $Source -DestinationPath $staging -Force
    $inner = Get-ChildItem $staging -Directory | Where-Object { Test-Path (Join-Path $_.FullName "11vated.exe") }
    if ($inner) {
        Get-ChildItem $inner.FullName | Move-Item -Destination $staging
        Remove-Item -Recurse -Force $inner.FullName
    }
}

$gui = Join-Path $staging "11vated-Intelligence.exe"
$cli = Join-Path $staging "11vated.exe"
if (-not (Test-Path $gui) -or -not (Test-Path $cli)) { throw "Release is missing an executable." }
& $cli --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "The staged CLI failed to start (exit $LASTEXITCODE)." }

if (Test-Path $backup) { Remove-Item -Recurse -Force $backup }
$upgraded = $false
if (Test-Path $InstallRoot) { Rename-Item $InstallRoot $backup; $upgraded = $true }
try {
    Rename-Item $staging $InstallRoot
} catch {
    if ($upgraded) { Rename-Item $backup $InstallRoot }
    throw
}

$version = ((& (Join-Path $InstallRoot "11vated.exe") --version 2>$null) -join "").Replace("11vated", "").Trim()
if (-not $NoShortcut) {
    $programs = [Environment]::GetFolderPath("Programs")
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path $programs "$appName.lnk"))
    $link.TargetPath = Join-Path $InstallRoot "11vated-Intelligence.exe"
    $link.WorkingDirectory = $InstallRoot
    $link.Save()
}
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\11vated-Intelligence"
New-Item -Force -Path $key | Out-Null
Set-ItemProperty $key DisplayName $appName
Set-ItemProperty $key DisplayVersion $version
Set-ItemProperty $key Publisher "11vatedTech"
Set-ItemProperty $key InstallLocation $InstallRoot
Set-ItemProperty $key UninstallString ("powershell -ExecutionPolicy Bypass -File `"" + (Join-Path $InstallRoot "uninstall.ps1") + "`"")
Set-ItemProperty $key NoModify 1
Set-ItemProperty $key NoRepair 1
if ($upgraded) { Remove-Item -Recurse -Force $backup }
Write-Host ("{0} {1} {2} at {3}" -f $appName, $version, ($(if ($upgraded) { "upgraded" } else { "installed" })), $InstallRoot)
