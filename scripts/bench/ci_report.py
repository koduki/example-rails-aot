"""CI correctness report. Deliberately excludes capacity ranking and speedups."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'bench-results/ci-verification-report.md'
TARGETS = json.loads((ROOT / 'bench/targets.yml').read_text())['targets']


def read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def generate():
    builds = read(ROOT / 'bench-results/build/images.json')
    checks = read(ROOT / 'bench-results/preflight/preflight.json')
    trial = read(ROOT / 'bench-results/measurement/report.json')
    tests = read(ROOT / 'bench-results/unit-status.json')
    tf = read(ROOT / 'bench-results/terraform-status.json')
    commit = os.environ.get('GITHUB_SHA') or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    lines = ['# CI Verification Report', '', f'- Commit: `{commit}`',
             '- CI smoke is a regression signal, not a capacity benchmark.', '',
             '## Build, runtime and endpoint gate', '',
             '| Target | Build | Runtime/JIT probe | Page eligible |', '| --- | --- | --- | --- |']
    stages = {b.get('stage') for b in builds} if isinstance(builds, list) else set()
    for target, spec in TARGETS.items():
        check = checks.get(target, {})
        probe = check.get('status', 'missing')
        lines.append(f'| `{target}` | {"passed" if spec["image"] in stages else "missing"} | {probe} | {"/articles?page=1" in check.get("eligible_endpoints", [])} |')
    lines += ['', '## Functional and trial status', '',
              f'- CRUD preflight cases: {sum(checks.get(t, {}).get("cases", {}).get("update", {}).get("status") == "passed" for t in TARGETS)}/{len(TARGETS)} update; {sum(checks.get(t, {}).get("cases", {}).get("create", {}).get("status") == "passed" for t in TARGETS)}/{len(TARGETS)} create.',
              f'- Short smoke statuses: `{trial.get("trial_status_counts", {})}`.',
              f'- Harness unit tests: `{tests.get("status", "unknown")}`.',
              f'- Terraform fmt/init/validate: `{tf.get("status", "unknown")}`.', '',
              '## Known exclusions', '',
              '- Hosted runner observations are excluded from GCE capacity, runtime winner and speedup claims.',
              '- JRuby long convergence is a manual diagnostic workflow.',
              '- GCE execution requires separately provisioned private app/tester VMs and a real run.', '']
    DEST.parent.mkdir(exist_ok=True, parents=True)
    DEST.write_text('\n'.join(lines))
    return DEST

if __name__ == '__main__':
    print(generate())
