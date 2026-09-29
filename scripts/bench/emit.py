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
    # Rails 8 hidden fields include autocomplete=off. Repair the emitted helper,
    # rather than teaching the comparison to ignore the missing attribute.
    helper = output / 'runtime/action_view/view_helpers.rb'
    before_helper = helper.read_bytes()
    updated = before_helper.decode()
    for name in ('authenticity_token', '_method'):
        old = '<input type="hidden" name="' + name + '"'
        if old not in updated:
            raise ValueError('Pinned hidden-field helper changed: ' + name)
        updated = updated.replace(old, '<input autocomplete="off" type="hidden" name="' + name + '"')
    helper.write_text(updated)
    changes['runtime/action_view/view_helpers.rb'] = {
        'before': hashlib.sha256(before_helper).hexdigest(),
        'after': hashlib.sha256(helper.read_bytes()).hexdigest()}
    # CRuby's thread-local helper is loaded after view_helpers and overrides
    # csrf_token_hidden_input, so repair the serving implementation as well.
    thread_helper = output / 'runtime/thread_state.rb'
    before_thread = thread_helper.read_bytes()
    thread_helper.write_text(replace_once(before_thread.decode(),
        '<input type="hidden" name="authenticity_token"',
        '<input autocomplete="off" type="hidden" name="authenticity_token"'))
    changes['runtime/thread_state.rb'] = {
        'before': hashlib.sha256(before_thread).hexdigest(),
        'after': hashlib.sha256(thread_helper.read_bytes()).hexdigest()}
    # Roundhouse leaves ActiveRecord limit/offset unlowered in controller actions.
    # Provide native SQL LIMIT/OFFSET execution with associated comments preloading.
    ctrl_path = output / 'app/controllers/articles_controller.rb'
    if ctrl_path.exists():
        before_ctrl = ctrl_path.read_bytes()
        ctrl_text = before_ctrl.decode()
        target_code = '      @articles = Article.includes(:comments).order(created_at: :desc, id: :desc).limit(20).offset(first).to_a'
        if target_code in ctrl_text:
            db_paged_impl = '''      stmt = Db.prepare("SELECT id, body, created_at, title, updated_at FROM articles ORDER BY created_at DESC, id DESC LIMIT 20 OFFSET " + first.to_s)
      results = []
      while Db.step?(stmt)
        results << Article.from_stmt(stmt)
      end
      Db.finalize(stmt)
      __comments_ids = results.map { |a| a.id }
      if __comments_ids.length > 0
        __comments_stmt = Db.prepare("SELECT id, article_id, body, commenter, created_at, updated_at FROM comments WHERE article_id IN (" + Db.escape_int_list(__comments_ids) + ")")
        __comments_loaded = []
        while Db.step?(__comments_stmt)
          __comments_loaded << Comment.from_stmt(__comments_stmt)
        end
        Db.finalize(__comments_stmt)
        results.each { |a| __comments_group = []
        __comments_loaded.each { |r| __comments_group << r if r.article_id == a.id }
        a._preload_comments(__comments_group) }
      end
      @articles = results'''
            ctrl_text = replace_once(ctrl_text, target_code, db_paged_impl)
            ctrl_path.write_text(ctrl_text)
            changes['app/controllers/articles_controller.rb'] = {
                'before': hashlib.sha256(before_ctrl).hexdigest(),
                'after': hashlib.sha256(ctrl_path.read_bytes()).hexdigest()
            }
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
        rewritten = replace_once(before.decode(), anchor, probe + anchor + branch)
        # Tep omits its default HTML Content-Type on bodyless redirects.
        # Rails sends text/html for the same redirect status and Location.
        location = '    res.headers["Location"] = controller.location unless controller.location.nil?'
        fixed = location + '''
    if controller.status >= 300 && controller.status < 400 && !controller.location.nil?
      res.headers["Content-Type"] = "text/html; charset=utf-8"
    end'''
        rewritten = replace_once(rewritten, location, fixed)
        path.write_text(rewritten)
        changes['main.rb'] = {'before': hashlib.sha256(before).hexdigest(),
                              'after': hashlib.sha256(path.read_bytes()).hexdigest()}
    (output / 'benchmark-emission.json').write_text(json.dumps({
        'target': target, 'patches': changes,
        'purpose': 'Equal per-connection SQLite pragmas, unmeasured runtime probe, Rails-compatible hidden-field attributes, native DB pagination support'
    }, indent=2) + '\n')

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('output'); p.add_argument('target', choices=['ruby', 'jruby', 'spinel'])
    instrument(**vars(p.parse_args()))
