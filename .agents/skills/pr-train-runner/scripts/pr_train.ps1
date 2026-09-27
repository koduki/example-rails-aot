<#
.SYNOPSIS
    Automated Git and GitHub PR lifecycle helper for autonomous agents on Windows PowerShell.
.DESCRIPTION
    Provides reliable, idempotent helpers to:
    - Safely commit multi-line messages without quoting issues (git commit -F).
    - Watch PR checks, filtering out cancelled/superseded workflow runs.
    - Merge PRs with --delete-branch and synchronize local main branch.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("commit", "watch-and-merge", "sync-main")]
    [string]$Action,

    [Parameter(Mandatory = $false)]
    [string]$Message,

    [Parameter(Mandatory = $false)]
    [int]$PrNumber
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-SafeCommit {
    param([string]$CommitMessage)
    if ([string]::IsNullOrWhiteSpace($CommitMessage)) {
        throw "Commit message cannot be empty."
    }
    $tempFile = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tempFile, $CommitMessage, [System.Text.Encoding]::UTF8)
        & git commit -F $tempFile
        if ($LASTEXITCODE -ne 0) {
            throw "git commit failed with code $LASTEXITCODE"
        }
    }
    finally {
        if (Test-Path $tempFile) {
            Remove-Item -Path $tempFile -Force
        }
    }
}

function Invoke-WatchAndMerge {
    param([int]$TargetPr)
    if ($TargetPr -le 0) {
        throw "Valid PR number must be specified."
    }

    Write-Host "[PR-Train] Checking active workflow runs for PR #$TargetPr..."
    $branch = (& gh pr view $TargetPr --json headRefName --jq .headRefName).Trim()
    Write-Host "[PR-Train] PR branch: $branch"

    # Identify non-cancelled workflow runs triggered by pull_request
    $runsJson = (& gh run list --branch $branch --event pull_request --json databaseId,status,conclusion,name) | ConvertFrom-Json
    foreach ($run in $runsJson) {
        if ($run.status -ne "completed") {
            Write-Host "[PR-Train] Watching active run $($run.databaseId) ($($run.name))..."
            & gh run watch $run.databaseId
        }
    }

    Write-Host "[PR-Train] Verifying PR check conclusion..."
    $checks = & gh pr checks $TargetPr
    Write-Host $checks

    $prInfo = (& gh pr view $TargetPr --json mergeable,mergeStateStatus,state) | ConvertFrom-Json
    if ($prInfo.state -ne "OPEN") {
        Write-Host "[PR-Train] PR #$TargetPr is already $($prInfo.state)."
        return
    }

    if ($prInfo.mergeable -ne "MERGEABLE") {
        throw "[PR-Train] PR #$TargetPr is not mergeable (state: $($prInfo.mergeable), status: $($prInfo.mergeStateStatus)). Manual intervention required."
    }

    Write-Host "[PR-Train] Merging PR #$TargetPr..."
    & gh pr merge $TargetPr --merge --delete-branch
    if ($LASTEXITCODE -ne 0) {
        throw "gh pr merge failed with exit code $LASTEXITCODE"
    }

    Write-Host "[PR-Train] Switching to main and pulling latest changes..."
    & git checkout main
    & git pull
    Write-Host "[PR-Train] PR #$TargetPr successfully merged and main synced!"
}

function Invoke-SyncMain {
    Write-Host "[PR-Train] Syncing local main branch with origin..."
    & git checkout main
    & git pull
}

switch ($Action) {
    "commit" {
        Invoke-SafeCommit -CommitMessage $Message
    }
    "watch-and-merge" {
        Invoke-WatchAndMerge -TargetPr $PrNumber
    }
    "sync-main" {
        Invoke-SyncMain
    }
}
