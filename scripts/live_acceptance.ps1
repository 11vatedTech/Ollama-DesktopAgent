<#
.SYNOPSIS
  Runs the live acceptance campaign against the local Ollama instance.

.DESCRIPTION
  Everything here executes on real installed models. Nothing is simulated.
  Results are written as JSON evidence; a scenario that cannot be exercised
  reports "blocked" or "inconclusive" rather than a pass.

.EXAMPLE
  .\scripts\live_acceptance.ps1 -Models qwen2.5-coder:7b,deepseek-r1:7b
  .\scripts\live_acceptance.ps1 -Models qwen2.5-coder:14b,qwen2.5-coder:7b -AllowCodeExecution
#>
param(
    [Parameter(Mandatory = $true)][string[]]$Models,
    [int]$Trials = 2,
    [switch]$AllowCodeExecution,
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Creating virtual environment (.venv)"
    py -3.11 -m venv .venv
}
& $python -m pip install --quiet --disable-pip-version-check -e ".[dev,mcp,desktop]"
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

$evidence = Join-Path $root ".artifacts\campaign"
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

if (-not $SkipTests) {
    Write-Host "`n== Automated suite =="
    $env:QT_QPA_PLATFORM = "offscreen"
    & $python -m pytest 2>&1 | Tee-Object -FilePath (Join-Path $evidence "pytest-$stamp.log")
    if ($LASTEXITCODE -ne 0) { throw "The automated suite failed; fix it before live acceptance." }
    $env:OLLAMA_LIVE = "1"
    & $python -m pytest tests/test_live_ollama.py 2>&1 |
        Tee-Object -FilePath (Join-Path $evidence "pytest-live-$stamp.log")
    Remove-Item Env:OLLAMA_LIVE
}

Write-Host "`n== Diagnostics =="
& $python -m ollama_workstation.cli doctor | Tee-Object -FilePath (Join-Path $evidence "doctor-$stamp.json")

Write-Host "`n== Resource admission =="
& $python -m ollama_workstation.cli resources @Models |
    Out-File -Encoding utf8 (Join-Path $evidence "resources-$stamp.json")
Get-Content (Join-Path $evidence "resources-$stamp.json") | Select-String '"outcome"'

foreach ($model in $Models) {
    Write-Host "`n== Profile $model =="
    & $python -m ollama_workstation.cli profile $model --repeats 2 |
        Out-File -Encoding utf8 (Join-Path $evidence ("profile-" + ($model -replace '[:/]', '_') + "-$stamp.json"))
    if ($LASTEXITCODE -ne 0) { throw "Profiling $model failed." }
    Write-Host "`n== Qualify $model =="
    & $python -m ollama_workstation.cli qualify $model --trials $Trials 2>&1 |
        Tee-Object -FilePath (Join-Path $evidence ("qualify-" + ($model -replace '[:/]', '_') + "-$stamp.log"))
}

Write-Host "`n== Acceptance scenarios =="
$acceptArgs = @("accept", "--models") + $Models + @("--trials", "$Trials", "--output",
    (Join-Path $evidence "acceptance-$stamp.json"))
if ($AllowCodeExecution) { $acceptArgs += "--allow-code-execution" }
& $python -m ollama_workstation.cli @acceptArgs 2>&1 |
    Tee-Object -FilePath (Join-Path $evidence "acceptance-$stamp.log")
$code = $LASTEXITCODE
Write-Host "`nEvidence folder: $evidence"
exit $code
