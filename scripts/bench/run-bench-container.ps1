# PowerShell wrapper to run benchmark tests or CLI inside a Linux container.
param (
    [Parameter(Position = 0)]
    [string]$Command = "tests",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

if ($Command -eq "tests") {
    Write-Host "Running tests inside Linux container (python:3.12-slim)..."
    docker run --rm `
        -v "${RepoRoot}:/workspace" `
        -w /workspace `
        python:3.12-slim `
        python3 -m unittest discover -s tests/bench -v
} else {
    Write-Host "Running benchmark command inside Linux container: $Command $RemainingArgs"
    docker run --rm `
        -v "${RepoRoot}:/workspace" `
        -v "/var/run/docker.sock:/var/run/docker.sock" `
        -w /workspace `
        python:3.12-slim `
        python3 scripts/bench/run.py $Command $RemainingArgs
}
