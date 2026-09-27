import http from 'k6/http';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import exec from 'k6/execution';

const checkFailures = new Counter('check_failures');
const successfulOperations = new Counter('successful_operations');
const totalOperations = new Counter('total_operations');
const readOperations = new Counter('read_operations');
const writeOperations = new Counter('write_operations');
const readDuration = new Trend('read_duration_ms', true);
const writeDuration = new Trend('write_duration_ms', true);

const rate = Number(__ENV.RATE || 20); // offered operations per second
const duration = __ENV.DURATION || '30s';
const preAllocatedVUs = Number(__ENV.PRE_ALLOCATED_VUS || 10);
const maxVUs = Number(__ENV.MAX_VUS || 50);
const scenarioType = __ENV.SCENARIO || 'mix'; // 'mix' (90/10), 'read', 'update', 'create_delete'
const numArticles = Number(__ENV.NUM_ARTICLES || 100);
const baseUrl = __ENV.TARGET_URL || 'http://127.0.0.1:3000';
const timeout = __ENV.TIMEOUT || '5s';

export const options = {
  scenarios: {
    crud_arrival_scenario: {
      executor: 'constant-arrival-rate',
      rate: rate,
      timeUnit: '1s',
      duration: duration,
      preAllocatedVUs: preAllocatedVUs,
      maxVUs: maxVUs,
    },
  },
  discardResponseBodies: false,
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

function extractCsrfToken(html) {
  if (!html) return '';
  const match = html.match(/<input[^>]+name=["']authenticity_token["'][^>]+value=["']([^"']+)["']/i) ||
                html.match(/<meta[^>]+name=["']csrf-token["'][^>]+content=["']([^"']+)["']/i);
  return match ? match[1] : '';
}

function extractIdFromLocation(location) {
  if (!location) return null;
  const match = location.match(/\/articles\/(\d+)/);
  return match ? parseInt(match[1], 10) : null;
}

export default function () {
  totalOperations.add(1);
  // __ITER resets for each VU; at saturation that turns newly allocated VUs
  // into writes and silently changes the promised 90/10 workload.
  const iteration = exec.scenario.iterationInTest;
  const targetId = 1 + ((iteration * 31) % numArticles);

  let isWrite = false;
  if (scenarioType === 'mix') {
    // 90% read, 10% update
    isWrite = (iteration % 10 === 0);
  } else if (scenarioType === 'update' || scenarioType === 'create_delete') {
    isWrite = true;
  }

  const defaultHeaders = {
    'Accept': 'text/html,application/xhtml+xml',
    'Connection': 'keep-alive',
    'User-Agent': 'example-rails-aot-bench-k6-crud',
  };

  if (!isWrite) {
    // READ OPERATION: GET /articles or GET /articles/:id
    readOperations.add(1);
    const readUrl = (iteration % 2 === 0)
      ? `${baseUrl}/articles`
      : `${baseUrl}/articles/${targetId}`;

    const start = Date.now();
    const res = http.get(readUrl, { headers: defaultHeaders, timeout: timeout, redirects: 5 });
    const elapsed = Date.now() - start;
    readDuration.add(elapsed);

    const ok = check(res, {
      'read status 200': (r) => r.status === 200,
      'read body non-empty': (r) => r.body && r.body.length > 0,
    });

    if (ok) {
      successfulOperations.add(1);
    } else {
      checkFailures.add(1);
    }
  } else {
    // WRITE OPERATION
    writeOperations.add(1);
    const start = Date.now();

    if (scenarioType === 'create_delete') {
      // Balanced Create then Delete to keep database bounded
      const newPageRes = http.get(`${baseUrl}/articles/new`, { headers: defaultHeaders, timeout: timeout });
      const token = extractCsrfToken(newPageRes.body);

      const createPayload = {
        'authenticity_token': token,
        'article[title]': `Ephemeral Article Iter ${iteration}`,
        'article[body]': 'Ephemeral body content for bounded CRUD load verification.',
      };

      const createRes = http.post(`${baseUrl}/articles`, createPayload, {
        headers: defaultHeaders,
        timeout: timeout,
        redirects: 0,
      });

      const createdId = extractIdFromLocation(createRes.headers.Location || createRes.headers.location);
      let deleteOk = false;

      if (createdId) {
        const deletePayload = {
          'authenticity_token': token,
          '_method': 'delete',
        };
        const delRes = http.post(`${baseUrl}/articles/${createdId}`, deletePayload, {
          headers: defaultHeaders,
          timeout: timeout,
          redirects: 0,
        });
        deleteOk = delRes.status === 302 || delRes.status === 303;
      }

      const elapsed = Date.now() - start;
      writeDuration.add(elapsed);

      const ok = check(createRes, {
        'create redirect with id': (r) => (r.status === 302 || r.status === 303) && Boolean(createdId),
        'delete succeeded': () => deleteOk,
      });

      if (ok) {
        successfulOperations.add(1);
      } else {
        checkFailures.add(1);
      }
    } else {
      // IN-PLACE UPDATE (PATCH /articles/:id)
      // 1. Fetch edit form to get current authenticity_token and establish session
      const editRes = http.get(`${baseUrl}/articles/${targetId}/edit`, {
        headers: defaultHeaders,
        timeout: timeout,
      });
      const token = extractCsrfToken(editRes.body);

      // 2. Submit PATCH
      const updatePayload = {
        'authenticity_token': token,
        '_method': 'patch',
        'article[title]': `Article ${targetId} (iteration ${iteration})`,
        'article[body]': `Updated body for article ${targetId} at iteration ${iteration}. Preserves bounded storage.`,
      };

      const patchRes = http.post(`${baseUrl}/articles/${targetId}`, updatePayload, {
        headers: defaultHeaders,
        timeout: timeout,
        redirects: 0,
      });

      const elapsed = Date.now() - start;
      writeDuration.add(elapsed);

      const ok = check(patchRes, {
        'update redirects to article': (r) => (r.status === 302 || r.status === 303) &&
          extractIdFromLocation(r.headers.Location || r.headers.location) === targetId,
      });

      if (ok) {
        successfulOperations.add(1);
      } else {
        checkFailures.add(1);
      }
    }
  }
}

export function handleSummary(data) {
  const metrics = data.metrics || {};
  const latency = metrics.http_req_duration ? metrics.http_req_duration.values : {};
  const iterations = metrics.iterations ? metrics.iterations.values : {};
  const dropped = metrics.dropped_iterations ? metrics.dropped_iterations.values : { count: 0 };
  const vus = metrics.vus ? metrics.vus.values : {};
  const reqs = metrics.http_reqs ? metrics.http_reqs.values : {};
  const failed = metrics.http_req_failed ? metrics.http_req_failed.values : {};

  const opsTotal = metrics.total_operations ? metrics.total_operations.values.count : 0;
  const opsSuccess = metrics.successful_operations ? metrics.successful_operations.values.count : 0;
  const readsCount = metrics.read_operations ? metrics.read_operations.values.count : 0;
  const writesCount = metrics.write_operations ? metrics.write_operations.values.count : 0;

  const readDur = metrics.read_duration_ms ? metrics.read_duration_ms.values : {};
  const writeDur = metrics.write_duration_ms ? metrics.write_duration_ms.values : {};

  const testDurationSec = data.state && data.state.testRunDurationMs
    ? (data.state.testRunDurationMs / 1000) : Number.parseInt(duration, 10);

  const parsed = {
    driver: 'k6-crud',
    scenario: scenarioType,
    rate_offered: rate,
    target_url: baseUrl,
    duration_configured: duration,
    elapsed: testDurationSec,
    vus_max_configured: maxVUs,
    vus_preallocated_configured: preAllocatedVUs,
    vus_peak: vus.max || 0,
    iterations_started: (iterations.count || 0) + (dropped.count || 0),
    iterations_completed: iterations.count || 0,
    iterations_dropped: dropped.count || 0,
    requests_total: reqs.count || 0,
    requests_successful: (reqs.count || 0) - (failed.passes || 0),
    requests_failed: failed.passes || 0,
    errors: failed.passes || 0,
    operations: {
      total: opsTotal,
      successful: opsSuccess,
      failed: opsTotal - opsSuccess,
      reads: readsCount,
      writes: writesCount,
      ops_effective_rate: opsTotal / testDurationSec,
      ops_successful_rate: opsSuccess / testDurationSec,
    },
    rps_effective: reqs.rate || 0,
    rps_successful: ((reqs.count || 0) - (failed.passes || 0)) / testDurationSec,
    rps: ((reqs.count || 0) - (failed.passes || 0)) / testDurationSec,
    p95_ms: latency['p(95)'] || 0,
    latency_ms: {
      min: latency.min || 0,
      avg: latency.avg || 0,
      med: latency.med || 0,
      p50: latency.med || 0,
      p90: latency['p(90)'] || 0,
      p95: latency['p(95)'] || 0,
      p99: latency['p(99)'] || 0,
      max: latency.max || 0,
    },
    read_latency_ms: {
      min: readDur.min || 0,
      avg: readDur.avg || 0,
      med: readDur.med || 0,
      p50: readDur.med || 0,
      p95: readDur['p(95)'] || 0,
      p99: readDur['p(99)'] || 0,
      max: readDur.max || 0,
    },
    write_latency_ms: {
      min: writeDur.min || 0,
      avg: writeDur.avg || 0,
      med: writeDur.med || 0,
      p50: writeDur.med || 0,
      p95: writeDur['p(95)'] || 0,
      p99: writeDur['p(99)'] || 0,
      max: writeDur.max || 0,
    },
    client_saturated: (dropped.count || 0) > 0 || (vus.max || 0) >= maxVUs,
    raw: data,
  };

  return {
    [__ENV.SUMMARY_PATH || 'summary.json']: JSON.stringify(parsed, null, 2),
  };
}
