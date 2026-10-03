from __future__ import annotations

import json
import zipfile
from pathlib import Path

from benchmark.participant.loaders.dataset import write_jsonl
from benchmark.participant.release import PublicRelease
from benchmark.participant.submission import package_submission, validate_submission


def _make_release(tmp_path: Path) -> PublicRelease:
    root = tmp_path / "release"
    public = root / "public"
    public.mkdir(parents=True)
    train_path = public / "train.jsonl"
    validation_path = public / "validation.jsonl"
    write_jsonl(
        train_path,
        [
            {
                "episode_id": "e0",
                "repo": "demo",
                "base_commit": "base",
                "prod_files": [],
                "prod_diff": "",
                "gold": {"maintenance_label": "negative", "test_patch": ""},
            }
        ],
    )
    write_jsonl(
        validation_path,
        [
            {
                "episode_id": "e1",
                "repo": "demo",
                "base_commit": "base",
                "prod_files": [],
                "prod_diff": "",
            }
        ],
    )
    manifest = {"release_id": "r1", "counts": {"train": 1, "validation": 1}}
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return PublicRelease("r1", root, train_path, validation_path, manifest_path, manifest)


def _make_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (run_dir / "summary.json").write_text("{}", encoding="utf-8")
    write_jsonl(
        run_dir / "predictions.jsonl",
        [
            {
                "episode_id": "e1",
                "result": {"maintenance_label": "negative", "test_patch": ""},
            }
        ],
    )
    traces = run_dir / "traces"
    traces.mkdir()
    (traces / "e1.json").write_text("{}", encoding="utf-8")
    return run_dir


def test_validate_submission_accepts_complete_run(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    run_dir = _make_run(tmp_path)

    report = validate_submission(run_dir, release)

    assert report.ok
    assert report.stats["prediction_count"] == 1
    assert report.stats["trace_count"] == 1


def test_validate_submission_rejects_missing_prediction(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    run_dir = _make_run(tmp_path)
    write_jsonl(run_dir / "predictions.jsonl", [])

    report = validate_submission(run_dir, release)

    assert not report.ok
    assert any("missing prediction count" in error for error in report.errors)


def test_package_submission_includes_traces_by_default(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    run_dir = _make_run(tmp_path)

    package_path = package_submission(run_dir, release)

    with zipfile.ZipFile(package_path) as archive:
        names = set(archive.namelist())
    assert "predictions.jsonl" in names
    assert "traces/e1.json" in names
    assert "package_manifest.json" in names
