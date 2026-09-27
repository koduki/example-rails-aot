#!/usr/bin/env python3
"""Derive a benchmark copy. Never mutate blog/ or silently patch business code."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file() and not any(x in path.parts for x in ('log', 'tmp', 'storage', '.bundle')):
            digest.update(str(path.relative_to(root)).encode() + b'\0' + path.read_bytes())
    return digest.hexdigest()

def prepare(destination):
    destination = Path(destination).resolve()
    if destination == ROOT / 'blog' or ROOT / 'blog' in destination.parents:
        raise ValueError('Refusing to overwrite the original application')
    if destination.exists():
        raise ValueError('Destination must not exist (use a fresh build directory)')
    shutil.copytree(ROOT / 'blog', destination,
                    ignore=shutil.ignore_patterns('storage', 'log', 'tmp', '.bundle', 'Gemfile.lock'))
    for directory in ('storage', 'log', 'tmp/pids'):
        (destination / directory).mkdir(parents=True, exist_ok=True)
    for name in ('Gemfile', 'Gemfile.lock'):
        shutil.copyfile(ROOT / 'bench' / name, destination / name)
    for path in [destination / 'config/application.rb', destination / 'db/schema.rb',
                 *destination.glob('db/migrate/*.rb')]:
        text = path.read_text().replace('config.load_defaults 8.1', 'config.load_defaults 8.0')
        text = text.replace('Schema[8.1]', 'Schema[8.0]').replace('Migration[8.1]', 'Migration[8.0]')
        path.write_text(text)
    boot = destination / 'config/boot.rb'
    boot.write_text(boot.read_text().replace('require "bootsnap/setup"', '# Bootsnap disabled for both runtimes'))
    for source, target in [('production.rb', 'config/environments/production.rb'),
                           ('database.yml', 'config/database.yml'), ('puma.rb', 'config/puma.rb')]:
        shutil.copyfile(ROOT / 'bench/runtime' / source, destination / target)
    (destination / 'config/cable.yml').write_text('production:\n  adapter: async\n')
    # Authentication, CSRF, views, callbacks, and controller behavior stay intact.
    manifest = {'schema_version': 1, 'rails': '8.0.5.1', 'source_hash': tree_hash(ROOT / 'blog'),
                'derived_hash': tree_hash(destination), 'changes': [
                    'Rails 8.0 framework compatibility; platform-specific database gems',
                    'production settings: no response cache, inline jobs, async cable, warn logging',
                    'Bootsnap disabled in both Ruby runtimes; JIT explicit',
                    'database location supplied externally; identical SQLite pragmas']}
    (destination / 'benchmark-source.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination')
    print(json.dumps(prepare(parser.parse_args().destination), indent=2))
