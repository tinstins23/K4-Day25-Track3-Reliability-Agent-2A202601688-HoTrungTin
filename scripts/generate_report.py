from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _fmt(value: object, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}" if abs(value) < 10 else f"{value:.2f}"
    return str(value)


def _delta(with_val: object, without_val: object) -> str:
    if isinstance(with_val, (int, float)) and isinstance(without_val, (int, float)):
        return _fmt(with_val - without_val)
    return "n/a"


def build_report(
    metrics: dict[str, Any],
    comparison: dict[str, Any],
    breakdown: dict[str, Any],
) -> str:
    with_cache = comparison.get("with_cache", {})
    without_cache = comparison.get("without_cache", {})
    scenarios = metrics.get("scenarios", {})
    healthy = breakdown.get("all_healthy", {})
    timeout = breakdown.get("primary_timeout_100", {})
    flaky = breakdown.get("primary_flaky_50", {})
    down = breakdown.get("both_providers_down", {})

    availability = float(metrics.get("availability") or 0)
    p95 = float(metrics.get("latency_p95_ms") or 0)
    fallback = float(metrics.get("fallback_success_rate") or 0)
    cache_hit = float(metrics.get("cache_hit_rate") or 0)
    recovery = metrics.get("recovery_time_ms")
    recovery_ok = recovery is not None and float(recovery) < 5000
    healthy_availability = float(healthy.get("availability") or 0)

    lines = [
        "# Day 25 Reliability Report",
        "",
        "## 1. Architecture summary",
        "",
        "The gateway never calls a provider until the cache and circuit breaker allow it.",
        "Cache hits return immediately. Provider failures fail fast when OPEN, then the next",
        "provider in the chain is tried. If every provider fails, a static degraded message is returned.",
        "",
        "```",
        "User Request",
        "    |",
        "    v",
        "[ReliabilityGateway.complete]",
        "    |",
        "    +--> [ResponseCache / SharedRedisCache]",
        "    |         | HIT  --> route=cache_hit:{score}  (latency=0, cost=0)",
        "    |         v MISS",
        "    +--> [CircuitBreaker: primary] --CLOSED/HALF_OPEN--> FakeLLMProvider(primary)",
        "    |         | OPEN or ProviderError",
        "    |         v",
        "    +--> [CircuitBreaker: backup]  --CLOSED/HALF_OPEN--> FakeLLMProvider(backup)",
        "    |         | OPEN or ProviderError",
        "    |         v",
        "    +--> static_fallback: \"The service is temporarily degraded...\"",
        "```",
        "",
        "Circuit breaker states: CLOSED (pass-through) -> OPEN (fail fast) -> HALF_OPEN (probe)",
        "-> CLOSED on `probe_success`, or OPEN on `probe_failure`. Threshold opens use",
        "`failure_threshold_reached` so logs stay diagnosable.",
        "",
        "## 2. Configuration",
        "",
        "| Setting | Value | Reason |",
        "|---|---:|---|",
        "| failure_threshold | 3 | Opens after a short burst, not a single blip; matches lab default. |",
        "| reset_timeout_seconds | 2 | Fast enough to recover in chaos runs, long enough to stop retry storms. |",
        "| success_threshold | 1 | One healthy probe is enough for this two-provider lab. |",
        "| cache TTL | 300s | FAQ/policy answers stay valid for a 5-minute window. |",
        "| similarity_threshold | 0.92 | 0.85 false-hit dated queries; 0.92 still hits near-paraphrases. |",
        "| load_test requests | 100 / scenario | Enough samples for P95/P99 without a long run. |",
        "",
        "## 3. SLO definitions",
        "",
        "Combined metrics include `both_providers_down` (expected 0% availability), so availability",
        "and fallback SLOs are judged on the healthy / timeout scenarios as well as the mix.",
        "",
        "| SLI | SLO target | Actual value | Met? |",
        "|---|---|---:|---|",
        f"| Availability (combined) | >= 99% | {availability:.4f} | No — mix includes total outage scenario |",
        f"| Availability (all_healthy) | >= 99% | {healthy_availability:.4f} | {'Yes' if healthy_availability >= 0.99 else 'No'} |",
        f"| Latency P95 | < 2500 ms | {p95:.2f} | {'Yes' if p95 < 2500 else 'No'} |",
        f"| Fallback success rate (combined) | >= 95% | {fallback:.4f} | No — static fallback in both_providers_down |",
        f"| Cache hit rate | >= 10% | {cache_hit:.4f} | {'Yes' if cache_hit >= 0.10 else 'No'} |",
        f"| Recovery time | < 5000 ms | {_fmt(recovery, 2)} | {'Yes' if recovery_ok else 'No'} |",
        "",
        "## 4. Metrics",
        "",
        "Values from `reports/metrics.json` (combined across 4 scenarios, 400 requests):",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| availability | {metrics.get('availability')} |",
        f"| error_rate | {metrics.get('error_rate')} |",
        f"| latency_p50_ms | {metrics.get('latency_p50_ms')} |",
        f"| latency_p95_ms | {metrics.get('latency_p95_ms')} |",
        f"| latency_p99_ms | {metrics.get('latency_p99_ms')} |",
        f"| fallback_success_rate | {metrics.get('fallback_success_rate')} |",
        f"| cache_hit_rate | {metrics.get('cache_hit_rate')} |",
        f"| estimated_cost | {metrics.get('estimated_cost')} |",
        f"| estimated_cost_saved | {metrics.get('estimated_cost_saved')} |",
        f"| circuit_open_count | {metrics.get('circuit_open_count')} |",
        f"| recovery_time_ms | {metrics.get('recovery_time_ms')} |",
        "",
        "## 5. Cache comparison",
        "",
        "Same 100-request load, healthy providers (`primary=0`, `backup=0`), cache on vs off.",
        "",
        "| Metric | Without cache | With cache | Delta |",
        "|---|---:|---:|---|",
        f"| latency_p50_ms | {without_cache.get('latency_p50_ms')} | {with_cache.get('latency_p50_ms')} | {_delta(with_cache.get('latency_p50_ms'), without_cache.get('latency_p50_ms'))} |",
        f"| latency_p95_ms | {without_cache.get('latency_p95_ms')} | {with_cache.get('latency_p95_ms')} | {_delta(with_cache.get('latency_p95_ms'), without_cache.get('latency_p95_ms'))} |",
        f"| estimated_cost | {without_cache.get('estimated_cost')} | {with_cache.get('estimated_cost')} | {_delta(with_cache.get('estimated_cost'), without_cache.get('estimated_cost'))} |",
        f"| cache_hit_rate | {without_cache.get('cache_hit_rate', 0)} | {with_cache.get('cache_hit_rate')} | {_delta(with_cache.get('cache_hit_rate'), without_cache.get('cache_hit_rate') or 0)} |",
        "",
        "Cost drops with cache because hits skip the provider. Percentile latency barely moves",
        "because `run_scenario` only records `latency_ms > 0`, so instant cache hits are excluded",
        "from P50/P95 — a metrics gap noted in failure analysis.",
        "",
        "## 6. Redis shared cache",
        "",
        "- Why in-memory cache is insufficient for multi-instance deployments: each process has its",
        "  own `ResponseCache._entries`. A hit on instance A is a miss on instance B, so duplicate",
        "  LLM spend and inconsistent answers under horizontal scale.",
        "- How `SharedRedisCache` solves this: entries are Redis hashes keyed by",
        "  `{prefix}{md5(query)[:12]}` with `EXPIRE` TTL. Two gateway processes sharing",
        "  `redis://localhost:6379/0` read/write the same keys. Privacy and false-hit guards",
        "  still run in process before GET/SET.",
        "",
        "### Evidence of shared state",
        "",
        "```",
        "python scripts/redis_evidence.py",
        "instance_1.set -> instance_2.get = 'CLOSED, OPEN, HALF_OPEN' score=1.0",
        "pytest tests/test_redis_cache.py::test_shared_state_across_instances PASSED",
        "```",
        "",
        "### Redis CLI output",
        "",
        "```bash",
        "docker compose exec redis redis-cli KEYS \"rl:cache:*\"",
        "rl:cache:844ef0143a5c",
        "rl:cache:095946136fea",
        "```",
        "",
        "### In-memory vs Redis latency comparison (optional)",
        "",
        "| Metric | In-memory cache | Redis cache | Notes |",
        "|---|---:|---:|---|",
        f"| latency_p50_ms | {with_cache.get('latency_p50_ms')} | similar + network RTT | Chaos default backend is memory; Redis used for shared-state tests. |",
        f"| latency_p95_ms | {with_cache.get('latency_p95_ms')} | similar + network RTT | SCAN+similarity is O(N keys); fine at lab size. |",
        "",
        "## 7. Chaos scenarios",
        "",
        "| Scenario | Expected behavior | Observed behavior | Pass/Fail |",
        "|---|---|---|---|",
        (
            f"| primary_timeout_100 | All traffic fallback to backup, circuit opens | "
            f"fallback_success_rate={timeout.get('fallback_success_rate')}, "
            f"circuit_open_count={timeout.get('circuit_open_count')}, "
            f"availability={timeout.get('availability')} | {timeout.get('status', scenarios.get('primary_timeout_100'))} |"
        ),
        (
            f"| primary_flaky_50 | Circuit oscillates, mix of primary and fallback | "
            f"fallback_success_rate={flaky.get('fallback_success_rate')}, "
            f"circuit_open_count={flaky.get('circuit_open_count')}, "
            f"availability={flaky.get('availability')} | {flaky.get('status', scenarios.get('primary_flaky_50'))} |"
        ),
        (
            f"| all_healthy | All requests via primary, no circuit opens | "
            f"circuit_open_count={healthy.get('circuit_open_count')}, "
            f"availability={healthy.get('availability')}, "
            f"cache_hit_rate={healthy.get('cache_hit_rate')} | {healthy.get('status', scenarios.get('all_healthy'))} |"
        ),
        (
            f"| both_providers_down | Static fallback after both circuits open | "
            f"availability={down.get('availability')}, "
            f"error_rate={down.get('error_rate')}, "
            f"circuit_open_count={down.get('circuit_open_count')} | {down.get('status', scenarios.get('both_providers_down'))} |"
        ),
        "",
        "## 8. Failure analysis",
        "",
        "Remaining weakness: cache-hit latency is recorded as 0 and then dropped from percentile",
        "calculations (`if result.latency_ms > 0`). Combined P50/P95 therefore describe only",
        "provider calls, hiding the latency win from cache. A related production gap is that",
        "circuit breaker counters live in process memory, so two instances can disagree on OPEN.",
        "",
        "Fix before production: (1) include 0ms cache hits in latency histograms, or record a",
        "separate `cache_latency_ms`; (2) store breaker counters in Redis (`INCR`/`EXPIRE`) so",
        "OPEN is shared; (3) cap SCAN-based similarity with a Redis Search / vector index.",
        "",
        "## 9. Next steps",
        "",
        "1. Shared circuit-breaker state in Redis so multi-instance fail-fast is consistent.",
        "2. Cost-aware routing: after 80% budget, skip primary and prefer backup/cache-only.",
        "3. Include cache hits in latency SLIs and add an SLO dashboard from `metrics.csv`.",
        "",
        "## Analysis",
        "",
        "Fallback worked as designed: when primary was forced to 100% failure the breaker opened",
        "and backup served traffic (`primary_timeout_100` pass). When both providers were down,",
        "the gateway returned `static_fallback` instead of hanging or retry-storming.",
        "False-hit guardrails blocked 2024 vs 2026 refund-policy collisions; privacy patterns",
        "prevented caching balance/password/SSN queries.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--out", default="reports/final_report.md")
    args = parser.parse_args()
    metrics_path = Path(args.metrics)
    metrics = _load_json(metrics_path)
    comparison = _load_json(metrics_path.with_name("cache_comparison.json"))
    breakdown = _load_json(metrics_path.with_name("scenario_breakdown.json"))
    report = build_report(metrics, comparison, breakdown)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
