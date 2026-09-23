#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../blog"
bundle check || bundle install
test "$(bundle exec rails --version)" = 'Rails 8.1.3.1'
bundle exec rails db:prepare
RAILS_ENV=test bundle exec rails db:prepare
bundle exec rails test
