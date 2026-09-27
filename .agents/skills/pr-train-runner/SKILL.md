---
name: pr-train-runner
description: Execute dependent GitHub issues and PRs, check CI on the current PR HEAD, and use a Windows PowerShell helper for commits and explicitly authorized merges.
---

# PR train runner

Use for a user-authorized sequence of dependent issues. Continue implementation and local verification through the agreed scope; create a PR for review. A plan or a request to proceed does not by itself authorize merging. Merge only when the user has authorized it in the conversation.

1. Check the current branch and worktree, fetch the latest base, then create a dedicated branch. Avoid discarding other work.
2. Implement one reviewable change at a time; run relevant tests and inspect the diff.
3. Create a PR with a clear summary, test results, and issue linkage when applicable.
4. Watch the checks attached to the current PR HEAD. A cancelled older push run can be superseded by a newer successful run for the same workflow and check; a latest cancelled, failed, pending, or missing check blocks merge. Recheck HEAD, draft status, and merge status before acting.
5. Once merging is explicitly authorized, use the helper on a system with PowerShell and `gh`:

```powershell
pwsh -File .agents/skills/pr-train-runner/scripts/pr_train.ps1 -Action watch-and-merge -PrNumber 123 -AllowMerge
```

The helper requires `-AllowMerge`, waits up to 30 minutes for checks, and requires a clean merge state. The flag is a guardrail, not a replacement for user authorization. It syncs local `main` with `git pull --ff-only` after merging. If CI or the HEAD changes, fix and recheck rather than bypassing the gate.

For multiline Windows commits, use `-Action commit -Message "title`n`nbody"`; this writes an OS temporary file and calls `git commit -F`. Run `-Action sync-main` to update local main without merging. See [rationale](references/why-and-what.md).
