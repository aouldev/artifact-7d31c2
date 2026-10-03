from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from benchmark.participant.loaders.dataset import write_jsonl
from benchmark.server.evaluators.patch_common import (
    PatchGoldCase,
    load_gold_cases,
    load_predictions,
)
from benchmark.server.evaluators.patch_dynamic import (
    DynamicPatchRecord,
    evaluate_patch_dynamic,
    summarize_dynamic_records,
)
from benchmark.server.evaluators.patch_static import _apply_check, evaluate_patch_static
from benchmark.server.tools.prepared_snapshot import (
    create_prepared_snapshot,
    reset_prepared_snapshot,
)


def test_locator_unresolved_is_not_a_dynamic_attempt() -> None:
    gold = {
        "e1": PatchGoldCase(
            episode_id="e1",
            repo="demo",
            base_commit="abc",
            maintenance_label="negative",
            test_patch="",
            test_files=[],
            prod_diff="",
        )
    }
    records = [
        DynamicPatchRecord(
            episode_id="e1",
            repo="demo",
            stage="locator_unresolved",
            csr_status="not_run",
            tps_status="not_run",
            ucr_status="not_run",
        )
    ]
    metrics = summarize_dynamic_records(records, gold)
    assert metrics["evaluated_count"] == 1
    assert metrics["dynamic_attempt_count"] == 0
    assert metrics["stage_counts"] == {"locator_unresolved": 1}
    assert metrics["csr"] is None
    assert metrics["ucr_evaluable"] is None


def _git(repo: Path, *args: str, input_text: str | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(tmp_path: Path) -> tuple[Path, str, str, str, str]:
    repo = tmp_path / "repos" / "demo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "calc.js").write_text("function value() { return 1; }\n", encoding="utf-8")
    (repo / "tests" / "calc.test.js").write_text("assert value() == 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "src" / "calc.js").write_text("function value() { return 2; }\n", encoding="utf-8")
    prod_diff = _git(repo, "diff", base)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "prod")
    prod = _git(repo, "rev-parse", "HEAD")

    (repo / "tests" / "calc.test.js").write_text("assert value() == 2\n", encoding="utf-8")
    test_diff = _git(repo, "diff", prod)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "test")
    return repo, base, prod, prod_diff, test_diff


def _official_gold_row(
    *,
    repo: str,
    base_commit: str,
    prod_diff: str,
    test_patch: str,
    label: str = "positive",
) -> dict:
    return {
        "schema_version": 1,
        "kind": "official_gold",
        "episode_id": "s1",
        "repo": repo,
        "base_commit": base_commit,
        "prod_diff": prod_diff,
        "prod_files": ["src/calc.js"],
        "maintenance_label": label,
        "test_patch": test_patch if label == "positive" else "",
        "test_files": ["tests/calc.test.js"] if label == "positive" else [],
        "metadata": {"timestamp": "2026-01-01T00:00:00Z"},
    }


def _prediction_row(
    *, repo: str, base_commit: str, test_patch: str, label: str = "positive"
) -> dict:
    return {
        "episode_id": "s1",
        "repo": repo,
        "base_commit": base_commit,
        "result": {
            "maintenance_label": label,
            "test_patch": test_patch if label == "positive" else "",
        },
        "metadata": {"agent_name": "unit-test"},
    }


def test_patch_common_loads_official_contract(tmp_path: Path) -> None:
    repo, base, _, prod_diff, test_diff = _init_repo(tmp_path)
    gold_path = tmp_path / "gold.jsonl"
    pred_path = tmp_path / "pred.jsonl"
    write_jsonl(
        gold_path,
        [
            _official_gold_row(
                repo=repo.name,
                base_commit=base,
                prod_diff=prod_diff,
                test_patch=test_diff,
            )
        ],
    )
    write_jsonl(
        pred_path,
        [_prediction_row(repo=repo.name, base_commit=base, test_patch=test_diff)],
    )

    gold = load_gold_cases(gold_path)
    predictions = load_predictions(pred_path)

    assert gold["s1"].maintenance_label == "positive"
    assert gold["s1"].base_commit == base
    assert gold["s1"].test_files == ["tests/calc.test.js"]
    assert predictions["s1"].repo == repo.name
    assert predictions["s1"].test_patch == test_diff


