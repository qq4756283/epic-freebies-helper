# Local one-shot runner for epic-freebies-helper (Windows / PowerShell)
# Usage:  pwsh -File .\run.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$env:PYTHONPATH      = "app"
$env:PYTHONUTF8      = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:ENABLE_APSCHEDULER = "false"

uv run app/deploy.py
