<#
.SYNOPSIS
    Automated Git and GitHub PR lifecycle helper for autonomous agents on Windows PowerShell.
.DESCRIPTION
    Provides reliable, idempotent helpers to:
    - Safely commit multi-line messages without quoting issues (git commit -F).
    - Inspect current PR checks and fail closed on missing, pending, or failed checks.
    - Merge explicitly authorized PRs and synchronize local main branch.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("commit", "watch-and-merge", "sync-main")]
    [string]$Action,

    [Parameter(Mandatory = $false)]
    [string]$Message,

    [Parameter(Mandatory = $false)]
    [int]$PrNumber,

    [switch]$AllowMerge
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
    if (-not $AllowMerge) {
        throw "Merge requires explicit -AllowMerge (and the user's authorization)."
    }

    $prInfo = (& gh pr view $TargetPr --json headRefOid,mergeable,mergeStateStatus,state,isDraft) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $prInfo -or $prInfo.state -ne "OPEN" -or $prInfo.isDraft) {
        throw "[PR-Train] PR must be open and ready for review."
    }
    $head = $prInfo.headRefOid

    # gh pr checks reports checks associated with the PR, unlike gh run list
    # filtered by a branch name (which can include runs from older commits).
    # A cancelled push run may be superseded by a later pull_request check
    # with the same workflow and check name. Never ignore the latest result.
    $deadline = (Get-Date).AddMinutes(30)
    do {
        $raw = & gh pr checks $TargetPr --json name,workflow,event,startedAt,bucket
        if (-not $raw) {
            throw "[PR-Train] No PR checks returned (gh exit: $LASTEXITCODE)."
        }
        try {
            $checks = @(ConvertFrom-Json -InputObject ($raw -join "`n"))
        }
        catch {
            throw "[PR-Train] Could not parse PR checks: $_"
        }
        if ($checks.Count -eq 0) {
            throw "[PR-Train] No checks registered for PR #$TargetPr."
        }
        $latest = @($checks | Group-Object workflow,name | ForEach-Object {
            $_.Group | Sort-Object startedAt, @{ Expression = { if ($_.event -eq 'pull_request') { 1 } else { 0 } } } -Descending | Select-Object -First 1
        })
        $blocked = @($latest | Where-Object { $_.bucket -notin @("pass", "skipping", "pending") })
        if ($blocked.Count -gt 0) {
            throw "[PR-Train] Failed or cancelled current checks: $($blocked.name -join ', ')"
        }
        $pending = @($latest | Where-Object { $_.bucket -eq "pending" })
        if ($pending.Count -eq 0) { break }
        if ((Get-Date) -ge $deadline) {
            throw "[PR-Train] Timed out waiting for PR checks."
        }
        Start-Sleep -Seconds 10
    } while ($true)

    $prInfo = (& gh pr view $TargetPr --json headRefOid,mergeable,mergeStateStatus,state,isDraft) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $prInfo.headRefOid -ne $head -or $prInfo.state -ne "OPEN" -or
        $prInfo.isDraft -or $prInfo.mergeable -ne "MERGEABLE" -or $prInfo.mergeStateStatus -ne "CLEAN") {
        throw "[PR-Train] PR HEAD changed or merge gate is not clean. Recheck the latest checks and PR state."
    }

    Write-Host "[PR-Train] Merging PR #$TargetPr..."
    & gh pr merge $TargetPr --merge --delete-branch --match-head-commit $head
    if ($LASTEXITCODE -ne 0) {
        throw "gh pr merge failed with exit code $LASTEXITCODE"
    }

    Write-Host "[PR-Train] Switching to main and pulling latest changes..."
    & git checkout main
    if ($LASTEXITCODE -ne 0) { throw "git checkout main failed" }
    & git pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "git pull --ff-only failed" }
    Write-Host "[PR-Train] PR #$TargetPr successfully merged and main synced!"
}

function Invoke-SyncMain {
    Write-Host "[PR-Train] Syncing local main branch with origin..."
    & git checkout main
    if ($LASTEXITCODE -ne 0) { throw "git checkout main failed" }
    & git pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "git pull --ff-only failed" }
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
