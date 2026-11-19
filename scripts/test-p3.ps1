$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $root "src"

Write-Host "Running P3 planning-chain verification..." -ForegroundColor Cyan
python -m unittest `
  (Join-Path $root "tests\test_task_revisions.py") `
  (Join-Path $root "tests\test_need_records.py") `
  (Join-Path $root "tests\test_workflow_specs.py") `
  (Join-Path $root "tests\test_build_specs.py") `
  (Join-Path $root "tests\test_run_specs.py") `
  (Join-Path $root "tests\test_domain_handoffs.py") `
  (Join-Path $root "tests\test_flow_drafts.py") `
  (Join-Path $root "tests\test_flow_compiler.py") `
  (Join-Path $root "tests\test_node_catalog.py") `
  (Join-Path $root "tests\test_proposals.py") `
  (Join-Path $root "tests\test_builder_llm.py") -v

Write-Host "P3 planning-chain verification completed." -ForegroundColor Green
Write-Host "This verification creates no WorkflowRun, external operation, or model API request."
