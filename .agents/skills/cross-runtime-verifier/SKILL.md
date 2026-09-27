---
name: cross-runtime-verifier
description: >-
  Performs black-box semantic differential testing between heterogeneous Web runtimes
  (e.g., Rails vs JRuby vs Spinel AOT) by normalizing dynamic tokens, comparing HTML/JSON responses,
  and establishing preflight parity gates before performance benchmarking.
---

# Cross-Runtime Verifier

Use this skill when comparing rewritten, transpiled, or alternative implementations of Web services (e.g. Rails on CRuby vs JRuby vs Spinel AOT, Django vs Go, Node vs Rust) to verify semantic equivalence and prevent invalid performance benchmarks.

For detailed concepts, blocker classifications, and historical lessons from `example-rails-aot` Issues #14 & #20, see [Why and What Document](./references/why-and-what.md).

---

## Preflight Parity Workflow

### Step 1: Define Target Matrix & Endpoints
1. Designate the canonical baseline implementation (e.g. `http://127.0.0.1:3000` - Standard Rails).
2. List all candidate runtimes under evaluation (e.g. `http://127.0.0.1:3001` - JRuby, `http://127.0.0.1:3002` - Spinel).
3. Identify standard endpoints across HTTP methods and accept headers:
   - Primary HTML views: `GET /articles`, `GET /articles/1`, `GET /articles/new`
   - Data APIs: `GET /articles.json`, `GET /articles/1.json`
   - Mutation endpoints: `POST /articles`, `PUT /articles/1`
   - Validation & Error endpoints: `POST /articles` (with empty payload), invalid CSRF tokens.

### Step 2: Execute Semantic Differential Comparison
Run the differential testing helper script:
```powershell
python .agents/skills/cross-runtime-verifier/scripts/verify_differential.py `
  --baseline "http://127.0.0.1:3000" `
  --candidate "jruby=http://127.0.0.1:3001" `
  --candidate "spinel=http://127.0.0.1:3002"
```

The script automatically:
- Strips dynamic CSRF meta tokens and masks timestamps.
- Normalizes JSON keys, numeric representations, and whitespace.
- Outputs a clean Markdown differential report.

### Step 3: Classify Findings & Apply Benchmark Gates
Review each endpoint against the 3-tier qualification criteria:
1. **Eligible (`MATCH`)**:
   - Status code and normalized payload match 100%. Approved for performance benchmarking.
2. **Acceptable Variance**:
   - Minor discrepancies in non-functional headers (e.g. `Server: Puma` vs `Server: Spinel-HTTP`). Approved with documented note.
3. **Benchmark Blocker (`MISMATCH` or `ERROR`)**:
   - Discrepancies in validation rules, missing error wrappers, or skipped authentication.
   - **Action**: Quarantine these endpoints. Exclude them from load test profiles until implementation parity is achieved.
