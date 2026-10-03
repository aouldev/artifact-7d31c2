#!/usr/bin/env python3
"""Prepare or replay the two frozen Saleor behavior-mutation cases."""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent
SUITES = ("old", "developer", "codex", "opencode")


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def relative(root: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or ".." in parts:
        raise ValueError("Expected a repository-relative path")
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escapes the repository")
    return target


def apply_patch(repo: Path, patch: str) -> None:
    if not patch.strip():
        return
    for extra in (["--check"], []):
        result = subprocess.run(["git", "apply", *extra, "-"], cwd=repo,
                                input=patch, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError("Patch does not apply: " + result.stderr)


def hashes(repo: Path, paths: list[str]) -> dict:
    return {path: sha(relative(repo, path).read_bytes()) for path in paths}


def archive_revision(source: Path, commit: str) -> bytes:
    environment = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"}
    result = subprocess.run(["git", "-C", str(source), "archive", commit],
                            capture_output=True, env=environment, check=True)
    return result.stdout


def materialize(content: bytes, repo: Path) -> None:
    repo.mkdir(parents=True)
    with tarfile.open(fileobj=BytesIO(content), mode="r:") as archive:
        archive.extractall(repo, filter="data")


def execute_jest(repo: Path, paths: list[str], output: Path, timeout: int) -> dict:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is required for execution")
    command = [node, str(repo / "node_modules/jest/bin/jest.js"), "--runInBand",
               "--runTestsByPath", *paths, "--coverage=false", "--no-cache", "--json",
               "--outputFile=" + str(output)]
    result = subprocess.run(command, cwd=repo, env={**os.environ, "CI": "true", "TZ": "UTC"},
                            capture_output=True, text=True, timeout=timeout)
    output.with_suffix(".stdout.txt").write_text(result.stdout)
    output.with_suffix(".stderr.txt").write_text(result.stderr)
    if not output.exists():
        raise RuntimeError("Jest produced no JSON report; inspect the execution logs")
    raw = json.loads(output.read_text())
    assertions = []
    for suite in raw.get("testResults", []):
        path = Path(suite["name"]).resolve().relative_to(repo.resolve()).as_posix()
        assertions.extend({"path": path, "name": a["fullName"], "status": a["status"]}
                          for a in suite.get("assertionResults", []))
    return {"exit_code": result.returncode, "total": raw.get("numTotalTests"),
            "passed": raw.get("numPassedTests"), "failed": raw.get("numFailedTests"),
            "runtime_error_suites": raw.get("numRuntimeErrorTestSuites"), "assertions": assertions}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, required=True, help="Saleor Dashboard checkout containing the base commit")
    parser.add_argument("--dependencies-root", type=Path, help="Installed node_modules for the declared base revision")
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    parser.add_argument("--behavior", choices=("attribute_type_forwarding", "boolean_static_choices"))
    parser.add_argument("--timeout-seconds", type=int, default=180)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true", help="Verify inputs and recorded hashes without running Jest")
    mode.add_argument("--execute", action="store_true", help="Run admission probes and all normal/mutant test pairs")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Output directory already exists")
    source = args.source_repo.resolve()
    output = args.output.resolve()
    if output.is_relative_to(source):
        raise ValueError("Output must be outside the source checkout")
    if args.timeout_seconds <= 0:
        raise ValueError("timeout-seconds must be positive")
    dependencies = args.dependencies_root.resolve() if args.dependencies_root else None
    if args.execute and (dependencies is None or not (dependencies / "jest/bin/jest.js").is_file()):
        raise ValueError("Execution requires --dependencies-root containing Jest")
    config = json.loads((HERE / "case.json").read_text())
    cohort = (HERE / config["cohort_path"]).resolve()
    episode = next(row for row in map(json.loads, cohort.read_text().splitlines())
                   if row["episode_id"] == config["episode_id"])
    if episode["base_commit"] != config["base_commit"] or sha(episode["prod_diff"].encode()) != config["prod_diff_sha256"]:
        raise ValueError("Released production input does not match this case")
    patches = {"old": ""}
    for suite, entry in config["patches"].items():
        content = relative(HERE, entry["path"]).read_bytes()
        if sha(content) != entry["sha256"]:
            raise ValueError("Test-patch hash mismatch")
        patches[suite] = content.decode()
    expected = {row["behavior_id"]: row for row in json.loads(
        (HERE.parents[2] / "results/rq2/mutation_execution.json").read_text())["rows"]}
    specs = [json.loads(path.read_text()) for path in sorted((HERE / "specs").glob("*.json"))
             if not args.behavior or path.stem == args.behavior]
    if not specs:
        raise ValueError("No mutation specification matched")
    output.mkdir(parents=True)
    archive = archive_revision(source, config["base_commit"])
    records = []
    for spec in specs:
        behavior = spec["behavior_id"]
        edit = spec["production_edit"]
        if edit["path"] not in config["production_paths"]:
            raise ValueError("Mutation target is not a declared production path")
        case_root = output / behavior
        case_root.mkdir()
        record = {"episode_id": config["episode_id"], "behavior_id": behavior,
                  "admission_probe": {}, "suite_runs": {}}
        for suite in ("probe", *SUITES):
            repo = case_root / suite / "repo"
            materialize(archive, repo)
            apply_patch(repo, episode["prod_diff"])
            fixture = config["fixture"]
            target = relative(repo, fixture["path"])
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(fixture["content"])
            if hashes(repo, config["production_paths"]) != config["normal_source_sha256"]:
                raise ValueError("Normal production hashes differ from the recorded case")
            if suite == "probe":
                probe = spec["probe"]
                content = relative(HERE, probe["source"]).read_bytes()
                if sha(content) != probe["sha256"]:
                    raise ValueError("Probe hash mismatch")
                target = relative(repo, probe["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                targets = [probe["path"]]
            else:
                apply_patch(repo, patches[suite])
                targets = sorted(p for p in config["suite_slice"] if relative(repo, p).is_file())
                recorded = expected[behavior]["suite_runs"][suite]
                if targets != recorded["test_files"] or hashes(repo, targets) != recorded["test_sha256"]:
                    raise ValueError("Selected test files differ from the recorded suite: " + suite)
            test_hashes = hashes(repo, targets)
            production = relative(repo, edit["path"])
            normal = production.read_bytes()
            if normal.count(edit["normal"].encode()) != 1:
                raise ValueError("Mutation anchor must match exactly once")
            mutant = normal.replace(edit["normal"].encode(), edit["mutant"].encode(), 1)
            if args.execute:
                (repo / "node_modules").mkdir()
                for dependency in dependencies.iterdir():
                    if dependency.name not in {".cache", ".vite", ".vite-temp"}:
                        (repo / "node_modules" / dependency.name).symlink_to(dependency, target_is_directory=dependency.is_dir())
            states = {}
            for state, content in (("normal", normal), ("mutant", mutant)):
                production.write_bytes(content)
                required = config["normal_source_sha256"] if state == "normal" else spec["mutant_source_sha256"]
                if hashes(repo, config["production_paths"]) != required:
                    raise ValueError("Production state hash mismatch")
                if hashes(repo, targets) != test_hashes:
                    raise ValueError("Test files changed between normal and mutant")
                states[state] = execute_jest(repo, targets, repo.parent / (state + ".json"), args.timeout_seconds) if args.execute else {
                    "source_hashes_verified": True, "test_hashes_verified": True}
            production.write_bytes(normal)
            if suite == "probe":
                record["admission_probe"] = states
            else:
                record["suite_runs"][suite] = {"test_files": targets, "test_sha256": test_hashes, "states": states}
        records.append(record)
    result = {"mode": "execute" if args.execute else "prepare_only", "rows": records}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"mode": result["mode"], "behaviors": len(records),
                      "prepared_workspaces": len(records) * 5, "jest_executed": args.execute}))


if __name__ == "__main__":
    main()
