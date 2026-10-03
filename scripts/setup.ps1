param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$taskRunPath = Join-Path (Get-Location).Path 'run.py'
$taskAppProcesses = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -and
    $_.CommandLine.IndexOf($taskRunPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
}
if ($taskAppProcesses) { throw 'Close iPhoto before updating its dependencies.' }
& $Python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required.' }
# CPU and DirectML wheels share the onnxruntime import path. Remove both
# before an upgrade so one package's uninstaller cannot erase the other's DLLs.
& '.\.venv\Scripts\python.exe' -m pip uninstall -y onnxruntime onnxruntime-directml
if ($LASTEXITCODE -ne 0) { throw 'ONNX Runtime upgrade preparation failed.' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Preparing the edge solver once; the first compilation can take several minutes.'
& '.\.venv\Scripts\python.exe' -c 'import os; os.environ["NUMBA_NUM_THREADS"]="4"; import pymatting'
if ($LASTEXITCODE -ne 0) { throw 'Edge solver initialization failed.' }
Write-Host 'Ready. Double-click start-iphoto.cmd.'

