# Why the PR train has gates

Dependent issues benefit from continuous implementation and CI monitoring, but passing checks and permission to merge are separate conditions. Review the user's instruction for merge authorization before running `watch-and-merge -AllowMerge`.

A branch can have both cancelled push runs and newer pull request runs. The [PowerShell helper](../scripts/pr_train.ps1) queries PR checks, selects the latest per workflow and check name, and blocks on failed, cancelled, pending (after timeout), or missing checks. It also rechecks the PR HEAD, draft state, and clean merge status. The helper is designed for repositories where those check names represent the intended CI gate; required workflow configuration and branch protection still belong in GitHub settings.

The commit action writes a message to an OS temporary file and deletes it in a `finally` block. After a merge, `git pull --ff-only` prevents an unintended local merge commit.
