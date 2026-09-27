require 'json'
require 'jruby' if RUBY_ENGINE == 'jruby'
app_root = ENV.fetch('BENCH_APP_ROOT', '/app')
if ENV.fetch('BENCH_SHAPE') == 'rails'
  require File.join(app_root, 'config/environment')
  application = Rails.application
else
  application = Rack::Builder.parse_file(File.join(app_root, 'config.ru'))
end
require_relative 'probe'
use BenchProbe
run application
