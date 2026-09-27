Rails.application.configure do
  config.enable_reloading = false
  config.eager_load = true
  config.consider_all_requests_local = false
  # Benchmark-only policy: Roundhouse's emitted servers currently render CSRF
  # tokens but do not verify them. Disable the Rails verifier in the derived
  # benchmark application so successful write operations do the same work.
  # The original blog/ application keeps Rails' normal protection.
  config.action_controller.default_protect_from_forgery = false
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
