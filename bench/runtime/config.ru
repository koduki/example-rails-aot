require 'json'
if ENV.fetch('BENCH_SHAPE') == 'rails'
  require '/app/config/environment'
  application = Rails.application
else
  application = Rack::Builder.parse_file('/app/config.ru')
end
require '/bench/runtime/probe'
use BenchProbe
run application
