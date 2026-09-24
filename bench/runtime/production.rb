Rails.application.configure do
  config.enable_reloading = false
  config.eager_load = true
  config.consider_all_requests_local = false
  config.action_controller.perform_caching = false
  config.cache_store = :null_store
  config.active_job.queue_adapter = :inline
  config.log_level = :warn
  config.logger = ActiveSupport::Logger.new($stdout)
  config.force_ssl = false
  config.hosts.clear
  config.public_file_server.enabled = true
  config.secret_key_base = ENV.fetch("SECRET_KEY_BASE")
  config.yjit = ENV.fetch("BENCH_JIT") == "on" if defined?(RubyVM::YJIT)
end
