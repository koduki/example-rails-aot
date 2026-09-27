# Repository agent skills

These skills document workflows for this repository. They are aids to the current task, not a substitute for the user's instructions or the project's executable benchmark gates.

| Skill | Use it for | Executable source of truth |
| --- | --- | --- |
| [PR train](skills/pr-train-runner/SKILL.md) | Dependent issues and PRs, CI checks, Windows commit helper | [pr_train.ps1](skills/pr-train-runner/scripts/pr_train.ps1); merging requires explicit authorization |
| [Benchmarking](skills/rigorous-benchmarking/SKILL.md) | Load models, warmup and reporting discipline | [benchmark scripts](../scripts/bench/); k6 file is a standalone example |
| [Runtime verification](skills/cross-runtime-verifier/SKILL.md) | Quick HTTP comparison of five read endpoints | [preflight.py](../scripts/bench/preflight.py) for authoritative benchmark eligibility |

The existing [benchmark results](../docs/benchmark-results.md) are a closed-loop smoke measurement. Open-arrival capacity search on dedicated GCE hardware remains future work. The benchmark-only CRUD profile checks successful write operations independently; invalid input rendering and CSRF rejection remain known differences and are outside its workload.
