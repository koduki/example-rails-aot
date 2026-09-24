#!/usr/bin/env python3
"""Apply recorded, fail-closed instrumentation to the pinned emitted runtime."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Pinned emitter changed: expected one {old!r}')
    return text.replace(old, new, 1)

def instrument(output, target):
    output = Path(output)
    path = output / 'runtime/db.rb'
    before = path.read_bytes()
    text = before.decode()
    if target == 'ruby':
        anchor = 'db.execute("PRAGMA synchronous=NORMAL")'
        extra = '\n'.join('      db.execute("PRAGMA ' + x + '")' for x in
                          ['foreign_keys=ON', 'cache_size=-65536', 'mmap_size=268435456'])
        text = replace_once(text, anchor, anchor + '\n' + extra)
    elif target == 'jruby':
        anchor = 'st.execute("PRAGMA synchronous=NORMAL")'
        extra = '\n'.join('      st.execute("PRAGMA ' + x + '")' for x in
                          ['foreign_keys=ON', 'cache_size=-65536', 'mmap_size=268435456'])
        text = replace_once(text, anchor, anchor + '\n' + extra)
    else:
        text = replace_once(text, '"PRAGMA synchronous=NORMAL",',
                            '"PRAGMA synchronous=NORMAL",\n    "PRAGMA foreign_keys=ON",')
    path.write_text(text)
    changes = {'runtime/db.rb': {'before': hashlib.sha256(before).hexdigest(),
                                'after': hashlib.sha256(path.read_bytes()).hexdigest()}}
    if target != 'spinel':
        shutil.copyfile(ROOT / 'bench/emitted.Gemfile', output / 'Gemfile')
        shutil.copyfile(ROOT / 'bench/emitted.Gemfile.lock', output / 'Gemfile.lock')
    else:
        # Diagnostic branch only; no Ruby reflection in the compiled application.
        path = output / 'main.rb'
        before = path.read_bytes()
        probe = '''  def self.bench_scalar(sql)
    stmt = Db.prepare(sql)
    value = ""
    if Db.step?(stmt)
      value = Db.column_text(stmt, 0)
    end
    Db.finalize(stmt)
    value
  end

  def self.bench_probe
    out = '{"runtime":"spinel","jit":"aot","pragmas":{'
    sep = ""
    ["journal_mode", "synchronous", "foreign_keys", "busy_timeout", "cache_size", "mmap_size"].each do |key|
      out = out + sep + '"' + key + '":"' + Main.bench_scalar("PRAGMA " + key) + '"'
      sep = ","
    end
    out + '},"sqlite_version":"' + Main.bench_scalar("SELECT sqlite_version()") + '"}'
  end

'''
        anchor = '  def self.dispatch(req, res)\n'
        branch = '''    if req.path == "/__bench/runtime"
      res.status = 200
      res.headers["Content-Type"] = "application/json"
      res.body = Main.bench_probe
      return
    end
'''
        path.write_text(replace_once(before.decode(), anchor, probe + anchor + branch))
        changes['main.rb'] = {'before': hashlib.sha256(before).hexdigest(),
                              'after': hashlib.sha256(path.read_bytes()).hexdigest()}
    (output / 'benchmark-emission.json').write_text(json.dumps({
        'target': target, 'patches': changes,
        'purpose': 'Equal per-connection SQLite pragmas and unmeasured runtime probe; no business changes'
    }, indent=2) + '\n')

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('output'); p.add_argument('target', choices=['ruby', 'jruby', 'spinel'])
    instrument(**vars(p.parse_args()))
