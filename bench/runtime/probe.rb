# Unmeasured diagnostic path, executed in the actual serving process.
class BenchProbe
  PRAGMAS = { 'journal_mode' => 'wal', 'synchronous' => '1', 'foreign_keys' => '1',
              'busy_timeout' => '5000', 'cache_size' => '-65536', 'mmap_size' => '268435456' }.freeze
  def initialize(app)
    @app = app
  end
  def call(env)
    return @app.call(env) unless env['PATH_INFO'] == '/__bench/runtime'
    info = { 'runtime' => RUBY_ENGINE, 'ruby_version' => RUBY_VERSION, 'pid' => Process.pid,
             'shape' => ENV.fetch('BENCH_SHAPE'), 'jit' => ENV.fetch('BENCH_JIT'),
             'pool_size' => Integer(ENV.fetch('RAILS_MAX_THREADS')) }
    if RUBY_ENGINE == 'jruby'
      info['compile_mode'] = JRuby.runtime.instance_config.compile_mode.to_s
      info['jvm_args'] = java.lang.management.ManagementFactory.runtime_mx_bean.input_arguments.to_a.map(&:to_s)
      info['jvm_compiler'] = java.lang.management.ManagementFactory.compilation_mx_bean&.name
      expected = ENV.fetch('BENCH_JIT') == 'off' ? 'OFF' : 'JIT'
      raise 'JRuby mode mismatch' unless info['compile_mode'] == expected && info['jvm_compiler']
    else
      info['yjit_enabled'] = defined?(RubyVM::YJIT) ? RubyVM::YJIT.enabled? : false
      raise 'YJIT mismatch' unless info['yjit_enabled'] == (ENV.fetch('BENCH_JIT') == 'on')
    end
    read = lambda do |sql|
      if ENV.fetch('BENCH_SHAPE') == 'rails'
        ActiveRecord::Base.connection.select_value(sql).to_s
      else
        stmt = Db.prepare(sql)
        begin
          Db.step?(stmt) ? Db.column_text(stmt, 0).to_s : ''
        ensure
          Db.finalize(stmt)
        end
      end
    end
    collect = lambda do
      info['sqlite_version'] = read.call('SELECT sqlite_version()')
      info['pragmas'] = PRAGMAS.keys.to_h { |key| [key, read.call("PRAGMA #{key}")] }
    end
    if ENV.fetch('BENCH_SHAPE') == 'rails'
      ActiveRecord::Base.connection_pool.with_connection { collect.call }
    else
      Db.with_connection { collect.call }
    end
    raise "PRAGMA mismatch: #{info['pragmas']}" unless info['pragmas'] == PRAGMAS
    [200, {'content-type' => 'application/json'}, [JSON.generate(info)]]
  end
end
