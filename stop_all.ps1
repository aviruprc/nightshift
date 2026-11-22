# Stops everything cleanly (no new applications; open pages are left alone).
Set-Location $PSScriptRoot
New-Item -ItemType File data\STOP, data\GUARDIAN_STOP -Force | Out-Null
Start-Sleep 5
Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.Name -eq 'python.exe' -and ($_.CommandLine -like '*hunt.py run*' -or $_.CommandLine -like '*guardian.py*' -or $_.CommandLine -like '*track\live.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.Name -eq 'powershell.exe' -and ($_.CommandLine -like '*supervise.ps1*' -or $_.CommandLine -like '*worker.ps1*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
