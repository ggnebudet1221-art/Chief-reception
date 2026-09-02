$ErrorActionPreference = "Stop"

$python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$job = Start-Job -ArgumentList $python, $root -ScriptBlock {
  param($pythonPath, $workingDirectory)
  Set-Location $workingDirectory
  & $pythonPath "scripts\run_main_with_shared_deps.py"
}

Start-Sleep -Seconds 12

try {
  $health = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/health" -TimeoutSec 5).StatusCode
} catch {
  $health = "DOWN: $($_.Exception.Message)"
}

Write-Output "JOB_ID=$($job.Id)"
Write-Output "JOB_STATE=$($job.State)"
Write-Output "HEALTH=$health"
Write-Output "---JOB OUTPUT---"
Receive-Job -Job $job -Keep

Stop-Job -Job $job -ErrorAction SilentlyContinue
Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
