$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot "venv\Scripts\python.exe"

Set-Location $projectRoot

if (-not (Test-Path $venvPython)) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        $python = Get-Command py -ErrorAction SilentlyContinue
    }

    if (-not $python) {
        throw "Python is not installed or not available on PATH. Install Python 3.11+ first."
    }

    Write-Host "Creating virtual environment..."
    if ($python.Name -eq "py.exe") {
        & py -3 -m venv venv
    } else {
        & python -m venv venv
    }
}

Write-Host "Installing dependencies..."
& $venvPython -m pip install -r requirements.txt

Write-Host "Starting FlightApp at http://127.0.0.1:5000"
& $venvPython app.py
