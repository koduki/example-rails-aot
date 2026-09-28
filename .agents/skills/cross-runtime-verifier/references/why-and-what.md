# Why compare behavior first?

A fast error response can look like a performance gain. Compare equivalent work on identical fixtures before interpreting runtime differences.

This repository's [benchmark preflight](../../../../scripts/bench/preflight.py) is the eligibility gate. It canonicalizes HTML and JSON, checks five reads, exercises writes, and compares database state. The smaller [HTTP helper](../scripts/verify_differential.py) compares only five GET responses across already running services. It reuses the preflight canonicalizer but cannot observe DB state or test POST/PUT/DELETE.

The [validation report](../../../../docs/roundhouse-rails-jit-aot-report.md) reports eligible reads and unresolved write differences: invalid HTML create/update responses, JSON validation errors, and CSRF rejection. Those write profiles remain blocked until preflight passes. A match for a limited set of GET responses is evidence for those routes only.
