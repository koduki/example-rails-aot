"""HTTP create/validation/read and persistence check against the native server."""
import html.parser
import http.client
import json
import sqlite3
import sys
import urllib.parse
from http.cookies import SimpleCookie

base, database, phase = sys.argv[1:]
url = urllib.parse.urlsplit(base)
cookies = {}

def request(method, path, fields=None):
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=10)
    headers = {'Cookie': '; '.join(f'{k}={v}' for k, v in cookies.items()), 'Origin': base, 'Referer': base + '/articles/new'}
    body = None
    if fields is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        body = urllib.parse.urlencode(fields)
    connection.request(method, path, body, headers)
    response = connection.getresponse()
    status, location = response.status, response.getheader('Location')
    for key, value in response.getheaders():
        if key.lower() == 'set-cookie':
            jar = SimpleCookie(value)
            cookies.update({name: item.value for name, item in jar.items()})
    text = response.read().decode()
    connection.close()
    return status, location, text

class TokenParser(html.parser.HTMLParser):
    token = ''
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('name') == 'authenticity_token':
            self.token = attrs.get('value', '')

def article_row():
    with sqlite3.connect(database) as db:
        return db.execute('SELECT id, title, body FROM articles WHERE title = ?', ('AOT persistence check',)).fetchone()

status, _, text = request('GET', '/articles')
assert status == 200 and 'Articles' in text, (status, text[:500])
if phase == 'create':
    status, _, form = request('GET', '/articles/new')
    assert status == 200, status
    parser = TokenParser()
    parser.feed(form)
    status, _, body = request('POST', '/articles', {'article[title]': '', 'article[body]': '', 'authenticity_token': parser.token})
    assert status == 422, (status, body[:500])
    status, location, body = request('POST', '/articles', {'article[title]': 'AOT persistence check', 'article[body]': 'Persist this record across a native server restart.', 'authenticity_token': parser.token})
    assert status in (302, 303) and location, (status, body[:500])
row = article_row()
assert row is not None, 'HTTP-created article is missing from SQLite'
status, _, text = request('GET', f'/articles/{row[0]}')
assert status == 200 and row[1] in text and row[2] in text, (status, text[:500])
print(json.dumps({'phase': phase, 'http': status, 'record': row, 'result': 'pass'}))