def test_patch_common_rejects_non_official_shapes(tmp_path: Path) -> None:
    repo, base, _, prod_diff, test_diff = _init_repo(tmp_path)
    gold_path = tmp_path / "invalid.jsonl"
    write_jsonl(
        gold_path,
        [
            {
                "sample_id": "s1",
                "repo": repo.name,
                "label": 1,
                "base": base,
                "prod_diff": prod_diff,
                "test_diff": test_diff,
                "test_files": ["tests/calc.test.js"],
            }
        ],
    )

    with pytest.raises(ValueError, match="unknown fields"):
        load_gold_cases(gold_path)


def test_static_patch_evaluator_reports_apply_metrics(tmp_path: Path) -> None:
    repo, base, _, prod_diff, test_diff = _init_repo(tmp_path)
    gold_path = tmp_path / "gold.jsonl"
    pred_path = tmp_path / "pred.jsonl"
    write_jsonl(
        gold_path,
        [
            _official_gold_row(
                repo=repo.name,
                base_commit=base,
                prod_diff=prod_diff,
                test_patch=test_diff,
            )
        ],
    )
    write_jsonl(
        pred_path,
        [_prediction_row(repo=repo.name, base_commit=base, test_patch=test_diff)],
    )

    metrics = evaluate_patch_static(
        gold_path=gold_path,
        predictions_path=pred_path,
        repos_root=tmp_path / "repos",
    )

    assert metrics["intent_tp_count"] == 1
    assert metrics["patch_apply_rate"] == 1.0
    assert set(metrics) == {
        "evaluated_count",
        "intent_tp_count",
        "patch_present_rate",
        "patch_apply_rate",
        "stage_counts",
    }


