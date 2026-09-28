import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const checkFailures = new Counter('check_failures');
const successfulRequests = new Counter('successful_requests');

const rate = Number(__ENV.RATE || 50);
const duration = __ENV.DURATION || '30s';
const preAllocatedVUs = Number(__ENV.PRE_ALLOCATED_VUS || 10);
const maxVUs = Number(__ENV.MAX_VUS || 50);

export const options = {
  scenarios: __ENV.MODE === 'closed' ? {
    warmup: { executor: 'constant-vus', vus: Number(__ENV.WARMUP_VUS || 32), duration: duration },
  } : {
    open_arrival_reads: {
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

export default function () {
  const url = __ENV.TARGET_URL || 'http://127.0.0.1:3000/articles';
  const isJson = url.includes('.json');
  const timeout = __ENV.TIMEOUT || '5s';

  const params = {
    headers: {
      'Accept': isJson ? 'application/json' : 'text/html,application/xhtml+xml',
      'Connection': 'keep-alive',
      'User-Agent': 'example-rails-aot-bench-k6',
    },
    timeout: timeout,
    redirects: 5,
  };

  const res = http.get(url, params);
  const statusOk = res.status === 200;
  const bodyOk = res.body && res.body.length > 0;
  const contentType = (res.headers['Content-Type'] || res.headers['content-type'] || '').toLowerCase();
  const ctOk = isJson ? contentType.includes('application/json') : contentType.includes('text/html');

  const passed = check(res, {
    'status 200': () => statusOk,
    'non-empty body': () => bodyOk,
    'expected content-type': () => ctOk,
  });

  if (passed) {
    successfulRequests.add(1);
  } else {
    checkFailures.add(1);
  }
}

export function handleSummary(data) {
  // Extract key summary metrics for portable parsing
  const metrics = data.metrics || {};
  const latency = metrics.http_req_duration ? metrics.http_req_duration.values : {};
  const iterations = metrics.iterations ? metrics.iterations.values : {};
  const dropped = metrics.dropped_iterations ? metrics.dropped_iterations.values : { count: 0 };
  const vus = metrics.vus ? metrics.vus.values : {};
  const reqs = metrics.http_reqs ? metrics.http_reqs.values : {};
  const successes = metrics.successful_requests ? metrics.successful_requests.values : { count: 0 };
  const elapsed = data.state && data.state.testRunDurationMs
    ? data.state.testRunDurationMs / 1000 : Number.parseInt(duration, 10);
  const total = reqs.count || 0;
  const successful = successes.count || 0;

  const parsed = {
    driver: 'k6-open-arrival',
    rate_offered: rate,
    target_url: __ENV.TARGET_URL || '',
    duration_configured: duration,
    elapsed: elapsed,
    vus_max_configured: maxVUs,
    vus_preallocated_configured: preAllocatedVUs,
    vus_peak: vus.max || 0,
    iterations_started: (iterations.count || 0) + (dropped.count || 0),
    iterations_completed: iterations.count || 0,
    iterations_dropped: dropped.count || 0,
    requests_total: total,
    requests_successful: successful,
    requests_failed: Math.max(0, total - successful),
    errors: Math.max(0, total - successful),
    rps: successful / elapsed,
    p95_ms: latency['p(95)'] || 0,
    rps_effective: reqs.rate || 0,
    rps_successful: successful / elapsed,
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
    client_saturated: (dropped.count || 0) > 0 || (vus.max || 0) >= maxVUs,
    raw: data,
  };

  return {
    [__ENV.SUMMARY_PATH || 'summary.json']: JSON.stringify(parsed, null, 2),
  };
}
