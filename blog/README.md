# Canonical Rails fixture

This small articles-and-comments application is the source input for the
[Roundhouse / Spinel AOT experiment](../README.md). It is pinned to Rails
**8.0.5.1** on Ruby 3.4.5. The AOT workflow transforms this checked-in source;
the benchmark workflow derives an isolated copy with JRuby-compatible
dependencies, server settings, and a fresh SQLite fixture. The source Rails
version and application code are shared rather than rewritten for benchmarking.

CSRF verification is deliberately **disabled** in `config/application.rb` for
this experiment because the current emitted runtimes do not reject forged
writes. This application is not a production deployment template. Normal
create/update/delete behavior can be compared; CSRF rejection is outside the
equivalence claim, and invalid-input HTML/JSON behavior still differs.

For the pinned toolchain, tests, native build, container validation, and
benchmark commands, see the [root README](../README.md). For the measured
results and their limits, see the [validation report](../docs/roundhouse-rails-jit-aot-report.md).
