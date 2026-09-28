# Why benchmark design matters

A closed-loop client sends a new request after receiving a response. During a server pause, fewer requests arrive, so tail latency can appear better than it would under an external arrival schedule. An open-arrival test reduces this bias only while the generator can maintain its schedule; record dropped iterations and client headroom.

The [current repository report](../../../../docs/roundhouse-rails-jit-aot-report.md) records short closed-loop smoke trials and convergence runs. They are useful for pilot observations, not sustained capacity comparisons. At fixed offered RPS, similar achieved throughput mostly reflects the imposed load. A JIT interaction ratio such as `(emitted JIT capacity / emitted non-JIT capacity) / (Rails JIT capacity / Rails non-JIT capacity)` needs valid capacity estimates for all four configurations. It describes an interaction under that experimental setup, not the causal contribution of JIT alone.

SQLite WAL still has a single writer. VU-specific record IDs can avoid updating the same row, but they cannot remove database writer contention; measure lock behavior for write workloads. Likewise, a Spinel result reflects its complete AOT/server/database stack. Record CPU placement, warmup evidence, trial exclusions, and the precise memory metric before interpreting differences.
