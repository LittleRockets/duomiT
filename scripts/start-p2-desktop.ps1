$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$database = Join-Path $root "build\p2-user-test\workspace.sqlite3"

if (-not (Test-Path -LiteralPath $database)) {
  throw "P2 test workspace was not found. Run scripts\test-p2.ps1 first."
}

$projectId = python -c "import sqlite3,sys; connection=sqlite3.connect(sys.argv[1]); row=connection.execute('select id from projects order by rowid desc limit 1').fetchone(); print(row[0] if row else '')" $database
if ([string]::IsNullOrWhiteSpace($projectId)) {
  throw "The P2 test workspace has no Project. Run scripts\test-p2.ps1 first."
}

$env:PYTHONPATH = Join-Path $root "src"
Write-Host "Opening duomiT native Desktop..." -ForegroundColor Green
python -m duomit `
  --desktop-workspace-database $database `
  --desktop-project $projectId
