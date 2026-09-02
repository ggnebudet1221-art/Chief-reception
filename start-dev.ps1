$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
$sitePackages = "C:\Users\Public\AIManagerVenv\Lib\site-packages"

if (-not (Test-Path $python)) {
    Write-Error "Python 3.13 is missing: $python"
    exit 1
}

if (-not (Test-Path $sitePackages)) {
    Write-Error "AI Manager dependency env is missing: $sitePackages"
    exit 1
}

Write-Host "AI Manager dev"
Write-Host "Project: $projectRoot"
Write-Host "Python:  $python"
Write-Host "Deps:    $sitePackages"

Push-Location $projectRoot
try {
    $env:PYTHONPATH = $sitePackages
    & $python -c "import fastapi, uvicorn, pydantic_core, anthropic; print('Backend deps OK:', pydantic_core.__version__)"
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    npm.cmd run desktop:dev
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
