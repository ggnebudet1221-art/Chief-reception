$ErrorActionPreference = "Stop"

$healthUrl = "http://127.0.0.1:8000/health"
$rootUrl = "http://127.0.0.1:8000/"

try {
    $health = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
    if ($health.StatusCode -eq 200) {
        Write-Host "AI Manager backend already running at $healthUrl"
        exit 0
    }
} catch {
    try {
        $root = Invoke-WebRequest -UseBasicParsing $rootUrl -TimeoutSec 2
        if ($root.Headers.Server -like "*SimpleHTTP*") {
            Write-Error "Port 8000 is occupied by python -m http.server. Stop it and run npm run desktop:dev again."
            exit 1
        }
    } catch {
    }
}

Write-Host "Starting AI Manager FastAPI backend..."
$projectRoot = Split-Path -Parent $PSScriptRoot
$selectedCandidate = $null
$sharedSitePackages = "C:\Users\Public\AIManagerVenv\Lib\site-packages"
$candidates = @(
    @{
        Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
        PythonPath = $sharedSitePackages
        Name = "Python 3.13 + AIManagerVenv site-packages"
    },
    @{
        Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
        PythonPath = $null
        Name = "Python 3.12"
    },
    @{
        Python = "python"
        PythonPath = $null
        Name = "PATH python"
    }
)

foreach ($candidate in $candidates) {
    $python = $candidate.Python
    if ($python -eq "python" -or (Test-Path $python)) {
        $selectedCandidate = $candidate
        Write-Host "Using $($candidate.Name): $python"
        if ($candidate.PythonPath) {
            $env:PYTHONPATH = $candidate.PythonPath
            Write-Host "PYTHONPATH: $env:PYTHONPATH"
        }
        & $python -m src.main
        if ($LASTEXITCODE -eq 0) {
            exit 0
        }
        Write-Warning "FastAPI backend exited with code $LASTEXITCODE. Trying next Python candidate."
    }
}

if ($selectedCandidate) {
    Write-Warning "Emergency static fallback is starting. Real API is unavailable, and frontend mock fallback may be used."
    & $selectedCandidate.Python -m http.server 8000 --directory web
    exit $LASTEXITCODE
}

Write-Error "Python executable not found. Install Python or create venv\Scripts\python.exe."
exit 1
