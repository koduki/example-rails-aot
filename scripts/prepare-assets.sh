#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
APP="${BENCH_APP_DIR:-$ROOT/blog}"
ASSETS="${BENCH_ASSETS_DIR:-$ROOT/.cache/static-assets}"
rm -rf "$ASSETS"
mkdir -p "$ASSETS"
(
  cd "$APP"
  bundle exec rails tailwindcss:build
  cp -a app/javascript/. "$ASSETS/"
  cp -a app/assets/stylesheets/. "$ASSETS/"
  cp app/assets/builds/tailwind.css "$ASSETS/tailwind.css"
  TURBO="$(bundle exec ruby -e 'puts Gem::Specification.find_by_name("turbo-rails").gem_dir')"
  STIMULUS="$(bundle exec ruby -e 'puts Gem::Specification.find_by_name("stimulus-rails").gem_dir')"
  cp "$TURBO/app/assets/javascripts/turbo.min.js" "$ASSETS/"
  cp "$STIMULUS/app/assets/javascripts/stimulus.min.js" "$ASSETS/"
  cp "$STIMULUS/app/assets/javascripts/stimulus-loading.js" "$ASSETS/"
)
