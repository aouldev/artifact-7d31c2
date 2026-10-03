#!/usr/bin/env python3
"""Convert actor output and workspace edits into the evaluator contract."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from typing import Any


def git(arguments: list[str], *, env: dict[str, str] | None = None) -> bytes:
    if env is None:
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(["git", *arguments], env=env, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def parse_actor_output(agent: str, path: Path) -> dict[str, Any]:
    if agent not in {"codex", "opencode"}:
        raise ValueError("Unsupported actor client")
    text = path.read_text(encoding="utf-8")
    if agent == "opencode":
        texts = []
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            part = event.get("part")
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif event.get("type") == "text" and isinstance(event.get("text"), str):
                texts.append(event["text"])
        if not texts:
            raise ValueError("OpenCode output contains no final text")
        text = texts[-1]
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {"maintenance_label", "rationale"}:
        raise ValueError("Actor output must contain only maintenance_label and rationale")
    if (not isinstance(value["maintenance_label"], str)
            or value["maintenance_label"] not in {"positive", "negative"}
            or not isinstance(value["rationale"], str)):
        raise ValueError("Actor output has an invalid label or rationale")
    return value


def relative_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"..", ".git"} for part in path.parts):
        raise ValueError("Production paths must be repository-relative")
    if any(ord(character) < 32 for character in value):
        raise ValueError("Production path contains a control character")
    return path.as_posix()


def workspace_patch(episode: dict[str, Any], source_repo: Path, workspace: Path) -> str:
    source_repo, workspace = source_repo.resolve(), workspace.resolve()
    if not workspace.is_dir():
        raise ValueError("Workspace must be a directory")
    if not isinstance(episode.get("prod_files"), list) or not all(isinstance(p, str) for p in episode["prod_files"]):
        raise ValueError("Episode prod_files must be a list of paths")
    production = {relative_path(p) for p in episode["prod_files"]}
    for parent, directories, files in os.walk(workspace, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            if name == ".git":
                raise ValueError("Actor workspace must not contain Git metadata")
            if path.is_symlink() and not path.resolve().is_relative_to(workspace):
                raise ValueError("Workspace symlink leaves the repository")
    base = git(["-C", str(source_repo), "rev-parse", "--verify", "--end-of-options",
                str(episode["base_commit"]) + "^{commit}"]).decode().strip()
    common = Path(git(["-C", str(source_repo), "rev-parse", "--git-common-dir"]).decode().strip())
    common = (source_repo / common).resolve() if not common.is_absolute() else common.resolve()
    for relative in production:
        entries = git(["--literal-pathspecs", "-C", str(source_repo), "ls-tree", "-z", base, "--", relative]).split(b"\0")
        entries = [entry for entry in entries if entry]
        final = workspace / relative
        if not entries:
            if final.exists() or final.is_symlink():
                raise ValueError(f"Production path was created: {relative}")
            continue
        mode, kind, object_id = entries[0].split(b"\t", 1)[0].split()
        if kind != b"blob":
            raise ValueError("Production paths must identify files")
        original = git(["-C", str(source_repo), "cat-file", "blob", object_id.decode()])
        if mode == b"120000":
            if not final.is_symlink() or os.fsencode(os.readlink(final)) != original:
                raise ValueError(f"Production symlink changed: {relative}")
        elif (not final.is_file() or final.is_symlink() or final.read_bytes() != original
              or bool(final.stat().st_mode & 0o111) != (mode == b"100755")):
            raise ValueError(f"Production file changed: {relative}")
    with tempfile.TemporaryDirectory(prefix="repotem-export-") as temporary:
        git_dir = Path(temporary) / "git"
        git(["init", "--bare", "--quiet", str(git_dir)])
        environment = {
            **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_INDEX_FILE": str(Path(temporary) / "index"),
            "GIT_OBJECT_DIRECTORY": str(git_dir / "objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": json.dumps(str(common / "objects")),
        }
        command = ["--git-dir", str(git_dir), "--work-tree", str(workspace),
                   "-c", "core.filemode=true", "-c", "core.autocrlf=false"]
        git([*command, "read-tree", base], env=environment)
        git([*command, "add", "--all", "--force", "--", "."], env=environment)
        changed = {path.decode() for path in git([*command, "diff", "--cached", "--name-only", "-z", base], env=environment).split(b"\0") if path}
        if changed & production:
            raise ValueError("Derived patch changes production files")
        return git([*command, "diff", "--cached", "--binary", "--no-ext-diff", "--no-renames",
                    "--src-prefix=a/", "--dst-prefix=b/", base], env=environment).decode("utf-8")


def derive_prediction(*, agent: str, episode: dict[str, Any], source_repo: Path,
                      workspace: Path, final_output: Path) -> dict[str, Any]:
    for key in ("episode_id", "repo", "base_commit"):
        if not isinstance(episode.get(key), str) or not episode[key].strip():
            raise ValueError(f"Episode requires {key}")
    result = parse_actor_output(agent, final_output)
    patch = workspace_patch(episode, source_repo, workspace)
    if result["maintenance_label"] == "negative" and patch:
        raise ValueError("Negative decision conflicts with nonempty workspace edits")
    return {key: episode[key] for key in ("episode_id", "repo", "base_commit")} | {
        "result": {**result, "test_patch": patch}}


def write_prediction(path: Path, prediction: dict[str, Any], *, append: bool = False) -> None:
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    if path.exists() and not append:
        raise FileExistsError("Prediction output already exists; choose a new file")
    for line in previous.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get("episode_id"), str):
            raise ValueError("Existing prediction output contains an invalid record")
        if row["episode_id"] == prediction["episode_id"]:
            raise ValueError("Prediction output already contains this episode")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
        candidate = Path(temporary.name)
        temporary.write(previous + ("\n" if previous and not previous.endswith("\n") else ""))
        temporary.write(json.dumps(prediction, ensure_ascii=False) + "\n")
    try:
        candidate.replace(path)
    finally:
        candidate.unlink(missing_ok=True)


def export_case(*, agent: str, episode: dict[str, Any], source_repo: Path,
                workspace: Path, case_root: Path, output: Path, append: bool = False) -> None:
    if output.resolve().is_relative_to(workspace.resolve()) or output.resolve().is_relative_to(source_repo.resolve()):
        raise ValueError("Prediction output must be outside source and actor repositories")
    final_output = case_root / ("last_message.json" if agent == "codex" else "events.jsonl")
    prediction = derive_prediction(agent=agent, episode=episode, source_repo=source_repo,
                                   workspace=workspace, final_output=final_output)
    write_prediction(output, prediction, append=append)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("codex", "opencode"), required=True)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, help="Defaults to CASE_DIR/workspace/repo")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--append", action="store_true")
    args = parser.parse_args()
    export_case(agent=args.agent, episode=json.loads((args.case_dir / "episode.json").read_text()),
                source_repo=args.source_repo, workspace=args.workspace or args.case_dir / "workspace/repo",
                case_root=args.case_dir, output=args.output, append=args.append)
    print(json.dumps({"status": "exported", "output": str(args.output)}))
