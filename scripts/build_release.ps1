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
    [switch]$KeepBuildEnv,
    # Code signing (optional). Either a certificate thumbprint in Cert:\CurrentUser\My
    # or a PFX file (password from $env:SIGNING_PFX_PASSWORD). Without either, the
    # release is built unsigned and says so in release-manifest.json.
    [string]$SigningThumbprint = $env:SIGNING_CERT_THUMBPRINT,
    [string]$SigningPfx = $env:SIGNING_PFX_PATH,
    [string]$TimestampServer = "http://timestamp.digicert.com"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
# Intermediate build state stays outside the checkout: a synced folder (OneDrive)
# locks freshly written files and makes PyInstaller fail with "Access is denied".
$work = Join-Path $env:LOCALAPPDATA "11vated-build"
New-Item -ItemType Directory -Force -Path $work | Out-Null
$buildEnv = Join-Path $work "release-venv"
if (Test-Path $buildEnv) { Remove-Item -Recurse -Force $buildEnv }
# "py -3.12" locally; plain "python" (no version switch) on CI runners.
$versionArgs = @(if ($PythonVersion) { "-$PythonVersion" })

Write-Host "== Clean build environment =="
& $Python @versionArgs -m venv $buildEnv
if ($LASTEXITCODE -ne 0) { throw "Could not create the build environment." }
$py = Join-Path $buildEnv "Scripts\python.exe"
& $py -m pip install --quiet --disable-pip-version-check --upgrade pip
& $py -m pip install --quiet --disable-pip-version-check ".[desktop,interaction]"
if ($LASTEXITCODE -ne 0) { throw "Installing the application failed." }

$version = (& $py -c "import ollama_workstation; print(ollama_workstation.__version__)").Trim()
$out = Join-Path $root "dist\release\$version"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force -Path $out | Out-Null

Write-Host "== SBOM and licence audit (shipped environment only) =="
# The tools live in their own environment and inspect the build environment from
# outside, so nothing they depend on is mistaken for a shipped component.
$toolsEnv = Join-Path $work "release-tools"
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
& $py -m PyInstaller --noconfirm --clean --distpath (Join-Path $work "dist") --workpath (Join-Path $work "pyinstaller") packaging\11vated.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
$app = Join-Path $work "dist\11vated-Intelligence"
Copy-Item packaging\uninstall.ps1 $app
Copy-Item (Join-Path $out "THIRD_PARTY_LICENSES.txt") $app

Write-Host "== Code signing =="
$signing = @{ signed = $false; reason = "no signing certificate configured" }
$certificate = $null
if ($SigningThumbprint) {
    $certificate = Get-Item "Cert:\CurrentUser\My\$SigningThumbprint" -ErrorAction Stop
} elseif ($SigningPfx) {
    $password = ConvertTo-SecureString -String $env:SIGNING_PFX_PASSWORD -AsPlainText -Force
    $certificate = Get-PfxData -FilePath $SigningPfx -Password $password |
        Select-Object -ExpandProperty EndEntityCertificates | Select-Object -First 1
}
if ($certificate) {
    $targets = Get-ChildItem $app -Recurse -Include "11vated.exe", "11vated-Intelligence.exe"
    foreach ($target in $targets) {
        $arguments = @{ FilePath = $target.FullName; Certificate = $certificate; HashAlgorithm = "SHA256" }
        if ($TimestampServer) { $arguments.TimestampServer = $TimestampServer }
        $result = Set-AuthenticodeSignature @arguments
        if (-not $result.SignerCertificate) { throw "Signing $($target.Name) failed: $($result.StatusMessage)" }
        Write-Host "Signed $($target.Name): $($result.Status) ($($certificate.Subject))"
    }
    $signing = @{ signed = $true; subject = $certificate.Subject; thumbprint = $certificate.Thumbprint;
                  timestamped = [bool]$TimestampServer;
                  status = (Get-AuthenticodeSignature (Join-Path $app "11vated.exe")).Status.ToString() }
} else {
    Write-Host "Release is UNSIGNED (set SIGNING_CERT_THUMBPRINT or SIGNING_PFX_PATH to sign)."
}

