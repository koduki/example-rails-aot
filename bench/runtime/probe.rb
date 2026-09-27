# Unmeasured diagnostic path, executed in the actual serving process.
class BenchProbe
  PRAGMAS = { 'journal_mode' => 'wal', 'synchronous' => '1', 'foreign_keys' => '1',
              'busy_timeout' => '5000', 'cache_size' => '-65536', 'mmap_size' => '268435456' }.freeze
  def initialize(app)
    @app = app
  end
  def call(env)
    if env['PATH_INFO'] == '/__bench/diagnostics'
      return serve_diagnostics
    end
    return @app.call(env) unless env['PATH_INFO'] == '/__bench/runtime'
    info = { 'runtime' => RUBY_ENGINE, 'ruby_version' => RUBY_VERSION, 'pid' => Process.pid,
             'shape' => ENV.fetch('BENCH_SHAPE'), 'jit' => ENV.fetch('BENCH_JIT'),
             'pool_size' => Integer(ENV.fetch('RAILS_MAX_THREADS')) }
    if RUBY_ENGINE == 'jruby'
      info['jruby_version'] = JRUBY_VERSION
      info['java_version'] = java.lang.System.getProperty('java.version')
      info['java_vendor'] = java.lang.System.getProperty('java.vendor')
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

  def serve_diagnostics
    diag = {
      'runtime' => RUBY_ENGINE,
      'ruby_version' => RUBY_VERSION,
      'pid' => Process.pid,
      'shape' => ENV.fetch('BENCH_SHAPE', 'unknown'),
      'jit' => ENV.fetch('BENCH_JIT', 'unknown'),
      'time' => Time.now.to_f
    }
    if RUBY_ENGINE == 'jruby'
      diag['jruby_version'] = JRUBY_VERSION
      diag['compile_mode'] = JRuby.runtime.instance_config.compile_mode.to_s
      comp = java.lang.management.ManagementFactory.compilation_mx_bean
      if comp
        diag['jvm_compiler'] = comp.name
        diag['jvm_compilation_time_ms'] = comp.total_compilation_time
      end
      diag['jvm_args'] = java.lang.management.ManagementFactory.runtime_mx_bean.input_arguments.to_a.map(&:to_s)
      diag['jvm_gc'] = java.lang.management.ManagementFactory.garbage_collector_mx_beans.to_a.map do |gc|
        { 'name' => gc.name, 'collection_count' => gc.collection_count, 'collection_time_ms' => gc.collection_time }
      end
      mem = java.lang.management.ManagementFactory.memory_mx_bean
      if mem
        heap = mem.heap_memory_usage
        non_heap = mem.non_heap_memory_usage
        diag['jvm_memory'] = {
          'heap_used_bytes' => heap.used,
          'heap_committed_bytes' => heap.committed,
          'heap_max_bytes' => heap.max,
          'non_heap_used_bytes' => non_heap.used,
          'non_heap_committed_bytes' => non_heap.committed
        }
      end
      thread = java.lang.management.ManagementFactory.thread_mx_bean
      if thread
        diag['thread_count'] = thread.thread_count
        diag['peak_thread_count'] = thread.peak_thread_count
      end
    else
      yjit_on = defined?(RubyVM::YJIT) && RubyVM::YJIT.enabled?
      diag['yjit_enabled'] = yjit_on
      if yjit_on
        begin
          diag['yjit_stats'] = RubyVM::YJIT.runtime_stats(all: true) rescue (RubyVM::YJIT.runtime_stats rescue nil)
        rescue StandardError => e
          diag['yjit_stats_error'] = e.message
        end
      end
      begin
        diag['gc_stat'] = GC.stat
        diag['gc_count'] = GC.count
      rescue StandardError => e
        diag['gc_stat_error'] = e.message
      end
    end
    [200, { 'content-type' => 'application/json' }, [JSON.generate(diag)]]
  rescue StandardError => e
    [500, { 'content-type' => 'application/json' }, [JSON.generate({ 'error' => e.message })]]
  end
end
