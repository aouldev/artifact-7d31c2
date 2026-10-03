"""Shared metadata resolution for dynamic preparation and evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmark.participant.core.paths import get_metadata_path
from benchmark.server.evaluators.patch_common import PatchGoldCase


def metadata_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []


def _source_jsonl_candidates() -> list[Path]:
    metadata_root = get_metadata_path()
    # Try the canonical release metadata first, then the historical v1 fallback.
    candidates = [
        metadata_root / "source.jsonl",
        get_metadata_path("repo_test_maintenance_v1") / "source.jsonl",
    ]
    return [p for p in candidates if p.exists()]


def _lookup_source_metadata(episode_id: str) -> dict[str, Any]:
    for path in _source_jsonl_candidates():
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if episode_id not in line:
                    continue
                row = json.loads(line)
                if row.get("episode_id") != episode_id:
                    continue
                metadata: dict[str, Any] = {}
                for key in ("prod_commits", "test_commits"):
                    if key in row:
                        metadata[key] = row[key]
                source_metadata = dict(row.get("metadata") or {})
                if row.get("prod_diff") is not None:
                    source_metadata["source_prod_diff"] = row.get("prod_diff")
                if isinstance(row.get("prod_files"), list):
                    source_metadata["source_prod_files"] = row.get("prod_files")
                if source_metadata:
                    metadata["source_metadata"] = source_metadata
                metadata["source_jsonl"] = str(path)
                return metadata
    return {}


def dynamic_gold_metadata(gold: PatchGoldCase) -> dict[str, Any]:
    """Resolve the same source metadata for snapshot prep and dynamic scoring."""

    metadata = dict(gold.metadata)
    source_metadata = _lookup_source_metadata(gold.episode_id)
    for key, value in source_metadata.items():
        if key in {"source_metadata", "source_jsonl"}:
            metadata[key] = value
        elif key not in metadata or not metadata_list(metadata, key):
            metadata[key] = value
    return metadata
