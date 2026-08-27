# Day 25 Reliability Report

## 1. Architecture summary

The gateway never calls a provider until the cache and circuit breaker allow it.
Cache hits return immediately. Provider failures fail fast when OPEN, then the next
provider in the chain is tried. If every provider fails, a static degraded message is returned.

```
User Request
    |
    v
[ReliabilityGateway.complete]
    |
    +--> [ResponseCache / SharedRedisCache]
    |         | HIT  --> route=cache_hit:{score}  (latency=0, cost=0)
    |         v MISS
    +--> [CircuitBreaker: primary] --CLOSED/HALF_OPEN--> FakeLLMProvider(primary)
    |         | OPEN or ProviderError
    |         v
    +--> [CircuitBreaker: backup]  --CLOSED/HALF_OPEN--> FakeLLMProvider(backup)
    |         | OPEN or ProviderError
    |         v
    +--> static_fallback: "The service is temporarily degraded..."
```

Circuit breaker states: CLOSED (pass-through) -> OPEN (fail fast) -> HALF_OPEN (probe)
-> CLOSED on `probe_success`, or OPEN on `probe_failure`. Threshold opens use
`failure_threshold_reached` so logs stay diagnosable.

## 2. Configuration

| Setting | Value | Reason |
|---|---:|---|
| failure_threshold | 3 | Opens after a short burst, not a single blip; matches lab default. |
| reset_timeout_seconds | 2 | Fast enough to recover in chaos runs, long enough to stop retry storms. |
| success_threshold | 1 | One healthy probe is enough for this two-provider lab. |
| cache TTL | 300s | FAQ/policy answers stay valid for a 5-minute window. |
| similarity_threshold | 0.92 | 0.85 false-hit dated queries; 0.92 still hits near-paraphrases. |
| load_test requests | 100 / scenario | Enough samples for P95/P99 without a long run. |

## 3. SLO definitions

Combined metrics include `both_providers_down` (expected 0% availability), so availability
and fallback SLOs are judged on the healthy / timeout scenarios as well as the mix.

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability (combined) | >= 99% | 0.7450 | No — mix includes total outage scenario |
| Availability (all_healthy) | >= 99% | 1.0000 | Yes |
| Latency P95 | < 2500 ms | 315.37 | Yes |
| Fallback success rate (combined) | >= 95% | 0.3289 | No — static fallback in both_providers_down |
| Cache hit rate | >= 10% | 0.4750 | Yes |
| Recovery time | < 5000 ms | 2295.94 | Yes |

## 4. Metrics

Values from `reports/metrics.json` (combined across 4 scenarios, 400 requests):

| Metric | Value |
|---|---:|
| availability | 0.745 |
| error_rate | 0.255 |
| latency_p50_ms | 238.59 |
| latency_p95_ms | 315.37 |
| latency_p99_ms | 320.34 |
| fallback_success_rate | 0.3289 |
| cache_hit_rate | 0.475 |
| estimated_cost | 0.051092 |
| estimated_cost_saved | 0.19 |
| circuit_open_count | 9 |
| recovery_time_ms | 2295.9444522857666 |

## 5. Cache comparison

Same 100-request load, healthy providers (`primary=0`, `backup=0`), cache on vs off.

| Metric | Without cache | With cache | Delta |
|---|---:|---:|---|
| latency_p50_ms | 207.98 | 206.04 | -1.9400 |
| latency_p95_ms | 237.42 | 242.1 | 4.6800 |
| estimated_cost | 0.05647 | 0.02517 | -0.0313 |
| cache_hit_rate | 0.0 | 0.58 | 0.5800 |

Cost drops with cache because hits skip the provider. Percentile latency barely moves
because `run_scenario` only records `latency_ms > 0`, so instant cache hits are excluded
from P50/P95 — a metrics gap noted in failure analysis.

## 6. Redis shared cache

- Why in-memory cache is insufficient for multi-instance deployments: each process has its
  own `ResponseCache._entries`. A hit on instance A is a miss on instance B, so duplicate
  LLM spend and inconsistent answers under horizontal scale.
- How `SharedRedisCache` solves this: entries are Redis hashes keyed by
  `{prefix}{md5(query)[:12]}` with `EXPIRE` TTL. Two gateway processes sharing
  `redis://localhost:6379/0` read/write the same keys. Privacy and false-hit guards
  still run in process before GET/SET.

### Evidence of shared state

```
python scripts/redis_evidence.py
instance_1.set -> instance_2.get = 'CLOSED, OPEN, HALF_OPEN' score=1.0
pytest tests/test_redis_cache.py::test_shared_state_across_instances PASSED
```

### Redis CLI output

```bash
docker compose exec redis redis-cli KEYS "rl:cache:*"
rl:cache:844ef0143a5c
rl:cache:095946136fea
```

### In-memory vs Redis latency comparison (optional)

| Metric | In-memory cache | Redis cache | Notes |
|---|---:|---:|---|
| latency_p50_ms | 206.04 | similar + network RTT | Chaos default backend is memory; Redis used for shared-state tests. |
| latency_p95_ms | 242.1 | similar + network RTT | SCAN+similarity is O(N keys); fine at lab size. |

## 7. Chaos scenarios

| Scenario | Expected behavior | Observed behavior | Pass/Fail |
|---|---|---|---|
| primary_timeout_100 | All traffic fallback to backup, circuit opens | fallback_success_rate=0.9412, circuit_open_count=5, availability=0.98 | pass |
| primary_flaky_50 | Circuit oscillates, mix of primary and fallback | fallback_success_rate=1.0, circuit_open_count=2, availability=1.0 | pass |
| all_healthy | All requests via primary, no circuit opens | circuit_open_count=0, availability=1.0, cache_hit_rate=0.62 | pass |
| both_providers_down | Static fallback after both circuits open | availability=0.0, error_rate=1.0, circuit_open_count=2 | pass |

## 8. Failure analysis

Remaining weakness: cache-hit latency is recorded as 0 and then dropped from percentile
calculations (`if result.latency_ms > 0`). Combined P50/P95 therefore describe only
provider calls, hiding the latency win from cache. A related production gap is that
circuit breaker counters live in process memory, so two instances can disagree on OPEN.

Fix before production: (1) include 0ms cache hits in latency histograms, or record a
separate `cache_latency_ms`; (2) store breaker counters in Redis (`INCR`/`EXPIRE`) so
OPEN is shared; (3) cap SCAN-based similarity with a Redis Search / vector index.

## 9. Next steps

1. Shared circuit-breaker state in Redis so multi-instance fail-fast is consistent.
2. Cost-aware routing: after 80% budget, skip primary and prefer backup/cache-only.
3. Include cache hits in latency SLIs and add an SLO dashboard from `metrics.csv`.

## Analysis

Fallback worked as designed: when primary was forced to 100% failure the breaker opened
and backup served traffic (`primary_timeout_100` pass). When both providers were down,
the gateway returned `static_fallback` instead of hanging or retry-storming.
False-hit guardrails blocked 2024 vs 2026 refund-policy collisions; privacy patterns
prevented caching balance/password/SSN queries.
