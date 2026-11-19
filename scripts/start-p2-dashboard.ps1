param(
  [int]$Port = 8765
)

$ErrorActionPreference = "Stop"

if ($Port -lt 1 -or $Port -gt 65535) {
  throw "Port must be between 1 and 65535."
}

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
Write-Host "duomiT read-only dashboard: http://127.0.0.1:$Port/" -ForegroundColor Green
Write-Host "Keep this window open. Press Ctrl+C to stop the local dashboard." -ForegroundColor Cyan

python -m duomit `
  --desktop-reference-workspace-database $database `
  --desktop-project $projectId `
  --desktop-port $Port
