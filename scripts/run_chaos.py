from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from reliability_lab.chaos import load_queries, run_cache_comparison, run_simulation
from reliability_lab.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/metrics.json")
    args = parser.parse_args()
    config = load_config(args.config)
    queries = load_queries()
    breakdown: dict[str, dict[str, object]] = {}
    metrics = run_simulation(config, queries, scenario_breakdown=breakdown)
    metrics.write_json(args.out)
    csv_path = str(Path(args.out).with_suffix(".csv"))
    metrics.write_csv(csv_path)
    comparison = run_cache_comparison(config, queries)
    comparison_path = Path(args.out).with_name("cache_comparison.json")
    comparison_path.write_text(json.dumps(comparison, indent=2, ensure_ascii=False))
    breakdown_path = Path(args.out).with_name("scenario_breakdown.json")
    breakdown_path.write_text(json.dumps(breakdown, indent=2, ensure_ascii=False))
    print(f"wrote {args.out}")
    print(f"wrote {csv_path}")
    print(f"wrote {comparison_path}")
    print(f"wrote {breakdown_path}")


if __name__ == "__main__":
    main()
