#!/usr/bin/env python3
"""Recompute RQ2 patch-label counts and paired missing-share bootstrap results."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTORS = ("developer", "codex", "opencode")
KNOWN = {"complete", "partial", "missing"}


def first_difference(expected, actual, path: str = "$") -> str | None:
    """Compare structure and counts exactly, allowing floating-point roundoff."""
    if type(expected) is not type(actual):
        return path
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            return path
        for key in expected:
            difference = first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference is not None:
                return difference
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            return path
        for index, (left, right) in enumerate(zip(expected, actual)):
            difference = first_difference(left, right, f"{path}[{index}]")
            if difference is not None:
                return difference
    elif isinstance(expected, float):
        if not math.isclose(expected, actual, rel_tol=0, abs_tol=1e-12):
            return path
    elif expected != actual:
        return path
    return None


def bootstrap(values: list[float], seed: int, draws: int) -> dict:
    rng = random.Random(seed)
    n = len(values)
    if not n or draws < 40:
        raise ValueError("Bootstrap requires a nonempty sample and at least 40 draws")
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(draws))
    return {
        "n_episodes": n,
        "mean": sum(values) / n,
        "ci95_low": means[int(0.025 * draws)],
        "ci95_high": means[int(0.975 * draws) - 1],
        "positive_fraction": sum(v > 0 for v in values) / n,
        "seed": seed,
        "draws": draws,
    }


def summarize(rows: list[dict], seed: int, draws: int) -> tuple[dict, list[dict]]:
    pairs = [(r["episode_id"], r["behavior_id"]) for r in rows]
    if len(pairs) != len(set(pairs)):
        raise ValueError("Duplicate episode/behavior pair")
    groups = defaultdict(list)
    for row in rows:
        if any(row[a] not in KNOWN | {"unknown"} for a in ACTORS):
            raise ValueError("Unrecognized patch label")
        groups[row["episode_id"]].append(row)
    included, excluded = [], []
    for episode_id, behaviors in groups.items():
        unknown = {a: sum(r[a] == "unknown" for r in behaviors) for a in ACTORS}
        if any(unknown.values()):
            excluded.append({"episode_id": episode_id, "reason": "unknown_patch_semantics", "unknown_counts": unknown})
            continue
        shares = {a: sum(r[a] == "missing" for r in behaviors) / len(behaviors) for a in ACTORS}
        included.append({
            "episode_id": episode_id,
            "repo": behaviors[0]["repo"],
            "behavior_count": len(behaviors),
            **{f"{a}_missing_share": shares[a] for a in ACTORS},
            **{f"{a}_minus_developer": shares[a] - shares["developer"] for a in ("codex", "opencode")},
        })
    return {
        "schema": "rq2_patch_behavior_summary",
        "source_confirmed_behavior_rows": len(rows),
        "episodes_with_source_confirmed_behaviors": len(groups),
        "patch_semantics_counts": {a: dict(Counter(r[a] for r in rows)) for a in ACTORS},
        "paired_comparison": {
            "included_episodes": len(included),
            "excluded_episodes_with_unknown_labels": excluded,
            "selection": "Include episodes with at least one source-confirmed behavior and no unknown patch label for any actor. Episodes absent from the behavior table are not in the comparison.",
            "unit": "Difference in missing-share proportions, agent minus developer, with each paired episode weighted equally.",
            "bootstrap": "Resample paired episodes with replacement; use percentile indices floor(0.025*draws) and floor(0.975*draws)-1 in sorted bootstrap means.",
            "differences_agent_minus_developer": {a: bootstrap([r[f"{a}_minus_developer"] for r in included], seed, draws) for a in ("codex", "opencode")},
        },
    }, included


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "results/rq2/behavior_labels.csv")
    parser.add_argument("--paired", type=Path, default=ROOT / "results/rq2/episode_missing_share.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "results/rq2/behavior_summary.json")
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--draws", type=int, default=20_000)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with args.input.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    result, paired = summarize(rows, args.seed, args.draws)
    with args.paired.open(newline="") as handle:
        released = list(csv.DictReader(handle))
    if released != [{k: str(v) for k, v in row.items()} for row in paired]:
        raise SystemExit("Paired episode table differs from the behavior labels")
    if args.check:
        difference = first_difference(json.loads(args.output.read_text()), result)
        if difference is not None:
            raise SystemExit(f"Behavior summary differs from the released labels at {difference}")
    else:
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(result, indent=2) + "\n")
        temporary.replace(args.output)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
