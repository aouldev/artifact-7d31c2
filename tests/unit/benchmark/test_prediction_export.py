"""Verify patch preservation and production-file boundaries with real Git."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "experiments/agents"))
from prediction_export import derive_prediction, parse_actor_output, workspace_patch, write_prediction


@pytest.fixture
def case(tmp_path):
    repo = tmp_path / "repos/demo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src/core.js").write_text("exports.value = 1;\n")
    (repo / "tests/core.js").write_text("// Base test\n")
    (repo / "old-support.txt").write_text("Old support\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Artifact Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "Fixture"], check=True)
    base = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    workspace = tmp_path / "workspace"
    shutil.copytree(repo, workspace, ignore=shutil.ignore_patterns(".git"))
    episode = {"episode_id": "demo__1", "repo": "demo", "base_commit": base,
               "prod_files": ["src/core.js"], "prod_diff": "Fixture production diff\n"}
    return repo, workspace, episode


def test_export_includes_support_additions_deletions_and_binary_without_mutating_source(case, tmp_path):
    repo, workspace, episode = case
    before = (repo / ".git/index").read_bytes()
    (workspace / "tests/core.js").write_text("// Updated test\n")
    (workspace / "old-support.txt").unlink()
    (workspace / "fixture.bin").write_bytes(b"\x00\xff\x01")
    patch = workspace_patch(episode, repo, workspace)
    assert "tests/core.js" in patch and "deleted file" in patch and "GIT binary patch" in patch
    replay = tmp_path / "replay"
    subprocess.run(["git", "clone", "-q", str(repo), str(replay)], check=True)
    subprocess.run(["git", "-C", str(replay), "apply", "--binary", "-"], input=patch.encode(), check=True)
    assert (replay / "fixture.bin").read_bytes() == b"\x00\xff\x01"
    assert not (replay / "old-support.txt").exists()
    assert (repo / ".git/index").read_bytes() == before
    assert not subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"])


@pytest.mark.parametrize("change", ["edit", "delete", "mode", "symlink"])
def test_export_rejects_production_changes(case, change):
    repo, workspace, episode = case
    path = workspace / "src/core.js"
    if change == "edit":
        path.write_text("exports.value = 2;\n")
    elif change == "delete":
        path.unlink()
    elif change == "mode":
        path.chmod(0o755)
    else:
        path.unlink()
        path.symlink_to("../tests/core.js")
    with pytest.raises(ValueError, match="Production"):
        workspace_patch(episode, repo, workspace)


def test_export_rejects_negative_with_edits_and_duplicate_append(case, tmp_path):
    repo, workspace, episode = case
    final = tmp_path / "final.json"
    final.write_text(json.dumps({"maintenance_label": "negative", "rationale": "Fixture"}))
    prediction = derive_prediction(agent="codex", episode=episode, source_repo=repo,
                                   workspace=workspace, final_output=final)
    output = tmp_path / "predictions.jsonl"
    write_prediction(output, prediction)
    with pytest.raises(ValueError, match="already contains"):
        write_prediction(output, prediction, append=True)
    output.write_text("null\n")
    with pytest.raises(ValueError, match="invalid record"):
        write_prediction(output, prediction, append=True)
    (workspace / "support.txt").write_text("Support\n")
    with pytest.raises(ValueError, match="Negative decision"):
        derive_prediction(agent="codex", episode=episode, source_repo=repo,
                          workspace=workspace, final_output=final)


@pytest.mark.parametrize("label", [[], None, 1, "unknown"])
def test_export_rejects_invalid_labels(tmp_path, label):
    output = tmp_path / "final.json"
    output.write_text(json.dumps({"maintenance_label": label, "rationale": "Fixture"}))
    with pytest.raises(ValueError, match="invalid label"):
        parse_actor_output("codex", output)


@pytest.mark.parametrize("entry", ["external_symlink", "git_metadata"])
def test_export_rejects_unsafe_workspace_entries(case, tmp_path, entry):
    repo, workspace, episode = case
    if entry == "external_symlink":
        (workspace / "escape").symlink_to(tmp_path / "outside")
    else:
        (workspace / ".git").mkdir()
    with pytest.raises(ValueError):
        workspace_patch(episode, repo, workspace)


@pytest.mark.parametrize("mode", ["stateless", "continuous", "online"])
@pytest.mark.parametrize("agent", ["codex", "opencode"])
def test_runners_export_each_episode_before_workspace_reset(case, tmp_path, mode, agent):
    repo, _, episode = case
    episodes = tmp_path / "episodes.jsonl"
    rows = [{**episode, "episode_id": f"demo__{i}"} for i in (1, 2)]
    episodes.write_text("".join(json.dumps(row) + "\n" for row in rows))
    client = tmp_path / "fixture-client"
    client.write_text(f"#!{sys.executable}\n" + '''import json,sys
from pathlib import Path
args=sys.argv[1:]
directory=next((args[args.index(flag)+1] for flag in ('--cd','--dir','-C') if flag in args), str(Path.cwd()))
repo=Path(directory)/'repo'
(repo/'support.txt').write_text('Episode support\\n')
answer={'maintenance_label':'positive','rationale':'Fixture client output'}
if '--output-last-message' in args:
    Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(answer))
    print(json.dumps({'type':'thread.started','thread_id':'fixture-session'}))
else:
    print(json.dumps({'type':'text','sessionID':'fixture-session','part':{'type':'text','text':json.dumps(answer)}}))
''')
    client.chmod(0o755)
    auth = tmp_path / "fixture-auth.json"
    auth.write_text('{"openai":{"type":"oauth"}}\n')
    catalog = tmp_path / "catalog.json"
    catalog.write_text("[]\n")
    runner = ROOT / f"experiments/agents/{mode}/{mode}_runner.py"
    command = [sys.executable, str(runner), "--agent", agent, "--repos-root", str(repo.parent),
               "--run-root", str(tmp_path / "runs"), "--codex-bin", str(client),
               "--opencode-bin", str(client), "--opencode-auth", str(auth), "--export-predictions"]
    if mode == "stateless":
        command += ["--episode", str(episodes), "--episode-id", "demo__1"]
        prediction_path = tmp_path / "runs/demo__1/predictions.jsonl"
    else:
        command += ["--episodes", str(episodes), "--repo", "demo"]
        prediction_path = tmp_path / "runs/demo/predictions.jsonl"
    if mode == "online":
        command += ["--memory-catalog", str(catalog)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    predictions = [json.loads(line) for line in prediction_path.read_text().splitlines()]
    assert [row["episode_id"] for row in predictions] == [row["episode_id"] for row in rows[:len(predictions)]]
    assert len(predictions) == (1 if mode == "stateless" else 2)
    assert all("support.txt" in row["result"]["test_patch"] for row in predictions)
