#!/usr/bin/env python3
"""Semantic checks run outside timed intervals. No known gap is called passed."""
import datetime as dt
import html.parser
import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('legacy_comparison', ROOT / 'scripts/compare.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
HttpClient = legacy.HttpClient
READS = ['/articles', '/articles/1', '/articles/new', '/articles.json', '/articles/1.json']
PRAGMAS = {'journal_mode': 'wal', 'synchronous': '1', 'foreign_keys': '1',
           'busy_timeout': '5000', 'cache_size': '-65536', 'mmap_size': '268435456'}

def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=parsed.tzinfo or dt.timezone.utc).astimezone(dt.timezone.utc).isoformat()

class DOM(html.parser.HTMLParser):
    """Compare main/body nodes. Ignore only explicitly nondeterministic attributes."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        for key in list(values):
            if key == 'nonce' or key == 'signed-stream-name' or (
                key == 'value' and values.get('name') == 'authenticity_token') or (
                key == 'content' and values.get('name') == 'csrf-token'):
                values[key] = '<dynamic>'
        self.events.append(['start', tag, sorted(values.items())])
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
    def handle_endtag(self, tag):
        if tag not in ('input', 'meta', 'img', 'br', 'hr', 'link'):
            self.events.append(['end', tag])
    def handle_data(self, text):
        if text.strip():
            self.events.append(['text', re.sub(r'\s+', ' ', text).strip()])

def relative(value):
    p = urlsplit(value)
    return p.path + (('?' + p.query) if p.query else '') + (('#' + p.fragment) if p.fragment else '')

def json_value(value, key=None):
    if isinstance(value, dict):
        return {k: json_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [json_value(v) for v in value]
    if isinstance(value, str) and key in ('created_at', 'updated_at'):
        return timestamp(value)  # Preserve the instant, not a blanket timestamp mask.
    if isinstance(value, str) and key == 'url':
        return relative(value)
    return value

def canonical(response, expect_json=False):
    media = response['content_type'].split(';')[0].strip().lower()
    if expect_json and media != 'application/json':
        raise ValueError(f'Expected application/json, got {media}')
    body = response['body']
    if media == 'application/json':
        body = json_value(json.loads(body))
    elif media == 'text/html':
        match = re.search(r'<main\b[^>]*>(.*?)</main>', body, re.S | re.I)
        if not match:
            match = re.search(r'<body\b[^>]*>(.*?)</body>', body, re.S | re.I)
        parser = DOM(); parser.feed(match.group(1) if match else body)
        body = parser.events
    return {'status': response['status'], 'media_type': media,
            'location': relative(response['location']) if response.get('location') else None, 'body': body}

def snapshot(path):
    with sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return {table: [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY id')]
                for table in ('articles', 'comments')}

def canonical_db(rows, initial, start, end):
    result = {}
    for table, records in rows.items():
        originals = {r['id']: r for r in initial[table]}
        result[table] = []
        for record in records:
            r = record.copy(); old = originals.get(r['id'])
            for key in ('created_at', 'updated_at'):
                instant = timestamp(r[key])
                unchanged = old and instant == timestamp(old[key])
                if unchanged:
                    r[key] = instant
                else:
                    t = dt.datetime.fromisoformat(instant).timestamp()
                    if not start - 5 <= t <= end + 5:
                        raise ValueError(f'{table}.{key} outside operation time interval')
                    r[key] = '<operation-time>'
            result[table].append(r)
    return result

def check_probe(probe, target):
    if probe['status'] != 200:
        raise ValueError('Serving-process runtime probe failed')
    info = json.loads(probe['body'])
    expected_runtime = {'cruby': 'ruby', 'jruby': 'jruby', 'spinel': 'spinel'}[target['runtime']]
    if info['runtime'] != expected_runtime or info['jit'] != target['jit']:
        raise ValueError('Unexpected serving runtime/JIT')
    if info['pragmas'] != PRAGMAS:
        raise ValueError(f'PRAGMA mismatch: {info["pragmas"]}')
    if target['runtime'] == 'cruby' and info.get('yjit_enabled') != (target['jit'] == 'on'):
        raise ValueError('YJIT state mismatch')
    if target['runtime'] == 'jruby':
        if info.get('compile_mode') != ('OFF' if target['jit'] == 'off' else 'JIT'):
            raise ValueError('JRuby compile mode mismatch')
        args = info.get('jvm_args', [])
        if not info.get('jvm_compiler') or any(a in ('-Xint', '-XX:-UseCompiler') for a in args):
            raise ValueError('JVM JIT must remain enabled')
    return info

def capture(base_url, database, target):
    import time
    client = HttpClient(base_url)
    initial = snapshot(database)
    result = {'cases': {}, 'runtime': check_probe(client.request('GET', '/__bench/runtime'), target)}
    for path in READS:
        response = client.request('GET', path)
        item = {'raw': response}
        try:
            if response['status'] != 200:
                raise ValueError(f'Unexpected HTTP {response["status"]}')
            item['canonical'] = canonical(response, path.endswith('.json'))
            item['status'] = 'passed'
        except (ValueError, TypeError) as e:
            item.update(status='failed', reason=str(e))
        result['cases'][path] = item
    start = time.time()
    def step(name, method, path, fields, statuses, expect_json=False):
        before = snapshot(database)
        if fields is not None:
            fields = dict(fields, authenticity_token=client.last_csrf_token)
        response = client.request(method, path, fields)
        after = snapshot(database)
        item = {'raw': response, 'database': after}
        try:
            if response['status'] not in statuses:
                raise ValueError(f'Unexpected HTTP {response["status"]}; expected {statuses}')
            if name.endswith('invalid') and before != after:
                raise ValueError('Invalid write changed database')
            item['canonical'] = canonical(response, expect_json)
            item['canonical_db'] = canonical_db(after, initial, start, time.time())
            item['status'] = 'passed'
        except (ValueError, TypeError) as e:
            item.update(status='failed', reason=str(e))
        result['cases'][name] = item
        return response
    client.request('GET', '/articles/new')
    step('create_invalid', 'POST', '/articles', {'article[title]': '', 'article[body]': ''}, [422])
    created = step('create', 'POST', '/articles', {'article[title]': 'Bench created',
                    'article[body]': 'A sufficiently long created body.'}, [302, 303])
    path = relative(created.get('location') or '')
    if re.fullmatch(r'/articles/\d+', path):
        client.request('GET', path + '/edit')
        step('update_invalid', 'PATCH', path, {'article[title]': '', 'article[body]': ''}, [422])
        step('update', 'PATCH', path, {'article[title]': 'Bench updated',
             'article[body]': 'A sufficiently long updated body.'}, [302, 303])
        client.request('GET', path)
        step('comment_create', 'POST', path + '/comments',
             {'comment[commenter]': 'Bench reader', 'comment[body]': 'Bench comment'}, [302, 303])
        rows = snapshot(database)['comments']
        comments = [r for r in rows if r['commenter'] == 'Bench reader']
        if len(comments) == 1:
            step('comment_delete', 'DELETE', path + '/comments/' + str(comments[0]['id']), {}, [302, 303])
        else:
            result['cases']['comment_create'].update(status='failed', reason='Comment not inserted exactly once')
        step('delete', 'DELETE', path, {}, [302, 303])
    else:
        result['cases']['create'].update(status='failed', reason='Missing created-resource location')
    client.request('GET', '/articles/new')
    step('json_create_invalid', 'POST', '/articles.json', {'article[title]': '', 'article[body]': ''}, [422], True)
    # This is a separate capability check; a write gap does not turn a GET into a match.
    response = client.request('POST', '/articles', {'article[title]': 'Rejected CSRF',
               'article[body]': 'This request must not persist.', 'authenticity_token': 'invalid'})
    result['cases']['csrf_invalid'] = {'status': 'passed' if response['status'] == 422 and
        not any(r['title'] == 'Rejected CSRF' for r in snapshot(database)['articles']) else 'failed',
        'raw': response, 'reason': 'Invalid CSRF must be rejected without a write'}
    return result

def compare(reference, candidate):
    checks = {}
    for name, expected in reference['cases'].items():
        actual = candidate['cases'].get(name)
        if expected['status'] != 'passed':
            checks[name] = {'status': 'excluded', 'reason': 'Reference did not pass its own contract'}
        elif not actual or actual['status'] != 'passed':
            checks[name] = {'status': 'failed', 'reason': (actual or {}).get('reason', 'Missing case')}
        elif any(expected.get(k) != actual.get(k) for k in ('canonical', 'canonical_db')):
            checks[name] = {'status': 'failed', 'reason': 'Response or persisted database differs'}
        else:
            checks[name] = {'status': 'passed'}
    return {'cases': checks, 'eligible_endpoints': [p for p in READS if checks.get(p, {}).get('status') == 'passed'],
            'complete': all(v['status'] == 'passed' for v in checks.values())}
