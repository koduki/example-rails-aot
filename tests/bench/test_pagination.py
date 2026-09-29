#!/usr/bin/env python3
"""Tests for ActiveRecord DB-level LIMIT/OFFSET pagination, query plan, and tie-breaking (#47)."""
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.bench import emit


class PaginationSqlAndPlanTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            body TEXT,
            created_at DATETIME NOT NULL,
            title VARCHAR,
            updated_at DATETIME NOT NULL
        );
        CREATE TABLE comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL,
            body TEXT,
            commenter VARCHAR,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(article_id) REFERENCES articles(id)
        );
        CREATE INDEX index_comments_on_article_id ON comments(article_id);
        """)

        # Seed 50 articles.
        # Articles 1..10 share identical created_at to verify tie-breaking.
        # Articles 11..50 have distinct timestamps.
        articles_data = []
        comments_data = []
        for n in range(1, 51):
            if n <= 10:
                stamp = "2026-01-01 12:00:00.000000"
            else:
                stamp = f"2026-01-01 12:{n % 60:02d}:00.000000"
            articles_data.append((n, f"Body {n}", stamp, f"Article {n}", stamp))
            comments_data.append((n, n, f"Comment for {n}", f"Reader {n}", stamp, stamp))

        self.conn.executemany("INSERT INTO articles VALUES (?, ?, ?, ?, ?)", articles_data)
        self.conn.executemany("INSERT INTO comments VALUES (?, ?, ?, ?, ?, ?)", comments_data)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_explain_query_plan_baseline_and_composite(self):
        """Record and verify EXPLAIN QUERY PLAN with and without composite index."""
        cur = self.conn.cursor()
        cur.execute(
            "EXPLAIN QUERY PLAN SELECT id, body, created_at, title, updated_at "
            "FROM articles ORDER BY created_at DESC, id DESC LIMIT 20 OFFSET 0"
        )
        plan_baseline = [r[3] for r in cur.fetchall()]
        # Without composite index, SQLite scans articles and uses temp B-Tree for ordering
        self.assertTrue(any("SCAN articles" in step for step in plan_baseline))
        self.assertTrue(any("USE TEMP B-TREE FOR ORDER BY" in step for step in plan_baseline))

        # Comments lookup uses index on article_id
        cur.execute(
            "EXPLAIN QUERY PLAN SELECT id, article_id, body, commenter, created_at, updated_at "
            "FROM comments WHERE article_id IN (1, 2, 3)"
        )
        plan_comments = [r[3] for r in cur.fetchall()]
        self.assertTrue(any("index_comments_on_article_id" in step for step in plan_comments))

        # Adding composite index on (created_at DESC, id DESC) replaces temp B-tree with direct index scan
        self.conn.execute("CREATE INDEX index_articles_on_created_at_and_id ON articles(created_at DESC, id DESC)")
        cur.execute(
            "EXPLAIN QUERY PLAN SELECT id, body, created_at, title, updated_at "
            "FROM articles ORDER BY created_at DESC, id DESC LIMIT 20 OFFSET 0"
        )
        plan_composite = [r[3] for r in cur.fetchall()]
        self.assertTrue(any("index_articles_on_created_at_and_id" in step for step in plan_composite))
        self.assertFalse(any("USE TEMP B-TREE" in step for step in plan_composite))

    def test_db_paged_limit_offset_row_counts_and_sql_counts(self):
        """Verify DB-level pagination retrieves only 20 articles and comments for those 20 articles."""
        offset = 0
        limit = 20
        # Query 1: articles
        articles = self.conn.execute(
            "SELECT id, body, created_at, title, updated_at FROM articles "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        self.assertEqual(len(articles), 20)

        # Query 2: comments only for fetched article IDs
        article_ids = [a["id"] for a in articles]
        placeholders = ",".join("?" * len(article_ids))
        comments = self.conn.execute(
            f"SELECT id, article_id, body, commenter, created_at, updated_at "
            f"FROM comments WHERE article_id IN ({placeholders})",
            article_ids
        ).fetchall()
        # Exactly 20 comments fetched (one per article), NOT 50
        self.assertEqual(len(comments), 20)

        # Page 2: remaining 20
        offset_p2 = 20
        articles_p2 = self.conn.execute(
            "SELECT id, body, created_at, title, updated_at FROM articles "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset_p2)
        ).fetchall()
        self.assertEqual(len(articles_p2), 20)

        # Page 3: remaining 10
        offset_p3 = 40
        articles_p3 = self.conn.execute(
            "SELECT id, body, created_at, title, updated_at FROM articles "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset_p3)
        ).fetchall()
        self.assertEqual(len(articles_p3), 10)

        # Page 4: out of bounds -> 0 articles, 0 comments query needed
        offset_p4 = 60
        articles_p4 = self.conn.execute(
            "SELECT id, body, created_at, title, updated_at FROM articles "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset_p4)
        ).fetchall()
        self.assertEqual(len(articles_p4), 0)

    def test_tie_breaking_order_is_deterministic(self):
        """Articles with identical created_at must order deterministically by id DESC."""
        # Articles 1..10 share the same created_at
        rows = self.conn.execute(
            "SELECT id, title FROM articles "
            "WHERE created_at = '2026-01-01 12:00:00.000000' "
            "ORDER BY created_at DESC, id DESC"
        ).fetchall()
        ids = [r["id"] for r in rows]
        # Must strictly be [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
        self.assertEqual(ids, [10, 9, 8, 7, 6, 5, 4, 3, 2, 1])


class EmitInstrumentationTests(unittest.TestCase):
    def test_emit_instruments_db_paged_in_controller(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            (out / "runtime").mkdir()
            (out / "runtime/action_view").mkdir()
            (out / "app/controllers").mkdir(parents=True)

            # Minimal files expected by emit.py
            (out / "runtime/db.rb").write_bytes(b'"PRAGMA synchronous=NORMAL",\n')
            (out / "runtime/action_view/view_helpers.rb").write_bytes(
                b'<input type="hidden" name="authenticity_token"\n<input type="hidden" name="_method"\n'
            )
            (out / "runtime/thread_state.rb").write_bytes(
                b'<input type="hidden" name="authenticity_token"\n'
            )
            (out / "main.rb").write_bytes(
                b'  def self.dispatch(req, res)\n    res.headers["Location"] = controller.location unless controller.location.nil?\n'
            )

            ctrl_code = b'''class ArticlesController < ApplicationController
  def index
    page = @params.fetch("page", 1).to_i
    first = (page - 1) * 20
    pagination = @params.fetch("pagination", "app-sliced")
    if pagination == "db-paged"
      @articles = Article.includes(:comments).order(created_at: :desc, id: :desc).limit(20).offset(first).to_a
    else
      @articles = []
    end
  end
end\n'''
            (out / "app/controllers/articles_controller.rb").write_bytes(ctrl_code)

            emit.instrument(out, "spinel")

            patched = (out / "app/controllers/articles_controller.rb").read_text()
            self.assertIn('LIMIT 20 OFFSET " + first.to_s', patched)
            self.assertIn("Db.prepare", patched)
            self.assertIn("a._preload_comments", patched)
            self.assertNotIn("Article.includes(:comments)", patched)


if __name__ == "__main__":
    unittest.main()
