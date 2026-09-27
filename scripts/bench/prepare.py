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

def prepare(path, count=3, comments_per_article=1):
    path = Path(path)
    if path.exists():
        raise ValueError('Refusing to replace an existing database')
    if count < 1:
        raise ValueError('count must be positive')
    if comments_per_article < 0:
        raise ValueError('comments_per_article must be non-negative')
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.executescript(SCHEMA)
        articles_data = []
        comments_data = []
        comment_id = 1
        for n in range(1, count + 1):
            stamp = f'2025-01-01 00:00:{n % 60:02d}.000000'
            articles_data.append((n, f'Benchmark article body number {n}. 日本語と <escaping> & data.', stamp, f'Article {n}', stamp))
            for c in range(comments_per_article):
                comments_data.append((comment_id, n, f'Comment body {comment_id} for article {n}', f'Reader {comment_id}', stamp, stamp))
                comment_id += 1
        
        db.executemany('INSERT INTO articles VALUES (?, ?, ?, ?, ?)', articles_data)
        if comments_data:
            db.executemany('INSERT INTO comments VALUES (?, ?, ?, ?, ?, ?)', comments_data)
        db.commit()
        db.execute('PRAGMA journal_mode=WAL')
    finally:
        db.close()
    return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'articles': count, 'comments': len(comments_data)}

def fetch_page(path, page=1, per_page=20):
    """Retrieve a 20-item paginated subset ordered by created_at desc."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'Database not found: {path}')
    if page < 1 or per_page < 1:
        raise ValueError('page and per_page must be positive integers')
    offset = (page - 1) * per_page
    db = sqlite3.connect(f'file:{path.resolve()}?mode=ro', uri=True)
    try:
        db.row_factory = sqlite3.Row
        total = db.execute('SELECT count(*) FROM articles').fetchone()[0]
        rows = db.execute(
            'SELECT a.id, a.title, a.body, a.created_at, count(c.id) as comments_count '
            'FROM articles a LEFT JOIN comments c ON c.article_id = a.id '
            'GROUP BY a.id ORDER BY a.created_at DESC, a.id DESC LIMIT ? OFFSET ?',
            (per_page, offset)
        ).fetchall()
        return {
            'page': page,
            'per_page': per_page,
            'total_items': total,
            'total_pages': (total + per_page - 1) // per_page,
            'items': [dict(r) for r in rows]
        }
    finally:
        db.close()

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('path', help='Target database path')
    p.add_argument('--count', type=int, default=3, help='Number of articles to generate (default: 3, large: 1000)')
    p.add_argument('--comments-per-article', type=int, default=1, help='Number of comments per article')
    p.add_argument('--verify-page', type=int, help='Verify pagination query for specified page')
    args = p.parse_args()
    if args.verify_page:
        print(json.dumps(fetch_page(args.path, page=args.verify_page), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(prepare(args.path, count=args.count, comments_per_article=args.comments_per_article), indent=2))
