# Brings Nightshift up (safe to run any time: it starts only what is NOT already running). Works from any folder.
#   dashboard -> http://127.0.0.1:8799 guardian -> starts + watches the applier workers, receipts, health
Set-Location $PSScriptRoot
$py = if (Test-Path (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')) { Join-Path $PSScriptRoot '.venv\Scripts\python.exe' } else { 'python' }   # the project's virtualenv when there is one
Remove-Item data\STOP, data\GUARDIAN_STOP, data\supervisor.lock -ErrorAction SilentlyContinue
function Running($pattern) { @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like $pattern }).Count -gt 0 }
$opened = $false
if (-not (Running '*track\live.py*'))     { Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList 'track\live.py' -WorkingDirectory $PSScriptRoot; $opened = $true }
if (-not (Running '*track\guardian.py*')) { Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList 'track\guardian.py' -WorkingDirectory $PSScriptRoot }
Start-Sleep 8
if ($opened) { Start-Process "http://127.0.0.1:8799" }   # only when the dashboard was just started: never pile up tabs. # the guardian starts the supervisor and workers within a minute
