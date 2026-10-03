#!/usr/bin/env python3
"""Render public benchmark-characterization summaries as SVG."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results" / "benchmark_characterization"
COLORS = {"all": "#0072B2", "some": "#E69F00", "none": "#B34E00"}
PATTERN_COLORS = {
    "AR": "#0072B2",
    "TA": "#009E73",
    "FA": "#E69F00",
    "IA": "#CC79A7",
    "OTHER": "#999999",
}


def load_inputs() -> tuple[dict, dict]:
    condition = json.loads((RESULTS / "condition_coverage.json").read_text(encoding="utf-8"))
    patterns = json.loads(
        (RESULTS / "maintenance_pattern_summary.json").read_text(encoding="utf-8")
    )
    denominator = condition["positive_episode_count"]
    if denominator != 288 or condition["analysis_episode_count"] != denominator:
        raise ValueError("condition coverage does not use the frozen 288-episode denominator")
    for row in condition["conditions"]:
        if sum(row[key] for key in ("all", "some", "none")) != denominator:
            raise ValueError(f"condition counts do not sum to {denominator}: {row['condition']}")
    if patterns["positive_episode_count"] != denominator:
        raise ValueError("maintenance-pattern denominator does not match condition coverage")
    return condition, patterns


def text(x: float, y: float, value: str, *, size: int = 16, weight: str = "normal",
         anchor: str = "start", fill: str = "#263747") -> str:
    return (
        f'<text x="{x:g}" y="{y:g}" font-family="Times New Roman, Times, serif" '
        f'font-size="{size}px" font-weight="{weight}" text-anchor="{anchor}" '
        f'fill="{fill}">{escape(value)}</text>'
    )


def rect(x: float, y: float, width: float, height: float, fill: str,
         stroke: str = "none") -> str:
    return (
        f'<rect x="{x:g}" y="{y:g}" width="{width:g}" height="{height:g}" '
        f'fill="{fill}" stroke="{stroke}" />'
    )


def svg(width: int, height: int, body: str, title: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">\n'
        f'<title id="title">{escape(title)}</title>\n{body}\n</svg>\n'
    )


def render_condition(condition: dict) -> str:
    width, height = 900, 470
    body: list[str] = [text(36, 38, "Condition coverage across recorded test targets", size=22, weight="bold")]
    body.append(text(36, 64, "288 positive episodes; counts are mutually exclusive at the episode level", size=14, fill="#61707D"))

    x0, bar_width, row_height = 230, 580, 46
    max_value = condition["positive_episode_count"]
    for row_index, row in enumerate(condition["conditions"]):
        y = 115 + row_index * 130
        label = "Old test source" if row["condition"] == "old_test_source" else "Module-name correspondence"
        body.append(text(36, y + 28, label, size=16, weight="bold"))
        body.append(text(36, y + 51, f"{row['episodes_omitting']} episodes omit at least one target", size=13, fill="#61707D"))
        cursor = x0
        for key in ("all", "some", "none"):
            count = row[key]
            bar = bar_width * count / max_value
            body.append(rect(cursor, y, bar, row_height, COLORS[key], "#FFFFFF"))
            body.append(text(cursor + bar / 2, y + 29, str(count), size=14, weight="bold", anchor="middle", fill="#FFFFFF" if key != "some" else "#263747"))
            cursor += bar
        body.append(text(x0 + bar_width + 12, y + 28, f"n={max_value}", size=13, fill="#61707D"))

    legend_y = 405
    cursor = 36
    for key, label in (("all", "All targets"), ("some", "Some targets"), ("none", "No targets")):
        body.append(rect(cursor, legend_y - 13, 16, 16, COLORS[key]))
        body.append(text(cursor + 23, legend_y, label, size=13))
        cursor += 145
    return svg(width, height, "\n".join(body), "Condition coverage")


def render_patterns(patterns: dict) -> str:
    width, height = 900, 470
    counts = patterns["pattern_episode_counts"]
    body: list[str] = [text(36, 38, "Maintenance patterns in positive episodes", size=22, weight="bold")]
    body.append(text(36, 64, "Episode counts; categories may overlap", size=14, fill="#61707D"))
    max_value = max(counts.values())
    x0, bar_width, row_height = 250, 560, 38
    for index, key in enumerate(("AR", "TA", "FA", "IA", "OTHER")):
        y = 100 + index * 58
        count = counts[key]
        width_value = bar_width * count / max_value
        body.append(text(36, y + 25, key, size=16, weight="bold"))
        body.append(rect(x0, y, width_value, row_height, PATTERN_COLORS[key]))
        body.append(text(x0 + width_value + 12, y + 25, f"{count} ({patterns['pattern_percentages'][key]:.2f}%)", size=14))
    body.append(text(36, 426, f"Positive episodes: {patterns['positive_episode_count']}  |  Requirement rows: {patterns['requirement_row_count']}", size=13, fill="#61707D"))
    return svg(width, height, "\n".join(body), "Maintenance patterns")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    condition, patterns = load_inputs()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "characterization_condition_coverage.svg").write_text(
        render_condition(condition), encoding="utf-8"
    )
    (args.output_dir / "characterization_maintenance_patterns.svg").write_text(
        render_patterns(patterns), encoding="utf-8"
    )
    print(f"wrote characterization figures to {args.output_dir}")


if __name__ == "__main__":
    main()
