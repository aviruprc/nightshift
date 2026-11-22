# Keeps the live appliers running. -Workers N runs N parallel workers (company-sharded, own browser profile each). Stops when data\STOP exists
# or every worker finishes. Lock file: only one supervisor at a time.
param([int]$Target = 100, [int]$Workers = 1, [string]$Only = "")
Set-Location $PSScriptRoot
$lock = "data\supervisor.lock"
if ((Test-Path $lock) -and ((Get-Date) - (Get-Item $lock).LastWriteTime).TotalMinutes -lt 15) {
    $old = Get-Content $lock -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) { exit 1 }   # another supervisor is alive: never run two appliers
}
Set-Content $lock $PID
$per = [int][Math]::Ceiling($Target / [Math]::Max(1, $Workers))
$procs = @()
for ($k = 0; $k -lt $Workers; $k++) {
    $wa = @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'worker.ps1'),'-K',"$k",'-N',"$Workers",'-Target',"$per")
    if ($Only) { $wa += @('-Only', $Only) }   # an empty argument makes Start-Process reject the whole list
    $procs += Start-Process -PassThru -WindowStyle Hidden -FilePath powershell -ArgumentList $wa
    Start-Sleep 20   # stagger starts: the first worker builds the queue before the others read it
}
while (($procs | Where-Object { -not $_.HasExited }).Count -gt 0) { Set-Content $lock $PID; Start-Sleep 30 }
Remove-Item $lock -ErrorAction SilentlyContinue
