---
name: pr-train-runner
description: >-
  Automates the chained execution of multi-step GitHub issues by creating feature branches,
  safely committing changes on Windows, pushing, opening PRs, monitoring CI workflows,
  merging when green, and advancing to the next dependent task without repetitive manual confirmation.
---

# PR Train Runner

Use this skill when managing or executing multi-issue epics, roadmap milestones, or chained Pull Requests where tasks depend on each other and require continuous implementation, CI verification, and merging into `main`.

For detailed background, architectural concepts, and the origin story from `example-rails-aot` Issues #11–#21, see [Why and What Document](./references/why-and-what.md).

---

## Autonomous Operation Agreement

When the user agrees to a multi-stage plan or instructs "進めて" / "マージまでやって" / "最後まで実施して":
1. **Do not stop to ask trivial confirmation** between PR creation, CI monitoring, and merging.
2. Only interrupt the user if:
   - A critical CI test fails and requires fundamental design changes.
   - An ambiguous product/architecture decision is encountered.
   - All tasks in the epic/parent issue are completed.

---

## Execution Protocol

### Step 1: Branch Preparation & Implementation
1. Ensure the local branch is clean and up to date with `main`:
   ```powershell
   git checkout main
   git pull
   ```
2. Create and switch to a descriptive feature branch:
   ```powershell
   git checkout -b feat/<issue-id>-<short-description>
   ```
3. Implement the required code, unit tests, and documentation.
4. Run all relevant local tests before committing:
   ```powershell
   python -m unittest discover -s tests/bench -v
   ```

### Step 2: Safe Commit on Windows
Avoid inline multiline strings in PowerShell. Use the provided helper or write to a temporary file:
- Using helper script:
  ```powershell
  pwsh -File .agents/skills/pr-train-runner/scripts/pr_train.ps1 -Action commit -Message "feat(scope): concise title (#issue_id)`n`nDetailed body."
  ```
- Or manual file commit:
  ```powershell
  # Write message to commit_msg.txt
  git commit -F commit_msg.txt
  del commit_msg.txt
  ```

### Step 3: Push & Create Pull Request
1. Push branch to remote:
   ```powershell
   git push origin feat/<issue-id>-<short-description>
   ```
2. Create the Pull Request with explicit issue linkage (`Closes #<id>`):
   ```powershell
   gh pr create --title "feat(scope): concise title (#id)" --body "Closes #id`n`n### Summary`n..."
   ```

### Step 4: Intelligent CI Monitoring & Auto-Merge
1. Identify the PR number from output or `gh pr view`.
2. Monitor workflows and merge automatically upon success:
   ```powershell
   pwsh -File .agents/skills/pr-train-runner/scripts/pr_train.ps1 -Action "watch-and-merge" -PrNumber <PR_NUMBER>
   ```
3. If monitoring manually:
   - Run `gh run watch <RUN_ID>` as a background task.
   - Distinguish between superseded/cancelled push runs and active pull_request runs.
   - Once all active checks pass, execute:
     ```powershell
     gh pr merge <PR_NUMBER> --merge --delete-branch
     git checkout main
     git pull
     ```

### Step 5: Advance to the Next Milestone
1. Verify that the linked sub-issue was closed automatically by the PR merge (`gh issue view <id> --json state`).
2. Update the parent issue progress checkbox if applicable (`gh issue comment <parent-id>`).
3. Immediately proceed to the next dependent sub-issue without asking for user permission, until the epic reaches its final destination.
