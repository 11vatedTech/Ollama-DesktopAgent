<#
.SYNOPSIS
  Removes the per-user 11vated Intelligence install. User data is kept unless
  -RemoveUserData is given.
#>
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Programs\11vated-Intelligence"),
    [switch]$RemoveUserData
)
$ErrorActionPreference = "Stop"
$running = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and $_.Path.StartsWith($InstallRoot, [StringComparison]::OrdinalIgnoreCase) }
if ($running) { throw "Close 11vated Intelligence before uninstalling (PID $($running.Id -join ', '))." }

$programs = [Environment]::GetFolderPath("Programs")
$shortcut = Join-Path $programs "11vated Intelligence.lnk"
if (Test-Path $shortcut) { Remove-Item -Force $shortcut }
Remove-Item -Force -ErrorAction SilentlyContinue "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\11vated-Intelligence"

# The script may live inside the folder it deletes; remove from a temp copy's view.
Set-Location $env:TEMP
if (Test-Path $InstallRoot) { Remove-Item -Recurse -Force $InstallRoot }
if ($RemoveUserData) {
    $data = Join-Path $env:LOCALAPPDATA "11vatedTech\11vated-Ollama-Workstation"
    if (Test-Path $data) { Remove-Item -Recurse -Force $data }
    Write-Host "Removed user data at $data"
}
Write-Host "11vated Intelligence uninstalled."
