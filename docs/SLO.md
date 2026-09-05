# Service Level Objectives

The orchestrator has two kinds of endpoint with different latency profiles, and the SLOs treat them separately. The read endpoints touch only the orchestrator's own database. A payment (`POST /payments`) runs the whole lifecycle synchronously, including two cross-service calls to the ledger (reserve and capture), so its floor is two network round trips, not a single local query.

## Defined SLOs

| Metric | Target | Measurement |
|--------|--------|-------------|
| Availability | 99.5% | Percentage of non-5xx responses during load test |
| Read p50 | < 100ms | Median for health, get payment, events, outbox |
| Read p95 | < 200ms | 95th percentile for the read endpoints |
| Payment p50 | < 500ms | Median for `POST /payments` (two ledger round trips) |
| Payment p95 | < 750ms | 95th percentile for `POST /payments` |
| Error rate | < 1% | Percentage of 5xx responses |
| Throughput | > 5 req/s sustained | Aggregate under 5 concurrent users |
| Reserve/capture/release invariant | Payment Suspense nets to zero | Suspense balance after the load test |
| Ledger consistency under load | pass | Ledger `GET /audit/verify` and `GET /audit/chain` after the load test |

## Load Test Configuration

- Tool: Locust ([`scripts/loadtest.py`](../scripts/loadtest.py))
- Target: `https://payment-orchestrator-eppidgbmxa-nw.a.run.app`
- Users: 5 concurrent
- Duration: 60 seconds
- Workload mix: create payment (weight 4), get payment (2), get events (1), outbox pending (1), health (1). Each user funds its own customer account in the ledger, then drives 10.00 payments that clear risk and settle.

## Load Test Results

Run on 2026-09-05 against the live Cloud Run deployment, after the fix described below. 589 requests over 60 seconds at 5 concurrent users, of which 249 were payments.

| Metric | Target | Measured | Status |
|--------|--------|----------|--------|
| Availability | 99.5% | 100% (0 of 589 failed) | pass |
| Read p50 | < 100ms | 40 to 51ms | pass |
| Read p95 | < 200ms | 48 to 61ms | pass |
| Payment p50 | < 500ms | 340ms | pass |
| Payment p95 | < 750ms | 430ms | pass |
| Error rate (5xx) | < 1% | 0% | pass |
| Throughput | > 5 req/s | 9.86 req/s | pass |
| Payment Suspense nets to zero | 0.00 | 0.00 | pass |
| Ledger `/audit/verify` and `/audit/chain` | pass | pass | pass |

Per-endpoint medians: health 40ms, get payment 41ms, get events 44ms, outbox pending 51ms, create payment 340ms. The payment path's p99 (1500ms) reflects the occasional request that pays the one-time ledger login when the cached token is first warmed or renewed.

## What the load test found

The first run, before the fix, met availability and error targets with zero failures, but `POST /payments` ran at 1100ms median and 2000ms p95, while every read endpoint sat around 50ms. The payment path was more than twenty times slower than the reads, and the gap was too clean to be network noise.

The cause was the ledger client's lifecycle. The client caches its authentication token and only re-authenticates on a 401, but `get_ledger_client` constructed a brand new client on every request. So each payment started with no token and re-ran the ledger's bcrypt login before it could move money. The ledger deliberately makes that login expensive to resist brute forcing, around 570ms, which is almost exactly the overhead each payment was paying. The read endpoints never call the ledger, which is why they were unaffected.

The fix makes the ledger client a shared singleton, the same pattern the provider router already uses, so the token and the HTTP connection pool are reused across payments and the login happens about once per token lifetime rather than once per payment. After the fix, the payment median fell from 1100ms to 340ms and the p95 from 2000ms to 430ms, and total throughput at the same five users roughly doubled, from 277 requests in the window to 589.

| Metric | Before | After |
|--------|--------|-------|
| Payment p50 | 1100ms | 340ms |
| Payment p95 | 2000ms | 430ms |
| Requests in 60s | 277 | 589 |
| Aggregate throughput | 4.67 req/s | 9.86 req/s |

This is the value of load testing stated plainly. Every request succeeded and every correctness check passed both before and after, so nothing looked broken. Load testing was what surfaced that a correctness-neutral lifecycle choice was costing a bcrypt login on every single payment.

The remaining payment latency is the honest floor of the design: two synchronous ledger round trips (reserve then capture) plus risk, the state-machine commits and the outbox writes. Payment throughput is also bounded by the shared Payment Suspense account, since every reserve locks the same ledger row; at five users the two round trips dominate, but under heavier concurrency that row would serialise reserves. Moving the provider call and capture to a worker driven by callbacks would raise throughput at the cost of the simple synchronous model the service uses today.

## Post-Load Verification

After the load test, the reserve, capture and release invariant is checked directly against the live ledger: the Payment Suspense account balance is 0.00, so every reservation the load test created was matched by a capture or a release and none was left stranded. The ledger's own reconciliation (`GET /audit/verify`) and hash chain (`GET /audit/chain`) both return pass, confirming that the ledger stayed fully consistent under the orchestrator's concurrent settlement load.
