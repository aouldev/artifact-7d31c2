"""Public release helpers for participant-side benchmark runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.participant.core.paths import get_participant_dataset_root
from benchmark.participant.loaders.dataset import read_jsonl

DEFAULT_RELEASE_ID = "benchmark_release"
PARTICIPANT_ROOT = Path(__file__).resolve().parent
DEFAULT_RELEASE_ROOT = get_participant_dataset_root(DEFAULT_RELEASE_ID)
DEFAULT_OUTPUT_DIR = PARTICIPANT_ROOT / "runs"
DEFAULT_AGENT = "benchmark.participant.agents.examples.dummy_agent:DummyAgent"


@dataclass(frozen=True)
class PublicRelease:
    release_id: str
    root: Path
    train_path: Path
    validation_path: Path
    manifest_path: Path
    manifest: dict[str, Any]

    @property
    def validation_count(self) -> int:
        return int(self.manifest.get("counts", {}).get("validation", 0))

    @property
    def train_count(self) -> int:
        return int(self.manifest.get("counts", {}).get("train", 0))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_release(release: str | Path | None = None) -> PublicRelease:
    root = Path(release) if release else DEFAULT_RELEASE_ROOT
    if not root.is_absolute():
        root = Path.cwd() / root
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"release manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    train_path = root / "train.jsonl"
    validation_path = root / "validation.jsonl"
    # Accept an explicitly supplied legacy release root while keeping the
    # default participant path canonical and duplicate-free.
    if not train_path.exists() or not validation_path.exists():
        train_path = root / "public" / "train.jsonl"
        validation_path = root / "public" / "validation.jsonl"
    if not train_path.exists():
        raise FileNotFoundError(f"public train not found: {train_path}")
    if not validation_path.exists():
        raise FileNotFoundError(f"public validation not found: {validation_path}")
    return PublicRelease(
        release_id=str(manifest.get("release_id") or root.name),
        root=root,
        train_path=train_path,
        validation_path=validation_path,
        manifest_path=manifest_path,
        manifest=manifest,
    )


def release_info(release: PublicRelease) -> dict[str, Any]:
    train_rows = read_jsonl(release.train_path)
    validation_rows = read_jsonl(release.validation_path)
    validation_leaks = count_validation_leaks(validation_rows)
    return {
        "dataset_id": release.release_id,
        "root": str(release.root),
        "train_path": str(release.train_path),
        "validation_path": str(release.validation_path),
        "train_count": len(train_rows),
        "validation_count": len(validation_rows),
        "counts": release.manifest.get("counts", {}),
        "sha256": {
            "public_train": sha256_file(release.train_path),
            "public_validation": sha256_file(release.validation_path),
        },
        "validation_leak_count": validation_leaks,
        "private_gold_included": bool(release.manifest.get("private_gold_included", False)),
    }


def count_validation_leaks(rows: list[dict[str, Any]]) -> int:
    forbidden = {
        "gold",
        "ground_truth",
        "maintenance_label",
        "episode_type",
        "test_change",
        "test_patch",
        "test_diff",
        "test_files",
        "test_files_changed",
        "label",
    }
    metadata_forbidden = {
        "gold",
        "label",
        "maintenance_label",
        "test_patch",
        "test_diff",
        "test_files",
        "test_files_changed",
        "test_commit",
        "episode_type",
        "source",
        "source_pairing_path",
        "source_results_root",
        "source_run",
        "original_sample_id",
    }
    leaks = 0
    for row in rows:
        if forbidden & set(row):
            leaks += 1
        metadata = row.get("metadata")
        if isinstance(metadata, dict) and metadata_forbidden & set(metadata):
            leaks += 1
    return leaks
