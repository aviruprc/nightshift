# One parallel worker: its own browser-profile copy, its own slice of the queue (company-sharded), restarted if the watchdog kills it.
param([int]$K = 0, [int]$N = 1, [int]$Target = 25, [string]$Only = "")
Set-Location $PSScriptRoot
$py = if (Test-Path (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')) { Join-Path $PSScriptRoot '.venv\Scripts\python.exe' } else { 'python' }
$label = [string][char](65 + $K)
$env:JOBHUNT_LABEL = $label
$env:JOBHUNT_SHARD = "$K/$N"
if ($K -gt 0) {
    $env:JOBHUNT_PROFILE = (Join-Path $PSScriptRoot "data\browser_profile_w$K")
    $env:JOBHUNT_NO_OPEN = "1"
    $env:JOBHUNT_SKIP_NAUKRI = "1"   # the copied profile has no Naukri session: this worker takes company career sites only
}
$log = "data\live_run_$label.log"; $err = "data\live_run_$label.err"
while (-not (Test-Path data\STOP)) {
    if ($K -gt 0) {   # fresh copy of the signed-in profile (logins/cookies) before every start
        robocopy (Join-Path $PSScriptRoot "data\browser_profile") $env:JOBHUNT_PROFILE /MIR /XD Cache "Code Cache" GPUCache ShaderCache GrShaderCache /NFL /NDL /NJH /NJS /NP /R:0 /W:0 | Out-Null
    }
    $a = @('-u','hunt.py','run','--live','--target',"$Target",'--max-attempts',"$($Target*4)")
    if ($Only) { $a += @('--only', $Only) }
    $p = Start-Process -PassThru -WindowStyle Hidden -FilePath $py -ArgumentList $a -RedirectStandardOutput $log -RedirectStandardError $err
    while (-not $p.HasExited) { Start-Sleep 30 }
    if ($p.ExitCode -eq 0) {
        # exit 0 with nothing attempted = no work for this worker right now: wait for the guardian's discovery to refill the queue, then try again
        $last = (Get-Content $log -Tail 3 -ErrorAction SilentlyContinue) -join ' '
        if ($last -match 'done: \d+/\d+ \(0 attempted\)') { Start-Sleep 300; continue }
        # A run that DID work ends when its prepared queue is spent. The old `break` handed back to supervise.ps1, which then
        # exited, so the applier sat idle until the guardian's next tick noticed - 3-9 minutes of nothing between runs
        # (observed). Stay in the loop: a fresh run re-prepares the queue, one applier at a time, and data\STOP
        # still ends it at the top of the loop.
        Start-Sleep 20
        continue
    }
    Start-Sleep 10
}
