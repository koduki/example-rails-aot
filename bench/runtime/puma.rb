threads Integer(ENV.fetch("RAILS_MAX_THREADS", "3")), Integer(ENV.fetch("RAILS_MAX_THREADS", "3"))
workers 0
bind "tcp://0.0.0.0:3000"
environment "production"