def test_static_apply_uses_episode_diff_with_commit_metadata(tmp_path: Path) -> None:
    repo, base, _, _, _ = _init_repo(tmp_path)
    _git(repo, "checkout", "--detach", base)
    (repo / "src" / "calc.js").write_text("function value() { return 2; }\n", encoding="utf-8")
    (repo / "src" / "helper.js").write_text("export const helper = 2;\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "mixed product")
    target = _git(repo, "rev-parse", "HEAD")
    sparse_prod_diff = _git(repo, "diff", base, target, "--", "src/calc.js")
    (repo / "tests" / "calc.test.js").write_text(
        "assert value() == 2\nassert helper == 2\n", encoding="utf-8"
    )
    test_diff = _git(repo, "diff", target, "--", "tests/calc.test.js")
    gold_path = tmp_path / "gold.jsonl"
    pred_path = tmp_path / "pred.jsonl"
    gold = _official_gold_row(
        repo=repo.name,
        base_commit=base,
        prod_diff=sparse_prod_diff,
        test_patch=test_diff,
    )
    gold["metadata"]["prod_commits"] = [target]
    write_jsonl(gold_path, [gold])
    write_jsonl(
        pred_path,
        [_prediction_row(repo=repo.name, base_commit=base, test_patch=test_diff)],
    )

    metrics = evaluate_patch_static(
        gold_path=gold_path,
        predictions_path=pred_path,
        repos_root=tmp_path / "repos",
    )

    assert metrics["patch_apply_rate"] == 1.0


def test_prepared_snapshot_uses_episode_diff_without_commit_extras(tmp_path: Path) -> None:
    repo, base, prod, prod_diff, _ = _init_repo(tmp_path)
    (repo / "src" / "helper.js").write_text("export const helper = 2;\n", encoding="utf-8")
    (repo / "tests" / "extra.spec.js").write_text("assert value() == 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "mixed product and tests")
    target = _git(repo, "rev-parse", "HEAD")
    sparse_prod_diff = _git(repo, "diff", base, target, "--", "src/calc.js")

    snapshot = create_prepared_snapshot(
        repo_source=repo,
        output_root=tmp_path / "snapshots",
        episode_id="s1",
        repo=repo.name,
        base_commit=base,
        prod_diff=sparse_prod_diff,
        prod_commits=[target],
        excluded_paths=["tests/calc.test.js"],
    )

    assert snapshot.metadata["product_state_source"] == "prod_diff"
    assert not (snapshot.repo_root / "src" / "helper.js").exists()
    assert "== 1" in (snapshot.repo_root / "tests" / "calc.test.js").read_text(encoding="utf-8")
    assert not (snapshot.repo_root / "tests" / "extra.spec.js").exists()


def test_static_apply_cannot_use_files_only_present_in_provenance_commit(tmp_path: Path) -> None:
    repo, base, _, prod_diff, _ = _init_repo(tmp_path)
    helper = repo / "src" / "helper.js"
    helper.write_text("export const helper = 2;\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "unrelated helper")
    target = _git(repo, "rev-parse", "HEAD")
    helper.write_text("export const helper = 3;\n")
    patch = _git(repo, "diff", "--", "src/helper.js")
    success, error = _apply_check(repo, base, prod_diff, patch, prod_commits=[target])
    assert not success
    assert error and "helper.js" in error


def test_prepared_snapshot_reset_preserves_node_modules(tmp_path: Path) -> None:
    repo, base, _, prod_diff, _ = _init_repo(tmp_path)
    snapshot = create_prepared_snapshot(
        repo_source=repo,
        output_root=tmp_path / "snapshots",
        episode_id="s1",
        repo=repo.name,
        base_commit=base,
        prod_diff=prod_diff,
    )
    node_modules = snapshot.repo_root / "node_modules"
    node_modules.mkdir()
    (node_modules / "cache.txt").write_text("keep", encoding="utf-8")
    (snapshot.repo_root / "tmp.txt").write_text("remove", encoding="utf-8")

    reset_prepared_snapshot(snapshot)

    assert (node_modules / "cache.txt").exists()
    assert not (snapshot.repo_root / "tmp.txt").exists()


def test_dynamic_patch_evaluator_runs_compile_and_changed_tests(tmp_path: Path) -> None:
    repo, base, _, prod_diff, test_diff = _init_repo(tmp_path)
    gold_path = tmp_path / "gold.jsonl"
    pred_path = tmp_path / "pred.jsonl"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    profile_path = profiles_dir / f"{repo.name}.json"
    profile_path.write_text(
        json.dumps(
            {
                "repo": repo.name,
                "validation": {
                    "compile": {
                        "argv": [sys.executable, "-c", "print('compile ok')"],
                    }
                },
                "runners": [
                    {
                        "name": "python_assert_file",
                        "path_regex": ".*\\.js$",
                        "cwd": "{{repo_root}}",
                        "argv": [
                            sys.executable,
                            "-c",
                            "from pathlib import Path; assert '== 2' in Path('{{test_file}}').read_text()",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_jsonl(
        gold_path,
        [
            _official_gold_row(
                repo=repo.name,
                base_commit=base,
                prod_diff=prod_diff,
                test_patch=test_diff,
            )
        ],
    )
    write_jsonl(
        pred_path,
        [_prediction_row(repo=repo.name, base_commit=base, test_patch=test_diff)],
    )

    metrics = evaluate_patch_dynamic(
        gold_path=gold_path,
        predictions_path=pred_path,
        repos_root=tmp_path / "repos",
        profiles_dir=profiles_dir,
        snapshots_root=tmp_path / "snapshots",
        output_dir=tmp_path / "out",
    )

    assert metrics["csr"] == 1.0
    assert metrics["tps"] == 1.0
    assert metrics["end_to_end_success_rate"] == 1.0


def test_dynamic_patch_evaluator_reports_ucr_from_lcov(tmp_path: Path, monkeypatch) -> None:
    # A local construction artifact must not replace this release's coverage target.
    monkeypatch.setattr(
        "benchmark.server.evaluators.dynamic_metadata._lookup_source_metadata",
        lambda episode_id: {"source_metadata": {"source_prod_files": ["src/unrelated.js"]}},
    )
    repo, base, _, prod_diff, test_diff = _init_repo(tmp_path)
    gold_path = tmp_path / "gold.jsonl"
    pred_path = tmp_path / "pred.jsonl"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    coverage_script = (
        "from pathlib import Path; "
        "assert '== 2' in Path('{{test_file}}').read_text(); "
        "out = Path('{{output_dir}}') / 'coverage'; "
        "out.mkdir(parents=True, exist_ok=True); "
        "(out / 'lcov.info').write_text('TN:\\nSF:{{repo_root}}/src/calc.js\\nDA:1,1\\nend_of_record\\n')"
    )
    (profiles_dir / f"{repo.name}.json").write_text(
        json.dumps(
            {
                "repo": repo.name,
                "validation": {"infer_compile": False},
                "runners": [
                    {
                        "name": "python_lcov",
                        "path_regex": ".*\\.js$",
                        "cwd": "{{repo_root}}",
                        "argv": [sys.executable, "-c", coverage_script],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_jsonl(
        gold_path,
        [
            _official_gold_row(
                repo=repo.name,
                base_commit=base,
                prod_diff=prod_diff,
                test_patch=test_diff,
            )
        ],
    )
    write_jsonl(
        pred_path,
        [_prediction_row(repo=repo.name, base_commit=base, test_patch=test_diff)],
    )

    records_path = tmp_path / "records.jsonl"
    metrics = evaluate_patch_dynamic(
        gold_path=gold_path,
        predictions_path=pred_path,
        repos_root=tmp_path / "repos",
        profiles_dir=profiles_dir,
        snapshots_root=tmp_path / "snapshots",
        output_dir=tmp_path / "out",
        records_path=records_path,
    )

    records = [json.loads(line) for line in records_path.read_text().splitlines()]
    assert metrics["tps"] == 1.0
    assert metrics["ucr"] == 1.0
    assert metrics["ucr_evaluable"] == 1.0
    assert metrics["coverage_status_counts"] == {"parsed": 1}
    assert records[0]["ucr_status"] == "passed"
    assert records[0]["coverage"]["covered_changed_location_count"] == 1
    assert records[0]["coverage"]["artifact_kind"] == "lcov"


def test_dynamic_patch_evaluator_marks_ucr_failed_without_coverage(tmp_path: Path) -> None:
    repo, base, _, prod_diff, test_diff = _init_repo(tmp_path)
    gold_path = tmp_path / "gold.jsonl"
    pred_path = tmp_path / "pred.jsonl"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / f"{repo.name}.json").write_text(
        json.dumps(
            {
                "repo": repo.name,
                "validation": {"infer_compile": False},
                "runners": [
                    {
                        "name": "python_no_coverage",
                        "path_regex": ".*\\.js$",
                        "cwd": "{{repo_root}}",
                        "argv": [
                            sys.executable,
                            "-c",
                            "from pathlib import Path; assert '== 2' in Path('{{test_file}}').read_text()",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_jsonl(
        gold_path,
        [
            _official_gold_row(
                repo=repo.name,
                base_commit=base,
                prod_diff=prod_diff,
                test_patch=test_diff,
            )
        ],
    )
    write_jsonl(
        pred_path,
        [_prediction_row(repo=repo.name, base_commit=base, test_patch=test_diff)],
    )

    records_path = tmp_path / "records.jsonl"
    metrics = evaluate_patch_dynamic(
        gold_path=gold_path,
        predictions_path=pred_path,
        repos_root=tmp_path / "repos",
        profiles_dir=profiles_dir,
        snapshots_root=tmp_path / "snapshots",
        output_dir=tmp_path / "out",
        records_path=records_path,
    )

    records = [json.loads(line) for line in records_path.read_text().splitlines()]
    assert metrics["tps"] == 1.0
    assert metrics["csr"] is None
    assert metrics["csr_configured_count"] == 0
    assert metrics["ucr"] == 0.0
    assert metrics["ucr_evaluable"] is None
    assert metrics["coverage_status_counts"] == {"missing_artifact": 1}
    assert records[0]["stage"] == "passed"
    assert records[0]["ucr_status"] == "failed"
    assert records[0]["coverage"]["status"] == "missing_artifact"


def test_dynamic_patch_evaluator_reports_ucr_from_raw_v8_native_path(tmp_path: Path) -> None:
    repo, base, _, prod_diff, test_diff = _init_repo(tmp_path)
    gold_path = tmp_path / "gold.jsonl"
    pred_path = tmp_path / "pred.jsonl"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    coverage_script = (
        "import json; "
        "from pathlib import Path; "
        "assert '== 2' in Path('{{test_file}}').read_text(); "
        "source = Path('{{repo_root}}') / 'src' / 'calc.js'; "
        "payload = {'result': [{'url': source.as_uri(), 'functions': ["
        "{'functionName': 'value', 'ranges': [{'startOffset': 0, 'endOffset': len(source.read_text()), 'count': 1}], 'isBlockCoverage': True}"
        "]}]}; "
        "out = Path('{{output_dir}}') / 'coverage'; "
        "out.mkdir(parents=True, exist_ok=True); "
        "(out / 'coverage-native.json').write_text(json.dumps(payload))"
    )
    (profiles_dir / f"{repo.name}.json").write_text(
        json.dumps(
            {
                "repo": repo.name,
                "validation": {"infer_compile": False},
                "runners": [
                    {
                        "name": "python_raw_v8",
                        "path_regex": ".*\\.js$",
                        "cwd": "{{repo_root}}",
                        "argv": [sys.executable, "-c", coverage_script],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_jsonl(
        gold_path,
        [
            _official_gold_row(
                repo=repo.name,
                base_commit=base,
                prod_diff=prod_diff,
                test_patch=test_diff,
            )
        ],
    )
    write_jsonl(
        pred_path,
        [_prediction_row(repo=repo.name, base_commit=base, test_patch=test_diff)],
    )

    records_path = tmp_path / "records.jsonl"
    metrics = evaluate_patch_dynamic(
        gold_path=gold_path,
        predictions_path=pred_path,
        repos_root=tmp_path / "repos",
        profiles_dir=profiles_dir,
        snapshots_root=tmp_path / "snapshots",
        output_dir=tmp_path / "out",
        records_path=records_path,
    )

    records = [json.loads(line) for line in records_path.read_text().splitlines()]
    assert metrics["ucr"] == 1.0
    assert metrics["coverage_status_counts"] == {"parsed": 1}
    assert records[0]["ucr_status"] == "passed"
    assert records[0]["coverage"]["artifact_kind"] == "raw-v8-native"
    assert records[0]["coverage"]["covered_changed_location_count"] == 1


def test_dynamic_patch_evaluator_reports_ucr_from_raw_v8_file_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "repos" / "demo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "calc.js").write_text(
        "const prefix = 1;\nfunction value() { return 1; }\n", encoding="utf-8"
    )
    (repo / "tests" / "calc.test.js").write_text("assert value() == 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "calc.js").write_text(
        "const prefix = 1;\nfunction value() { return 2; }\n", encoding="utf-8"
    )
    prod_diff = _git(repo, "diff", base)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "prod")
    prod = _git(repo, "rev-parse", "HEAD")
    (repo / "tests" / "calc.test.js").write_text("assert value() == 2\n", encoding="utf-8")
    test_diff = _git(repo, "diff", prod)

    gold_path = tmp_path / "gold.jsonl"
    pred_path = tmp_path / "pred.jsonl"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    coverage_script = (
        "import json; "
        "from pathlib import Path; "
        "assert '== 2' in Path('{{test_file}}').read_text(); "
        "source = Path('{{repo_root}}') / 'src' / 'calc.js'; "
        "payload = {'result': [{'url': source.as_uri(), 'functions': ["
        "{'functionName': 'prefix', 'ranges': [{'startOffset': 0, 'endOffset': 10, 'count': 1}], 'isBlockCoverage': True}"
        "]}]}; "
        "out = Path('{{output_dir}}') / 'coverage'; "
        "out.mkdir(parents=True, exist_ok=True); "
        "(out / 'coverage-native.json').write_text(json.dumps(payload))"
    )
    (profiles_dir / f"{repo.name}.json").write_text(
        json.dumps(
            {
                "repo": repo.name,
                "validation": {"infer_compile": False},
                "runners": [
                    {
                        "name": "python_raw_v8_file_fallback",
                        "path_regex": ".*\\.js$",
                        "cwd": "{{repo_root}}",
                        "argv": [sys.executable, "-c", coverage_script],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_jsonl(
        gold_path,
        [
            _official_gold_row(
                repo=repo.name,
                base_commit=base,
                prod_diff=prod_diff,
                test_patch=test_diff,
            )
        ],
    )
    write_jsonl(
        pred_path,
        [_prediction_row(repo=repo.name, base_commit=base, test_patch=test_diff)],
    )

    records_path = tmp_path / "records.jsonl"
    metrics = evaluate_patch_dynamic(
        gold_path=gold_path,
        predictions_path=pred_path,
        repos_root=tmp_path / "repos",
        profiles_dir=profiles_dir,
        snapshots_root=tmp_path / "snapshots",
        output_dir=tmp_path / "out",
        records_path=records_path,
    )

    records = [json.loads(line) for line in records_path.read_text().splitlines()]
    assert metrics["ucr"] == 1.0
    assert metrics["coverage_status_counts"] == {"parsed_file": 1}
    assert records[0]["coverage"]["ucr_basis"] == "changed_file_coverage"
    assert records[0]["coverage"]["covered_changed_files"] == ["src/calc.js"]


def test_dynamic_patch_evaluator_accepts_file_level_ucr_with_workspace_relative_coverage(
    tmp_path: Path,
) -> None:
    repo, base, _, prod_diff, test_diff = _init_repo(tmp_path)
    gold_path = tmp_path / "gold.jsonl"
    pred_path = tmp_path / "pred.jsonl"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    coverage_script = (
        "from pathlib import Path; "
        "assert '== 2' in (Path('{{repo_root}}') / '{{test_file}}').read_text(); "
        "out = Path('{{output_dir}}') / 'coverage'; "
        "out.mkdir(parents=True, exist_ok=True); "
        "(out / 'lcov.info').write_text('TN:\\nSF:calc.js\\nDA:99,1\\nend_of_record\\n')"
    )
    (profiles_dir / f"{repo.name}.json").write_text(
        json.dumps(
            {
                "repo": repo.name,
                "validation": {"infer_compile": False},
                "runners": [
                    {
                        "name": "python_workspace_relative_lcov",
                        "path_regex": ".*\\.js$",
                        "cwd": "{{repo_root}}/src",
                        "argv": [sys.executable, "-c", coverage_script],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_jsonl(
        gold_path,
        [
            _official_gold_row(
                repo=repo.name,
                base_commit=base,
                prod_diff=prod_diff,
                test_patch=test_diff,
            )
        ],
    )
    write_jsonl(
        pred_path,
        [_prediction_row(repo=repo.name, base_commit=base, test_patch=test_diff)],
    )

    records_path = tmp_path / "records.jsonl"
    metrics = evaluate_patch_dynamic(
        gold_path=gold_path,
        predictions_path=pred_path,
        repos_root=tmp_path / "repos",
        profiles_dir=profiles_dir,
        snapshots_root=tmp_path / "snapshots",
        output_dir=tmp_path / "out",
        records_path=records_path,
    )

    records = [json.loads(line) for line in records_path.read_text().splitlines()]
    assert metrics["ucr"] == 1.0
    assert metrics["coverage_status_counts"] == {"parsed_file": 1}
    assert records[0]["ucr_status"] == "passed"
    assert records[0]["coverage"]["covered_file_matches"] == {"src/calc.js": ["calc.js"]}
