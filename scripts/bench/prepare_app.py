#!/usr/bin/env python3
"""Derive a disposable runtime copy of the canonical Rails 8.0 fixture."""
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
    source_gemfile = (ROOT / 'blog/Gemfile').read_text()
    if 'gem "rails", "8.0.5.1"' not in source_gemfile:
        raise ValueError('Canonical fixture must use Rails 8.0.5.1')
    boot = destination / 'config/boot.rb'
    boot.write_text(boot.read_text().replace('require "bootsnap/setup"', '# Bootsnap disabled for both runtimes'))
    for source, target in [('production.rb', 'config/environments/production.rb'),
                           ('database.yml', 'config/database.yml'), ('puma.rb', 'config/puma.rb')]:
        shutil.copyfile(ROOT / 'bench/runtime' / source, destination / target)
    (destination / 'config/cable.yml').write_text('production:\n  adapter: async\n')
    manifest = {'schema_version': 1, 'rails': '8.0.5.1', 'source_hash': tree_hash(ROOT / 'blog'),
                'derived_hash': tree_hash(destination), 'changes': [
                    'Benchmark dependency lockfile includes JRuby JDBC and platform-specific gems',
                    'production settings: no response cache, inline jobs, async cable, warn logging',
                    'Bootsnap disabled in both Ruby runtimes; JIT explicit',
                    'database location supplied externally; identical SQLite pragmas']}
    (destination / 'benchmark-source.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination')
    print(json.dumps(prepare(parser.parse_args().destination), indent=2))
