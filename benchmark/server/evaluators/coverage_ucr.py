"""Coverage-backed UCR checks for dynamic patch evaluation."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from benchmark.participant.core.paths import get_coverage_parser_path


RAW_COVERAGE_PARSE_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class CoverageUcrResult:
    ucr_status: str
    coverage: dict[str, Any] = field(default_factory=dict)


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_DA_RE = re.compile(r"^DA:(\d+),(-?\d+)")


def _clean_diff_path(path: str) -> str | None:
    value = path.strip().split("	", 1)[0]
    if value == "/dev/null":
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    if value.startswith("a/") or value.startswith("b/"):
        value = value[2:]
    return _normalize_relative_path(value)


def _normalize_relative_path(path: str) -> str:
    value = path.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.lstrip("/")


def changed_lines_from_diff(
    prod_diff: str, prod_files: list[str] | None = None
) -> dict[str, set[int]]:
    """Return new-version changed line numbers by repo-relative production file."""

    allowed_files = {_normalize_relative_path(path) for path in prod_files or []}
    changed: dict[str, set[int]] = {}
    current_path: str | None = None
    new_line: int | None = None

    for diff_line in prod_diff.splitlines():
        if diff_line.startswith("diff --git "):
            current_path = None
            new_line = None
            continue
        if diff_line.startswith("+++ "):
            current_path = _clean_diff_path(diff_line[4:])
            if allowed_files and current_path not in allowed_files:
                current_path = None
            new_line = None
            continue
        hunk_match = _HUNK_RE.match(diff_line)
        if hunk_match:
            new_line = int(hunk_match.group(1))
            continue
        if current_path is None or new_line is None:
            continue
        if diff_line.startswith("+") and not diff_line.startswith("+++"):
            changed.setdefault(current_path, set()).add(new_line)
            new_line += 1
        elif diff_line.startswith("-") and not diff_line.startswith("---"):
            continue
        elif diff_line.startswith("\\"):
            continue
        else:
            new_line += 1

    return changed


def changed_files_from_diff(prod_diff: str, prod_files: list[str] | None = None) -> set[str]:
    """Return changed production files, including deletion-only hunks."""

    allowed_files = {_normalize_relative_path(path) for path in prod_files or []}
    changed: set[str] = set()
    current_path: str | None = None
    saw_hunk_change = False

    for diff_line in prod_diff.splitlines():
        if diff_line.startswith("diff --git "):
            if current_path and saw_hunk_change:
                changed.add(current_path)
            current_path = None
            saw_hunk_change = False
            continue
        if diff_line.startswith("+++ "):
            current_path = _clean_diff_path(diff_line[4:])
            if allowed_files and current_path not in allowed_files:
                current_path = None
            saw_hunk_change = False
            continue
        if current_path is None:
            continue
        if (diff_line.startswith("+") and not diff_line.startswith("+++")) or (
            diff_line.startswith("-") and not diff_line.startswith("---")
        ):
            saw_hunk_change = True

    if current_path and saw_hunk_change:
        changed.add(current_path)
    if not changed and allowed_files:
        return set(allowed_files)
    return changed


def _normalize_coverage_path(raw_path: str, repo_root: Path) -> str | None:
    if not raw_path:
        return None
    value = str(raw_path).strip()
    if value.startswith("file://"):
        parsed = urlparse(value)
        value = unquote(parsed.path)
    value = value.split("?", 1)[0].split("#", 1)[0]
    value = value.replace("\\", "/")
    if value.startswith("/@fs/"):
        value = value[4:]
    if value.startswith("webpack://"):
        return None

    repo_root = repo_root.resolve()
    try:
        path = Path(value)
        if path.is_absolute():
            return _normalize_relative_path(str(path.resolve().relative_to(repo_root)))
    except (OSError, ValueError):
        pass

    root_text = str(repo_root).replace("\\", "/")
    marker = f"{root_text}/"
    if marker in value:
        return _normalize_relative_path(value.split(marker, 1)[1])

    return _normalize_relative_path(value)


def _add_line_hit(hits: dict[str, dict[int, int]], path: str | None, line: int, hit: int) -> None:
    if not path or line <= 0:
        return
    file_hits = hits.setdefault(path, {})
    file_hits[line] = max(file_hits.get(line, 0), hit)


def _int_value(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_istanbul_json(path: Path, repo_root: Path) -> dict[str, dict[int, int]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    hits: dict[str, dict[int, int]] = {}
    for file_key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        rel_path = _normalize_coverage_path(str(entry.get("path") or file_key), repo_root)
        statement_map = entry.get("statementMap")
        statement_hits = entry.get("s")
        if not isinstance(statement_map, dict) or not isinstance(statement_hits, dict):
            continue
        for statement_id, location in statement_map.items():
            if not isinstance(location, dict):
                continue
            start = location.get("start")
            end = location.get("end") or start
            if not isinstance(start, dict) or not isinstance(end, dict):
                continue
            start_line = _int_value(start.get("line"))
            end_line = _int_value(end.get("line")) or start_line
            hit = _int_value(statement_hits.get(statement_id)) or 0
            if start_line is None or end_line is None:
                continue
            for line in range(start_line, max(start_line, end_line) + 1):
                _add_line_hit(hits, rel_path, line, hit)
    return hits


def _parse_lcov(path: Path, repo_root: Path) -> dict[str, dict[int, int]]:
    hits: dict[str, dict[int, int]] = {}
    current_path: str | None = None
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if line.startswith("SF:"):
            current_path = _normalize_coverage_path(line[3:], repo_root)
            continue
        match = _DA_RE.match(line)
        if match and current_path:
            _add_line_hit(hits, current_path, int(match.group(1)), int(match.group(2)))
    return hits


def _repo_owned_source_path(raw_url: str, repo_root: Path) -> tuple[str | None, Path | None]:
    if not raw_url:
        return None, None
    value = raw_url.strip()
    if value.startswith("node:") or value.startswith("webpack://"):
        return None, None
    if value.startswith("http://") or value.startswith("https://"):
        return None, None
    if value.startswith("file://"):
        parsed = urlparse(value)
        value = unquote(parsed.path)
    value = value.split("?", 1)[0].split("#", 1)[0]
    value = value.replace("\\", "/")
    if value.startswith("/@fs/"):
        value = value[4:]

    try:
        path = Path(value)
        if path.is_absolute():
            resolved = path.resolve()
            rel = _normalize_relative_path(str(resolved.relative_to(repo_root.resolve())))
            if _is_ignored_coverage_path(rel):
                return None, None
            if resolved.is_file():
                return rel, resolved
            return rel, None
    except (OSError, ValueError):
        return None, None

    candidate = (repo_root / _normalize_relative_path(value)).resolve()
    try:
        rel = _normalize_relative_path(str(candidate.relative_to(repo_root.resolve())))
    except ValueError:
        return None, None
    if _is_ignored_coverage_path(rel):
        return None, None
    return (rel, candidate) if candidate.is_file() else (rel, None)


def _is_ignored_coverage_path(path: str) -> bool:
    normalized = _normalize_relative_path(path).lower()
    return normalized.startswith("node_modules/") or "/node_modules/" in normalized


def _line_starts(source: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(source):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _line_for_offset(starts: list[int], offset: int) -> int:
    low = 0
    high = len(starts)
    while low < high:
        mid = (low + high) // 2
        if starts[mid] <= offset:
            low = mid + 1
        else:
            high = mid
    return max(1, low)


def _raw_v8_ranges(entry: dict[str, Any]) -> list[tuple[int, int, int]]:
    ranges: list[tuple[int, int, int]] = []
    for function in entry.get("functions") or []:
        if not isinstance(function, dict):
            continue
        for range_data in function.get("ranges") or []:
            if not isinstance(range_data, dict):
                continue
            start = _int_value(range_data.get("startOffset"))
            end = _int_value(range_data.get("endOffset"))
            count = _int_value(range_data.get("count")) or 0
            if start is None or end is None or end <= start or count <= 0:
                continue
            ranges.append((start, end, count))
    return ranges


def _parse_raw_v8_native(path: Path, repo_root: Path) -> dict[str, dict[int, int]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw if isinstance(raw, list) else raw.get("result") if isinstance(raw, dict) else []
    if not isinstance(entries, list):
        return {}

    hits: dict[str, dict[int, int]] = {}
    source_cache: dict[Path, tuple[str, list[int]]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rel_path, source_path = _repo_owned_source_path(str(entry.get("url") or ""), repo_root)
        if not rel_path or source_path is None:
            continue
        ranges = _raw_v8_ranges(entry)
        if not ranges:
            continue
        if source_path not in source_cache:
            source = source_path.read_text(encoding="utf-8", errors="replace")
            source_cache[source_path] = (source, _line_starts(source))
        source, starts = source_cache[source_path]
        source_len = len(source)
        for start, end, count in ranges:
            start = min(max(start, 0), source_len)
            end = min(max(end - 1, start), max(source_len - 1, 0))
            start_line = _line_for_offset(starts, start)
            end_line = _line_for_offset(starts, end)
            for line in range(start_line, end_line + 1):
                _add_line_hit(hits, rel_path, line, count)
    return hits


def _covered_files_from_hits(hits: dict[str, dict[int, int]]) -> set[str]:
    return {
        path
        for path, line_hits in hits.items()
        if not _is_ignored_coverage_path(path) and any(hit > 0 for hit in line_hits.values())
    }


def _path_matches(candidate: str, target: str) -> bool:
    candidate = _normalize_relative_path(candidate)
    target = _normalize_relative_path(target)
    return (
        candidate == target or candidate.endswith(f"/{target}") or target.endswith(f"/{candidate}")
    )


def _matching_covered_files(targets: set[str], covered_files: set[str]) -> dict[str, set[str]]:
    matches: dict[str, set[str]] = {}
    for target in targets:
        matched = {covered for covered in covered_files if _path_matches(covered, target)}
        if matched:
            matches[target] = matched
    return matches


def _matching_hit_path(path: str, hits: dict[str, dict[int, int]]) -> str | None:
    if path in hits:
        return path
    for candidate in hits:
        if _path_matches(candidate, path):
            return candidate
    return None


def _raw_v8_kind(path: Path) -> str:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return "raw-v8"
    entries = raw if isinstance(raw, list) else raw.get("result") if isinstance(raw, dict) else []
    if not isinstance(entries, list):
        return "raw-v8"
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "")
        if url.startswith(("http://", "https://", "about://")):
            return "browser-raw"
    return "raw-v8"


def _default_build_roots(repo_root: Path, coverage_root: Path) -> list[str]:
    candidates = [
        repo_root,
        coverage_root,
        repo_root / "dist",
        repo_root / "build",
        repo_root / ".next",
        repo_root / "apps",
        repo_root / "packages",
        repo_root / "apps" / "web" / ".next",
        repo_root / "apps" / "web" / "dist",
        repo_root / "apps" / "studio" / ".next",
        repo_root / "apps" / "studio" / "dist",
    ]
    return [str(path.resolve()) for path in candidates if path.exists()]


def _parse_raw_v8_remapped_files(
    artifacts: list[Path],
    kind: str,
    repo_root: Path,
    coverage_root: Path,
) -> tuple[set[str], list[str]]:
    parser_path = get_coverage_parser_path()
    if not parser_path.exists():
        return set(), ["missing_coverage_parser"]
    config = {
        "kind": kind,
        "artifacts": [str(artifact.resolve()) for artifact in artifacts],
        "repoRoot": str(repo_root.resolve()),
        "executionRepoRoot": str(repo_root.resolve()),
        "coverageCwd": str(coverage_root.resolve()),
        "cwd": str(repo_root.resolve()),
        "buildRoots": _default_build_roots(repo_root, coverage_root),
        "remapRoots": _default_build_roots(repo_root, coverage_root),
    }
    script = f"""
