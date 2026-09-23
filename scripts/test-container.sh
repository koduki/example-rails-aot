#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

IMAGE_NAME="example-rails-aot:latest"
CONTAINER_PORT=34000
VOLUME_NAME="blog_test_storage_$(date +%s)"
REPORT_DIR="$ROOT/reports/container"
mkdir -p "$REPORT_DIR" "$ROOT/artifacts"

cleanup() {
  echo "[CONTAINER] Cleaning up containers and test volume..."
  docker stop blog-test-1 blog-test-2 2>/dev/null || true
  docker rm -f blog-test-1 blog-test-2 2>/dev/null || true
  docker volume rm "$VOLUME_NAME" 2>/dev/null || true
}
trap cleanup EXIT

echo "[CONTAINER] Step 1: Building container image ($IMAGE_NAME)..."
docker build -t "$IMAGE_NAME" . 2>&1 | tee "$REPORT_DIR/docker-build.log"

echo "[CONTAINER] Step 2: Verifying runtime container does not contain Ruby or Spinel..."
docker run --rm --entrypoint /bin/sh "$IMAGE_NAME" -c '
  if command -v ruby || command -v rails || command -v spinel || command -v spin; then
    echo "ERROR: Runtime container must not include Ruby, Rails, or Spinel!"
    exit 1
  fi
  echo "Verification passed: No Ruby/Rails/Spinel found in runtime image."
'

echo "[CONTAINER] Step 3: Starting container with volume $VOLUME_NAME..."
docker volume create "$VOLUME_NAME"
docker run -d --name blog-test-1 -p "$CONTAINER_PORT:3000" -v "$VOLUME_NAME:/app/storage" "$IMAGE_NAME"

echo "[CONTAINER] Waiting for container to be healthy..."
for _ in $(seq 1 30); do
  if curl --fail --silent "http://127.0.0.1:$CONTAINER_PORT/articles" > /dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "[CONTAINER] Step 4: Creating article via HTTP in container..."
# Use python helper or curl to post article
python3 -c "
import http.client, urllib.parse, html.parser
from http.cookies import SimpleCookie

class TokenParser(html.parser.HTMLParser):
    token = ''
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('name') == 'authenticity_token':
            self.token = attrs.get('value', '')

conn = http.client.HTTPConnection('127.0.0.1', $CONTAINER_PORT, timeout=10)
conn.request('GET', '/articles/new')
resp = conn.getresponse()
cookies = {}
for k, v in resp.getheaders():
    if k.lower() == 'set-cookie':
        jar = SimpleCookie(v)
        cookies.update({name: item.value for name, item in jar.items()})

html_data = resp.read().decode()
parser = TokenParser()
parser.feed(html_data)

headers = {
    'Content-Type': 'application/x-www-form-urlencoded',
    'Origin': 'http://127.0.0.1:$CONTAINER_PORT',
    'Referer': 'http://127.0.0.1:$CONTAINER_PORT/articles/new'
}
if cookies:
    headers['Cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items())

body = urllib.parse.urlencode({
    'article[title]': 'Container Persistence Article',
    'article[body]': 'Verified across container recreation with volume mount.',
    'authenticity_token': parser.token
})
conn.request('POST', '/articles', body, headers)
post_resp = conn.getresponse()
assert post_resp.status in (302, 303), f'Expected redirect, got {post_resp.status} with body: {post_resp.read().decode()[:500]}'
print('Created article in container successfully.')
"

echo "[CONTAINER] Step 5: Stopping container 1 and recreating container 2 with the same volume..."
docker stop blog-test-1
docker rm blog-test-1

docker run -d --name blog-test-2 -p "$CONTAINER_PORT:3000" -v "$VOLUME_NAME:/app/storage" "$IMAGE_NAME"

echo "[CONTAINER] Waiting for container 2 to be healthy..."
for _ in $(seq 1 30); do
  if curl --fail --silent "http://127.0.0.1:$CONTAINER_PORT/articles" > /dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "[CONTAINER] Step 6: Verifying article persisted across container recreation..."
PERSISTED=$(curl --silent "http://127.0.0.1:$CONTAINER_PORT/articles")
if ! echo "$PERSISTED" | grep -q "Container Persistence Article"; then
  echo "ERROR: Data failed to persist in volume across container recreation!"
  exit 1
fi
echo "[CONTAINER] Persistence across container recreation verified!"

echo "[CONTAINER] Step 7: Saving container image archive to artifacts..."
docker save "$IMAGE_NAME" | gzip > "$ROOT/artifacts/blog-container-image.tar.gz"
test -s "$ROOT/artifacts/blog-container-image.tar.gz"
echo "[CONTAINER] Image saved to artifacts/blog-container-image.tar.gz ($(du -h "$ROOT/artifacts/blog-container-image.tar.gz" | cut -f1))"

echo "[CONTAINER] Step 8: Testing docker load from saved archive..."
docker stop blog-test-2 2>/dev/null || true
docker rm -f blog-test-2 2>/dev/null || true
docker rmi -f "$IMAGE_NAME"
docker load < "$ROOT/artifacts/blog-container-image.tar.gz"
docker inspect "$IMAGE_NAME" > /dev/null
echo "[CONTAINER] Docker load verification succeeded!"

echo "[CONTAINER] All container verification tests PASSED!"
