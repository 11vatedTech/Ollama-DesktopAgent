<#
.SYNOPSIS
  Builds a Windows release from a clean environment with release evidence.

.DESCRIPTION
  1. Creates a fresh build virtual environment (never the developer .venv).
  2. Installs the application with its runtime extras only.
  3. Freezes it with PyInstaller (GUI + CLI in one runtime folder).
  4. Writes a CycloneDX SBOM and a licence audit of the shipped environment;
     a forbidden or unknown licence fails the build.
  5. Smoke-tests the frozen CLI and GUI from the dist folder.
  6. Zips the release, adds install/uninstall scripts, and writes SHA-256 sums.

  Output: dist\release\<version>\
#>
param(
    [string]$Python = "py",
    [string]$PythonVersion = "3.12",
    [switch]$KeepBuildEnv
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$buildEnv = Join-Path $root "build\release-venv"
if (Test-Path $buildEnv) { Remove-Item -Recurse -Force $buildEnv }
# "py -3.12" locally; plain "python" (no version switch) on CI runners.
$versionArgs = @(if ($PythonVersion) { "-$PythonVersion" })

Write-Host "== Clean build environment =="
& $Python @versionArgs -m venv $buildEnv
if ($LASTEXITCODE -ne 0) { throw "Could not create the build environment." }
$py = Join-Path $buildEnv "Scripts\python.exe"
& $py -m pip install --quiet --disable-pip-version-check --upgrade pip
& $py -m pip install --quiet --disable-pip-version-check ".[desktop]"
if ($LASTEXITCODE -ne 0) { throw "Installing the application failed." }

$version = (& $py -c "import ollama_workstation; print(ollama_workstation.__version__)").Trim()
$out = Join-Path $root "dist\release\$version"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force -Path $out | Out-Null

Write-Host "== SBOM and licence audit (shipped environment only) =="
# The tools live in their own environment and inspect the build environment from
# outside, so nothing they depend on is mistaken for a shipped component.
$toolsEnv = Join-Path $root "build\release-tools"
if (Test-Path $toolsEnv) { Remove-Item -Recurse -Force $toolsEnv }
& $Python @versionArgs -m venv $toolsEnv
$tools = Join-Path $toolsEnv "Scripts\python.exe"
& $tools -m pip install --quiet --disable-pip-version-check "pip-licenses>=5" cyclonedx-bom
& $tools -m cyclonedx_py environment --output-reproducible --of JSON -o (Join-Path $out "sbom.cdx.json") $py
if ($LASTEXITCODE -ne 0) { throw "SBOM generation failed." }
& $tools -m piplicenses --python $py --format=json --with-license-file --no-license-path --with-urls |
    Out-File -Encoding utf8 (Join-Path $out "licenses-raw.json")
& $tools packaging\licence_audit.py (Join-Path $out "licenses-raw.json") (Join-Path $out "licence-audit.json") packaging\licence_overrides.json
if ($LASTEXITCODE -ne 0) { throw "Licence audit blocked the release." }
& $tools -m piplicenses --python $py --format=plain-vertical --with-license-file --no-license-path |
    Out-File -Encoding utf8 (Join-Path $out "THIRD_PARTY_LICENSES.txt")

Write-Host "== Freeze =="
& $py -m pip install --quiet --disable-pip-version-check pyinstaller
& $py -m PyInstaller --noconfirm --clean --distpath (Join-Path $root "dist\pyinstaller") --workpath (Join-Path $root "build\pyinstaller") packaging\11vated.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
$app = Join-Path $root "dist\pyinstaller\11vated-Intelligence"
Copy-Item packaging\uninstall.ps1 $app
Copy-Item (Join-Path $out "THIRD_PARTY_LICENSES.txt") $app

Write-Host "== Smoke tests of the frozen build =="
$smokeHome = Join-Path $root "build\smoke-home"
if (Test-Path $smokeHome) { Remove-Item -Recurse -Force $smokeHome }
$env:OLLAMA_WORKSTATION_HOME = $smokeHome
$cliVersion = (& (Join-Path $app "11vated.exe") --version) -join ""
if ($LASTEXITCODE -ne 0 -or -not $cliVersion.Contains($version)) { throw "Frozen CLI failed: $cliVersion" }
& (Join-Path $app "11vated.exe") doctor | Out-File -Encoding utf8 (Join-Path $out "smoke-doctor.json")
$gui = Start-Process -FilePath (Join-Path $app "11vated-Intelligence.exe") -ArgumentList "--smoke-test" -PassThru -Wait -NoNewWindow -RedirectStandardOutput (Join-Path $out "smoke-gui.log")
if ($gui.ExitCode -ne 0) { throw "Frozen GUI smoke test failed (exit $($gui.ExitCode))." }
Remove-Item Env:OLLAMA_WORKSTATION_HOME
Write-Host "Frozen CLI: $cliVersion; GUI: $(Get-Content (Join-Path $out 'smoke-gui.log'))"

Write-Host "== Package =="
$zip = Join-Path $out "11vated-Intelligence-$version-win64.zip"
Compress-Archive -Path $app -DestinationPath $zip
Copy-Item packaging\install.ps1, packaging\uninstall.ps1 $out
foreach ($file in Get-ChildItem $out -File | Where-Object { $_.Extension -ne ".sha256" }) {
    $hash = (Get-FileHash -Algorithm SHA256 $file.FullName).Hash.ToLower()
    "$hash  $($file.Name)" | Out-File -Encoding ascii -Append (Join-Path $out "SHA256SUMS.txt")
}
$zipHash = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
"$zipHash  $(Split-Path -Leaf $zip)" | Out-File -Encoding ascii "$zip.sha256"
if (-not $KeepBuildEnv) { Remove-Item -Recurse -Force $buildEnv, $toolsEnv }
Write-Host "Release $version written to $out"
