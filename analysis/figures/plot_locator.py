#!/usr/bin/env python3
"""Render public locator recall metrics as an SVG summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[2]
METRICS = ROOT / "results" / "locator" / "metrics.json"
COLORS = {"hit_at_5": "#0072B2", "all_recall_at_20": "#E69F00"}


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


def load_metrics() -> dict:
    metrics = json.loads(METRICS.read_text(encoding="utf-8"))
    cohort = metrics["cohort"]
    if cohort != {"positive_episodes": 103, "applicable_episodes": 74, "new_test_only_episodes": 29}:
        raise ValueError("locator cohort does not match the frozen validation scope")
    for method, row in metrics["methods"].items():
        for key in ("hit_at_5", "all_recall_at_20"):
            count_key = f"{key}_count"
            if row[count_key] > cohort["applicable_episodes"] or not 0 <= row[key] <= 1:
                raise ValueError(f"invalid {key} value for {method}")
    return metrics


def render(metrics: dict) -> str:
    width, height = 900, 500
    methods = ("nc", "ncc", "codex", "opencode")
    labels = {"nc": "NC", "ncc": "NCC", "codex": "Codex", "opencode": "OpenCode"}
    body: list[str] = [text(36, 38, "Locator recall on applicable positive episodes", size=22, weight="bold")]
    body.append(text(36, 64, "74 applicable episodes; 29 new-test-only episodes are reported separately", size=14, fill="#61707D"))
    x0, chart_width, chart_top, chart_height = 110, 700, 100, 300
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        y = chart_top + chart_height * (1 - tick)
        body.append(f'<line x1="{x0}" y1="{y:g}" x2="{x0 + chart_width}" y2="{y:g}" stroke="#D9E0E6" stroke-width="1" />')
        body.append(text(x0 - 12, y + 5, f"{tick:.0%}", size=12, anchor="end", fill="#61707D"))
    group_width = chart_width / len(methods)
    bar_width = 48
    for index, method in enumerate(methods):
        center = x0 + group_width * (index + 0.5)
        row = metrics["methods"][method]
        for offset, key in ((-30, "hit_at_5"), (30, "all_recall_at_20")):
            value = row[key]
            x = center + offset - bar_width / 2
            y = chart_top + chart_height * (1 - value)
            body.append(rect(x, y, bar_width, chart_top + chart_height - y, COLORS[key]))
            body.append(text(x + bar_width / 2, y - 8, f"{value:.1%}", size=12, anchor="middle"))
        body.append(text(center, chart_top + chart_height + 28, labels[method], size=14, anchor="middle", weight="bold"))
    body.append(f'<line x1="{x0}" y1="{chart_top + chart_height}" x2="{x0 + chart_width}" y2="{chart_top + chart_height}" stroke="#61707D" stroke-width="1" />')
    legend_y = 455
    for x, key, label in ((250, "hit_at_5", "Hit@5"), (470, "all_recall_at_20", "AllRecall@20")):
        body.append(rect(x, legend_y - 13, 16, 16, COLORS[key]))
        body.append(text(x + 23, legend_y, label, size=13))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">\n'
        '<title id="title">Locator recall</title>\n'
        + "\n".join(body)
        + "\n</svg>\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    metrics = load_metrics()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "locator_recall.svg").write_text(render(metrics), encoding="utf-8")
    print(f"wrote locator figure to {args.output_dir}")


if __name__ == "__main__":
    main()