Write-Host "== Smoke tests of the frozen build =="
$smokeHome = Join-Path $work "smoke-home"
if (Test-Path $smokeHome) { Remove-Item -Recurse -Force $smokeHome }
$env:OLLAMA_WORKSTATION_HOME = $smokeHome
$cliVersion = (& (Join-Path $app "11vated.exe") --version) -join ""
if ($LASTEXITCODE -ne 0 -or -not $cliVersion.Contains($version)) { throw "Frozen CLI failed: $cliVersion" }
$doctorJson = (& (Join-Path $app "11vated.exe") doctor) -join "`n"
# doctor exits 2 when Ollama is unreachable, which is expected on a build host
# without Ollama; the build checks only what the frozen app itself controls.
$doctor = $doctorJson | ConvertFrom-Json
if ($doctor.database.integrity -ne "ok") { throw "Frozen doctor reported database integrity: $($doctor.database.integrity)" }
$doctorJson | Out-File -Encoding utf8 (Join-Path $out "smoke-doctor.json")
# Browser and computer use from the frozen build (offline; drives Edge and UI Automation).
$interaction = (& (Join-Path $app "11vated.exe") interaction-selftest) -join "`n"
$interaction | Out-File -Encoding utf8 (Join-Path $out "smoke-interaction.json")
if ($LASTEXITCODE -ne 0) { throw "Frozen interaction self-test failed: $interaction" }
$gui = Start-Process -FilePath (Join-Path $app "11vated-Intelligence.exe") -ArgumentList "--smoke-test" -PassThru -Wait -NoNewWindow -RedirectStandardOutput (Join-Path $out "smoke-gui.log")
if ($gui.ExitCode -ne 0) { throw "Frozen GUI smoke test failed (exit $($gui.ExitCode))." }
Remove-Item Env:OLLAMA_WORKSTATION_HOME
Write-Host "Frozen CLI: $cliVersion; GUI: $(Get-Content (Join-Path $out 'smoke-gui.log'))"

Write-Host "== Package =="
$zip = Join-Path $out "11vated-Intelligence-$version-win64.zip"
# Antivirus briefly locks freshly written executables; retry instead of failing.
for ($attempt = 1; $attempt -le 6; $attempt++) {
    try {
        if (Test-Path $zip) { Remove-Item -Force $zip }
        Compress-Archive -Path $app -DestinationPath $zip -ErrorAction Stop
        break
    } catch {
        if ($attempt -eq 6) { throw }
        Write-Host "Packaging attempt $attempt failed ($($_.Exception.Message)); retrying"
        Start-Sleep -Seconds (5 * $attempt)
    }
}
Copy-Item packaging\install.ps1, packaging\uninstall.ps1 $out
[ordered]@{
    version = $version; built_at = (Get-Date).ToUniversalTime().ToString("o")
    python = (& $py -c "import sys; print(sys.version.split()[0])").Trim()
    signing = $signing; commit = (git rev-parse HEAD 2>$null)
} | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 (Join-Path $out "release-manifest.json")
foreach ($file in Get-ChildItem $out -File | Where-Object { $_.Extension -ne ".sha256" }) {
    $hash = (Get-FileHash -Algorithm SHA256 $file.FullName).Hash.ToLower()
    "$hash  $($file.Name)" | Out-File -Encoding ascii -Append (Join-Path $out "SHA256SUMS.txt")
}
$zipHash = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
"$zipHash  $(Split-Path -Leaf $zip)" | Out-File -Encoding ascii "$zip.sha256"
if (-not $KeepBuildEnv) { Remove-Item -Recurse -Force $buildEnv, $toolsEnv }
Write-Host "Release $version written to $out"
# Every failure above throws; do not let a stale native exit code fail the caller.
exit 0
