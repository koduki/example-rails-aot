import http from 'k6/http';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';

// Custom metrics to distinguish business operations from transport HTTP requests
export const operationCount = new Counter('bench_operations');
export const operationDuration = new Trend('bench_operation_duration_ms', true);

// Parameterization via environment variables
const TARGET_URL = __ENV.TARGET_URL || 'http://127.0.0.1:3000';
const TARGET_RPS = parseInt(__ENV.TARGET_RPS || '1000', 10);
const DURATION = __ENV.DURATION || '30s';
const PRE_ALLOCATED_VUS = parseInt(__ENV.PRE_ALLOCATED_VUS || '50', 10);
const MAX_VUS = parseInt(__ENV.MAX_VUS || '200', 10);
// The repository fixture always contains article 1; override for another prepared fixture.
const ARTICLE_ID = parseInt(__ENV.ARTICLE_ID || '1', 10);

export const options = {
  scenarios: {
    open_arrival_rate: {
      executor: 'constant-arrival-rate',
      rate: TARGET_RPS,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: PRE_ALLOCATED_VUS,
      maxVUs: MAX_VUS,
    },
  },
  thresholds: {
    // Drops can also result from long server latency exhausting the configured VUs.
    'dropped_iterations': ['count==0'],
    'http_req_failed': ['rate==0'],
    'checks': ['rate==1'],
  },
  discardResponseBodies: false,
};

export default function () {
  const res = http.get(`${TARGET_URL.replace(/\/$/, '')}/articles/${ARTICLE_ID}`, {
    headers: { 'Accept': 'text/html' },
    tags: { name: 'GetArticle' },
  });

  operationDuration.add(res.timings.duration);
  const valid = check(res, {
    'status is 200': (r) => r.status === 200,
    'body has content': (r) => r.body && r.body.length > 0,
  });
  if (valid) operationCount.add(1);
}
