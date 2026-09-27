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
const NUM_ENTITIES = parseInt(__ENV.NUM_ENTITIES || '1000', 10);

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
    // Fail if client-side executor saturates and cannot keep up with target arrival rate
    'dropped_iterations': ['count==0'],
    'http_req_failed': ['rate<0.001'], // 99.9% success required
    'http_req_duration': ['p(95)<100'], // Customizable SLA
  },
  discardResponseBodies: false,
};

export default function () {
  // Deterministic VU-partitioned targeting to prevent artificial DB lock contention
  const targetId = 1 + ((__VU * 31 + __ITER) % NUM_ENTITIES);
  const startTime = Date.now();

  const res = http.get(`${TARGET_URL}/articles/${targetId}`, {
    headers: { 'Accept': 'text/html' },
    tags: { name: 'GetArticle' },
  });

  const duration = Date.now() - startTime;
  operationDuration.add(duration);
  operationCount.add(1);

  check(res, {
    'status is 200': (r) => r.status === 200,
    'body has content': (r) => r.body && r.body.length > 0,
  });
}
