#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source config/toolchain.env
ROOT="$PWD"
PREFIX="$ROOT/.toolchain"
mkdir -p .cache/downloads "$PREFIX/bin"
test "$(uname -s)-$(uname -m)" = Linux-x86_64 || { echo 'Linux x86-64 is required' >&2; exit 1; }
download() {
  local url="$1" path="$2" digest="$3"
  if ! test -f "$path"; then
    curl --fail --location --retry 3 "$url" -o "$path.part"
    mv "$path.part" "$path"
  fi
  printf '%s  %s\n' "$digest" "$path" | sha256sum --check -
}
download "https://github.com/rubys/roundhouse/releases/download/$ROUNDHOUSE_VERSION/roundhouse-x86_64-unknown-linux-musl.tar.xz" .cache/downloads/roundhouse.tar.xz "$ROUNDHOUSE_SHA256"
mkdir -p .cache/roundhouse
tar -xJf .cache/downloads/roundhouse.tar.xz -C .cache/roundhouse
RH_BIN="$(find .cache/roundhouse -type f -name roundhouse | head -1)"
test -n "$RH_BIN"
install -m 755 "$RH_BIN" "$PREFIX/bin/roundhouse"
download "https://github.com/matz/spinel/releases/download/$SPINEL_VERSION/spinel-$SPINEL_VERSION.tar.xz" .cache/downloads/spinel.tar.xz "$SPINEL_SHA256"
if ! test -x "$PREFIX/bin/spin"; then
  mkdir -p .cache/spinel
  tar -xJf .cache/downloads/spinel.tar.xz -C .cache/spinel --strip-components=1
  make -C .cache/spinel -j"${BUILD_JOBS:-2}" CC="${CC:-clang}"
  make -C .cache/spinel install PREFIX="$PREFIX" CC="${CC:-clang}"
fi
export PATH="$PREFIX/bin:$PATH"
roundhouse --version
spinel --version
spin --version
