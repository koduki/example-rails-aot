#!/usr/bin/env python3
"""Deterministic data, isolated from the source app and every other trial."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

SCHEMA = '''
CREATE TABLE articles (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT, created_at DATETIME NOT NULL, title VARCHAR, updated_at DATETIME NOT NULL);
CREATE TABLE comments (id INTEGER PRIMARY KEY AUTOINCREMENT, article_id INTEGER NOT NULL, body TEXT, commenter VARCHAR, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, FOREIGN KEY(article_id) REFERENCES articles(id));
CREATE INDEX index_comments_on_article_id ON comments(article_id);
'''

def prepare(path, count=3):
    path = Path(path)
    if path.exists():
        raise ValueError('Refusing to replace an existing database')
    if count < 1:
        raise ValueError('count must be positive')
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        for n in range(1, count + 1):
            stamp = f'2025-01-01 00:00:{n % 60:02d}.000000'
            db.execute('INSERT INTO articles VALUES (?, ?, ?, ?, ?)',
                       (n, f'Benchmark article body number {n}. 日本語と <escaping> & data.', stamp, f'Article {n}', stamp))
            db.execute('INSERT INTO comments VALUES (?, ?, ?, ?, ?, ?)',
                       (n, n, f'Comment body {n}', f'Reader {n}', stamp, stamp))
        db.commit()
        db.execute('PRAGMA journal_mode=WAL')
    return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'articles': count, 'comments': count}

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('path'); p.add_argument('--count', type=int, default=3)
    print(json.dumps(prepare(**vars(p.parse_args())), indent=2))
