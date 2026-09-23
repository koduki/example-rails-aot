#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.toolchain/bin:$PATH"
export CC="${CC:-clang}"
mkdir -p reports artifacts
bash scripts/transpile.sh
(
  cd out/spinel
  spin build
) 2>&1 | tee reports/spin-build.log
test -x out/spinel/build/bin/blog
file out/spinel/build/bin/blog | tee reports/binary.txt
ldd out/spinel/build/bin/blog | tee reports/ldd.txt
if grep -Ei 'libruby|not found' reports/ldd.txt; then exit 1; fi
# Preserve all emitted runtime files, but exclude compiler intermediates/tests.
rm -rf out/runtime
mkdir -p out/runtime/storage
for name in static public config db; do
  if test -d "out/spinel/$name"; then cp -a "out/spinel/$name" out/runtime/; fi
done
install -m 755 out/spinel/build/bin/blog out/runtime/blog
cp config/toolchain.env out/runtime/toolchain.env
tar -czf artifacts/blog-linux-x86_64.tar.gz -C out/runtime .
