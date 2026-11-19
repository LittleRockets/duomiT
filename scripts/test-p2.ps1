$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $root "src"
$testRoot = Join-Path $root "build\p2-user-test"
New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
$database = Join-Path $testRoot "workspace.sqlite3"
Remove-Item -LiteralPath $database -Force -ErrorAction SilentlyContinue

Write-Host "Creating a local Project..." -ForegroundColor Cyan
$project = python -m duomit `
  --workspace-database $database `
  --workspace-project "P2 Local Test" `
  --workspace-create-project | ConvertFrom-Json

Write-Host "Creating a local Task..." -ForegroundColor Cyan
python -m duomit `
  --workspace-database $database `
  --workspace-project $project.project_id `
  --workspace-task "Verify local task entry" `
  --workspace-goal "Confirm Project and Task persistence without model or tool execution"

Write-Host "Reading the Project task list..." -ForegroundColor Cyan
python -m duomit `
  --workspace-database $database `
  --workspace-project $project.project_id

Write-Host "Running P2 resource and native-desktop boundary tests..." -ForegroundColor Cyan
python -m unittest discover -s (Join-Path $root "tests") -p "test_artifacts.py" -v
python -m unittest discover -s (Join-Path $root "tests") -p "test_resource_access.py" -v
python -m unittest discover -s (Join-Path $root "tests") -p "test_desktop_app.py" -v

Write-Host "P2 local test completed." -ForegroundColor Green
Write-Host "Workspace database: $database"
Write-Host "Open the native desktop shell with:" -ForegroundColor Cyan
Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\start-p2-desktop.ps1"
