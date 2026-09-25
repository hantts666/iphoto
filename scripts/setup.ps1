param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& $Python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required.' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Preparing the edge solver once; the first compilation can take several minutes.'
& '.\.venv\Scripts\python.exe' -c 'import os; os.environ["NUMBA_NUM_THREADS"]="4"; import pymatting'
if ($LASTEXITCODE -ne 0) { throw 'Edge solver initialization failed.' }
Write-Host 'Ready. Double-click start-iphoto.cmd.'

