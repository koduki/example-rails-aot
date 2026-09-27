#!/usr/bin/env bash
set -euo pipefail
cd /app
export RAILS_ENV=production RACK_ENV=production
export BLOG_DB=/data/benchmark.sqlite3
export SECRET_KEY_BASE=benchmark-only-not-a-deployment-secret-0123456789abcdef
export RAILS_MAX_THREADS="${RAILS_MAX_THREADS:-3}"
export WEB_CONCURRENCY=0
export BENCH_JIT="${BENCH_JIT:?BENCH_JIT is required}"
unset RUBYOPT JRUBY_OPTS JAVA_TOOL_OPTIONS JDK_JAVA_OPTIONS
if [[ "$BENCH_RUNTIME" = spinel ]]; then
  [[ "$BENCH_JIT" = aot ]]
  exec /app/blog
elif [[ "$BENCH_RUNTIME" = jruby ]]; then
  case "$BENCH_JIT" in jit) mode=JIT;; off) mode=OFF;; *) exit 2;; esac
  export JRUBY_OPTS="-Xcompile.mode=$mode -J-Xms256m -J-Xmx1024m"
else
  case "$BENCH_JIT" in on) export RUBYOPT=--yjit;; off) export RUBYOPT=--disable-yjit;; *) exit 2;; esac
fi
exec bundle exec puma -C /bench/runtime/puma.rb /bench/runtime/config.ru