import {{ parseCoverageArtifact }} from '{parser_path.resolve()}';
const config = JSON.parse(process.argv[1]);
const result = await parseCoverageArtifact(config);
console.log(JSON.stringify(result));
"""
    env = os.environ.copy()
    env.setdefault("REPO_TEST_EVOLUTION_PARSE_BROWSER_RAW", "1")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(config)],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        timeout=RAW_COVERAGE_PARSE_TIMEOUT_SECONDS,
        check=False,
    )
    if proc.returncode != 0:
        return set(), [f"raw_v8_remap_failed:{proc.stderr.strip()[:500]}"]
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return set(), [f"raw_v8_remap_bad_json:{exc}"]
    files = {
        _normalize_relative_path(str(item))
        for item in result.get("coveredProdFiles") or []
        if isinstance(item, str) and not _is_ignored_coverage_path(item)
    }
    notes = [str(item) for item in result.get("notes") or []]
    status = str(result.get("status") or "unknown")
    return files, [f"raw_v8_remap_status:{status}", *notes]


def _merge_hits(target: dict[str, dict[int, int]], source: dict[str, dict[int, int]]) -> None:
    for path, line_hits in source.items():
        for line, hit in line_hits.items():
            _add_line_hit(target, path, line, hit)


def _coverage_artifacts(coverage_root: Path) -> tuple[list[Path], list[Path], list[Path]]:
    if not coverage_root.exists():
        return [], [], []
    istanbul = sorted(coverage_root.rglob("coverage-final.json"))
    lcov = sorted(coverage_root.rglob("lcov.info"))
    raw_v8 = [
        path
        for path in sorted(coverage_root.rglob("*.json"))
        if path.name != "coverage-final.json" and path.name.startswith("coverage-")
    ]
    return istanbul, lcov, raw_v8


def _load_coverage_hits(
    coverage_root: Path, repo_root: Path
) -> tuple[dict[str, dict[int, int]], set[str], list[str], list[str], list[str]]:
    istanbul_paths, lcov_paths, raw_v8_paths = _coverage_artifacts(coverage_root)
    hits: dict[str, dict[int, int]] = {}
    artifact_kinds: list[str] = []
    artifact_paths: list[str] = []
    covered_files: set[str] = set()
    errors: list[str] = []

    for artifact in istanbul_paths:
        try:
            parsed_hits = _parse_istanbul_json(artifact, repo_root)
            covered_files.update(_covered_files_from_hits(parsed_hits))
            _merge_hits(hits, parsed_hits)
            artifact_paths.append(str(artifact))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            errors.append(f"{artifact}: {exc}")
    if istanbul_paths:
        artifact_kinds.append("istanbul-json")

    for artifact in lcov_paths:
        try:
            parsed_hits = _parse_lcov(artifact, repo_root)
            covered_files.update(_covered_files_from_hits(parsed_hits))
            _merge_hits(hits, parsed_hits)
            artifact_paths.append(str(artifact))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            errors.append(f"{artifact}: {exc}")
    if lcov_paths:
        artifact_kinds.append("lcov")

    raw_native_hit_count = 0
    raw_v8_by_kind: dict[str, list[Path]] = {}
    for artifact in raw_v8_paths:
        try:
            raw_hits = _parse_raw_v8_native(artifact, repo_root)
            raw_native_hit_count += sum(len(line_hits) for line_hits in raw_hits.values())
            covered_files.update(_covered_files_from_hits(raw_hits))
            _merge_hits(hits, raw_hits)
            raw_v8_by_kind.setdefault(_raw_v8_kind(artifact), []).append(artifact)
            artifact_paths.append(str(artifact))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            errors.append(f"{artifact}: {exc}")
    for kind, artifacts in raw_v8_by_kind.items():
        remapped_files, remap_notes = _parse_raw_v8_remapped_files(
            artifacts, kind, repo_root, coverage_root
        )
        covered_files.update(remapped_files)
        errors.extend(remap_notes)
    if raw_v8_paths:
        if raw_native_hit_count:
            artifact_kinds.append("raw-v8-native")
        elif covered_files:
            artifact_kinds.append("raw-v8-remapped")
        else:
            artifact_kinds.append("raw-v8-unmapped")

    return hits, covered_files, artifact_kinds, artifact_paths, errors


def evaluate_update_coverage(
    *,
    prod_diff: str,
    prod_files: list[str],
    repo_root: str | Path,
    coverage_root: str | Path,
) -> CoverageUcrResult:
    """Evaluate ReAccept-style UCR using production-file coverage.

    The dataset construction pipeline selected positives with file-level production coverage.
    Line-level changed-code hits are retained as diagnostics, but the headline UCR follows
    the construction/prior-work interpretation: tests pass and cover the changed product file.
    """

    repo_root_path = Path(repo_root).resolve()
    coverage_root_path = Path(coverage_root).resolve()
    changed_lines = changed_lines_from_diff(prod_diff, prod_files)
    changed_file_set = changed_files_from_diff(prod_diff, prod_files)
    changed_location_count = sum(len(lines) for lines in changed_lines.values())
    coverage: dict[str, Any] = {
        "status": "not_run",
        "target_kind": "prod_diff_changed_lines",
        "changed_location_count": changed_location_count,
        "coverable_changed_location_count": 0,
        "covered_changed_location_count": 0,
        "changed_files": sorted(changed_file_set),
        "covered_changed_files": [],
        "uncovered_changed_files": sorted(changed_file_set),
    }

    if changed_location_count == 0 and not changed_file_set:
        coverage["status"] = "no_changed_locations"
        return CoverageUcrResult(ucr_status="failed", coverage=coverage)

    hits, covered_files, artifact_kinds, artifact_paths, errors = _load_coverage_hits(
        coverage_root_path, repo_root_path
    )
    coverage["artifact_kind"] = "+".join(artifact_kinds) if artifact_kinds else None
    coverage["artifacts"] = artifact_paths
    if errors:
        coverage["errors"] = errors

    if not artifact_kinds:
        coverage["status"] = "missing_artifact"
        return CoverageUcrResult(ucr_status="failed", coverage=coverage)
    if not hits and not covered_files:
        coverage["status"] = "parse_failed" if errors else "empty_artifact"
        return CoverageUcrResult(ucr_status="failed", coverage=coverage)

    coverable: dict[str, set[int]] = {}
    covered: dict[str, set[int]] = {}
    for path, lines in changed_lines.items():
        hit_path = _matching_hit_path(path, hits)
        if hit_path is None:
            continue
        file_hits = hits[hit_path]
        for line in lines:
            if line not in file_hits:
                continue
            coverable.setdefault(path, set()).add(line)
            if file_hits[line] > 0:
                covered.setdefault(path, set()).add(line)

    coverable_count = sum(len(lines) for lines in coverable.values())
    covered_count = sum(len(lines) for lines in covered.values())
    file_level_matches = _matching_covered_files(changed_file_set, covered_files)
    file_level_covered = set(file_level_matches)
    covered_file_set = set(covered) | file_level_covered
    coverage["coverable_changed_location_count"] = coverable_count
    coverage["covered_changed_location_count"] = covered_count
    coverage["line_level_covered_changed_files"] = sorted(covered)
    coverage["covered_changed_files"] = sorted(covered_file_set)
    coverage["uncovered_changed_files"] = sorted(changed_file_set - covered_file_set)
    coverage["covered_changed_file_count"] = len(covered_file_set)
    if file_level_matches:
        coverage["covered_file_matches"] = {
            target: sorted(matches) for target, matches in sorted(file_level_matches.items())
        }

    if file_level_covered:
        coverage["status"] = "parsed" if covered_count > 0 else "parsed_file"
        coverage["ucr_basis"] = "changed_file_coverage"
        if covered_count == 0:
            coverage["line_level_status"] = (
                "no_coverable_changed_locations"
                if coverable_count == 0
                else "uncovered_changed_locations"
            )
        return CoverageUcrResult(ucr_status="passed", coverage=coverage)

    if not any(_matching_hit_path(path, hits) for path in changed_lines):
        coverage["status"] = "no_covered_changed_files" if covered_files else "unmapped_paths"
        return CoverageUcrResult(ucr_status="failed", coverage=coverage)
    if coverable_count == 0:
        coverage["status"] = "no_coverable_changed_locations"
        return CoverageUcrResult(ucr_status="failed", coverage=coverage)

    coverage["status"] = "parsed"
    return CoverageUcrResult(ucr_status="failed", coverage=coverage)
