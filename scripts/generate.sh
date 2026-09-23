#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source config/toolchain.env
test ! -e blog || { echo 'blog/ exists; generate in a fresh checkout to avoid overwriting source' >&2; exit 1; }
test "$(ruby -e 'print RUBY_VERSION')" = "$RUBY_VERSION"
gem install bundler -v "$BUNDLER_VERSION" --no-document
gem install rails -v "$RAILS_VERSION" --no-document
RAILS_NEW_VERSION="$RAILS_VERSION" bash scripts/vendor/create-blog blog
ruby -e 'p = "blog/Gemfile"; s = File.read(p); s.sub!(/^gem "rails".*$/, %q(gem "rails", "8.1.3.1")); File.write(p, s)'
# No generated application secrets are part of the example or source archive.
rm -f blog/config/master.key blog/config/credentials.yml.enc
(
  cd blog
  bundle install
  bundle exec rails db:prepare
)
