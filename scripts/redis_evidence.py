from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from reliability_lab.cache import SharedRedisCache

PREFIX = "rl:cache:"


def main() -> None:
    c1 = SharedRedisCache("redis://localhost:6379/0", 60, 0.5, PREFIX)
    c2 = SharedRedisCache("redis://localhost:6379/0", 60, 0.5, PREFIX)
    c1.flush()
    query = "Explain circuit breaker states in one paragraph."
    c1.set(query, "CLOSED, OPEN, HALF_OPEN")
    cached, score = c2.get(query)
    print(f"instance_1.set -> instance_2.get = {cached!r} score={score}")
    c1.set("List three benefits of response caching in LLM gateways.", "latency, cost, load")
    print(f"ping={c1.ping()}")
    keys = list(c1._redis.scan_iter(f"{PREFIX}*"))
    print("KEYS", keys)
    for key in keys:
        print(key, c1._redis.hgetall(key))
    c1.close()
    c2.close()


if __name__ == "__main__":
    main()
