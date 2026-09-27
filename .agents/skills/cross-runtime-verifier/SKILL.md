---
name: cross-runtime-verifier
description: Screen five read-only article endpoints across running Rails, JRuby, and Spinel services; use the benchmark preflight for mutation and database eligibility.
---

# Cross-runtime verifier

Use this skill before comparing runtimes. Start each service from the same fixture and use the same route and Accept header.

For a quick read-only screen:

```bash
python3 .agents/skills/cross-runtime-verifier/scripts/verify_differential.py \
  --baseline http://127.0.0.1:3000 \
  --candidate jruby=http://127.0.0.1:3001 \
  --candidate spinel=http://127.0.0.1:3002
```

The helper checks five GET routes (`/articles`, `/articles/1`, `/articles/new`, and their two JSON equivalents). It compares status, media type, and canonical response body using `scripts/bench/preflight.py`. HTML is parsed into DOM events with only specific dynamic token values masked; JSON timestamps are normalized to the same instant, not erased. Non-200 reads, unparsable responses, and transport errors block this screen. The helper exits nonzero on a mismatch.

For benchmark eligibility, run the repository's [benchmark preflight](../../../scripts/bench/preflight.py) through `scripts/bench/run.py preflight` with its target manifest and fixture databases. It additionally verifies serving runtime, mutations, and DB state. Consult `scripts/bench/run.py preflight --help` and [benchmark workflow](../../../.github/workflows/benchmark.yml) for arguments. The HTTP helper cannot approve CRUD or establish whole-application equivalence. Record and fix each blocker before benchmarking that endpoint.

See [rationale](references/why-and-what.md).
