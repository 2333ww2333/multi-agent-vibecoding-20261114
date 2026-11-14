# One-shot acceptance check. Run from anywhere:
#   .\scripts\acceptance.ps1
# Uses the project venv when present. `doctor` performs one real model call (~16 tokens).
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
$py = if (Test-Path $venvPy) { $venvPy } else { "python" }
Write-Host "python: $py"

$fail = 0

function Step($name, [scriptblock]$body) {
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    try { & $body } catch { Write-Host "[FAIL] $name : $_" -ForegroundColor Red; $script:fail++ }
}

Step "python >= 3.11" {
    $v = & $py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ([version]$v -lt [version]"3.11") { throw "python 3.11+ required" }
    Write-Host "OK $v"
}

Step "import agent" { & $py -c "import agent, agent.runner; print('OK', agent.__version__)" }

Step "ruff (optional)" {
    if (Get-Command ruff -ErrorAction SilentlyContinue) { ruff check agent/ } else { Write-Host "SKIP ruff not installed" }
}

Step "pytest" {
    & $py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
}

Step "doctor" {
    & $py -m agent.runner doctor
    if ($LASTEXITCODE -ne 0) { throw "doctor is not all green" }
}

Step "offline smoke" {
    & $py -m pytest tests/test_e2e_smoke.py -q
    if ($LASTEXITCODE -ne 0) { throw "smoke failed" }
}

Write-Host "`n================================" -ForegroundColor Yellow
if ($fail -eq 0) { Write-Host "PASS" -ForegroundColor Green; exit 0 }
Write-Host "FAILED: $fail step(s)" -ForegroundColor Red; exit 1
