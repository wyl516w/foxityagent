$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    python -m venv --without-pip .venv
}

Write-Host "Installing dependencies into .venv..."
python -m pip --python $venvPython install --upgrade pip
python -m pip --python $venvPython install -e ".[dev]"

Write-Host "Setup complete."

