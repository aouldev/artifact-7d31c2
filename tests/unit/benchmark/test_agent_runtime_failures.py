"""Exercise timeout, session, and reflection failure handling without API calls."""

from argparse import Namespace
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stream(tmp_path):
    repo = tmp_path / "repos/demo"
    repo.mkdir(parents=True)
    (repo / "core.js").write_text("exports.value = 1;\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Artifact Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "Fixture"], check=True)
    base = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    episodes = tmp_path / "episodes.jsonl"
    episodes.write_text("".join(json.dumps({"episode_id": f"demo__{i}", "repo": "demo",
        "base_commit": base, "prod_files": ["core.js"], "prod_diff": "Fixture production diff\n"}) + "\n"
        for i in (1, 2)))
    return episodes, tmp_path / "repos"


def test_stateful_timeout_preserves_partial_utf8_logs(tmp_path):
    module = load("artifact_timeout_runtime", "experiments/agents/stateful_runtime.py")
    result = module.run_process([sys.executable, "-c",
        "import sys,time;print('partial output \\u00e9',flush=True);print('partial error',file=sys.stderr,flush=True);time.sleep(5)"],
        prompt="fixture", cwd=tmp_path, env=os.environ.copy(), timeout=1,
        stdout_path=tmp_path / "stdout.txt", stderr_path=tmp_path / "stderr.txt")
    assert result["status"] == "timeout" and result["returncode"] is None
    assert (tmp_path / "stdout.txt").read_text() == "partial output é\n"
    assert (tmp_path / "stderr.txt").read_text() == "partial error\n"


def test_stateless_timeout_writes_summary_and_partial_logs(tmp_path, stream, monkeypatch):
    client = tmp_path / "slow-client"
    client.write_text(f"#!{sys.executable}\nimport sys,time\nsys.stdin.read()\nprint('partial event',flush=True)\nprint('partial error',file=sys.stderr,flush=True)\ntime.sleep(10)\n")
    client.chmod(0o755)
    module = load("artifact_stateless_timeout", "experiments/agents/stateless/stateless_runner.py")
    monkeypatch.setattr(module, "TIMEOUT_SECONDS", 3)
    episodes, repos = stream
    args = Namespace(agent="codex", episode=episodes, episode_id="demo__1", repos_root=repos,
        run_root=tmp_path / "runs", codex_bin=str(client), opencode_bin="unused", opencode_auth=None,
        dry_run=False, exist_ok=False)
    with pytest.raises(SystemExit) as caught:
        module.run_episode(args)
    assert caught.value.code == 124
    output = args.run_root / "demo__1"
    assert json.loads((output / "summary.json").read_text())["status"] == "timeout"
    assert (output / "events.jsonl").read_text() == "partial event\n"
    assert (output / "stderr.log").read_text() == "partial error\n"


@pytest.mark.parametrize("drift", [False, True])
def test_continuous_requires_persisted_session_and_rejects_drift(tmp_path, stream, monkeypatch, drift):
    client = tmp_path / "session-client"
    client.write_text(f"#!{sys.executable}\n" + '''import json,os,sys
from pathlib import Path
args=sys.argv[1:];store=Path(os.environ['REPOTEM_TEST_SESSION_STORE']);store.mkdir(exist_ok=True)
if 'resume' in args:
    session=args[args.index('resume')+1]
    if not (store/session).exists():
        print('Session was not persisted',file=sys.stderr);sys.exit(2)
    if os.environ.get('REPOTEM_TEST_SESSION_DRIFT')=='1':session='unexpected-session'
else:
    session='fixture-session'
    if '--ephemeral' not in args:(store/session).write_text('fixture history')
configs=[args[i+1] for i,v in enumerate(args[:-1]) if v=='-c']
if any(value.startswith('model_providers.openai.') for value in configs):
    print('Built-in provider cannot be overridden',file=sys.stderr);sys.exit(3)
memory=Path.cwd().parent/'memory'/'counter.txt'
memory.write_text(str(int(memory.read_text())+1) if memory.exists() else '1')
Path(args[args.index('--output-last-message')+1]).write_text(json.dumps({'maintenance_label':'negative','test_patch':'','rationale':'Fixture'}))
print(json.dumps({'type':'thread.started','thread_id':session}),flush=True)
''')
    client.chmod(0o755)
    monkeypatch.syspath_prepend(str(ROOT / "experiments/agents"))
    monkeypatch.setenv("REPOTEM_TEST_SESSION_STORE", str(tmp_path / "sessions"))
    monkeypatch.setenv("REPOTEM_TEST_SESSION_DRIFT", "1" if drift else "0")
    module = load("artifact_continuous_runtime", "experiments/agents/continuous/continuous_runner.py")
    episodes, repos = stream
    result = module.run_stream(Namespace(agent="codex", episodes=episodes, repo="demo", repos_root=repos,
        run_root=tmp_path / "runs", train_root=None, codex_bin=str(client), opencode_bin="unused",
        opencode_auth=None, limit=2, dry_run=False, allow_existing=False))
    assert len(result["episodes"]) == 2
    assert result["episodes"][0]["status"] == "ok"
    assert result["episodes"][1]["status"] == ("session_error" if drift else "ok")
    assert result["session_id"] == "fixture-session"
    assert (tmp_path / "runs/demo/memory/counter.txt").read_text() == "2"


def reflection_update():
    return {"operation": "add", "target_memory_id": "", "memory_type": "semantic",
            "anchor_path": "src/core.ts", "trigger": "Fixture change", "claim": "Inspect the core test.",
            "inspect_paths": ["tests/core.test.ts"], "links": [], "evidence_paths": ["src/core.ts"]}


def test_reflection_parses_fenced_updates_without_selecting_nested_objects():
    module = load("artifact_reflection_json", "experiments/agents/online/reflection_runner.py")
    update = reflection_update()
    update["links"] = [{"relation": "observed_by", "target_path": "tests/core.test.ts", "reason": "Fixture evidence"}]
    payload = {"memory_updates": [update]}
    text = "Candidate update:\n```json\n" + json.dumps(payload) + "\n```\n"
    assert module.validate_updates(module._parse_json_object(text)) == payload


@pytest.mark.parametrize("key,value", [
    ("operation", []), ("target_memory_id", 1), ("anchor_path", None),
    ("inspect_paths", [1]), ("links", [{"relation": "unknown", "target_path": "tests/core.ts", "reason": "Fixture"}]),
])
def test_reflection_rejects_malformed_candidate_fields(key, value):
    module = load("artifact_reflection_validation", "experiments/agents/online/reflection_runner.py")
    update = reflection_update()
    update[key] = value
    with pytest.raises(ValueError):
        module.validate_updates({"memory_updates": [update]})


@pytest.mark.parametrize("agent", ["codex", "opencode"])
def test_session_parser_ignores_nonobject_diagnostics(agent):
    module = load("artifact_session_parser", "experiments/agents/stateful_runtime.py")
    event = {"type": "thread.started", "thread_id": "fixture-session"} if agent == "codex" else {"sessionID": "fixture-session"}
    content = "null\n[]\n42\n" + json.dumps(event) + "\n"
    parser = getattr(module, f"session_id_from_{agent}_events")
    assert parser(content) == "fixture-session"


def test_continuous_records_ambiguous_session_failure(tmp_path, stream, monkeypatch):
    client = tmp_path / "ambiguous-client"
    client.write_text(f"#!{sys.executable}\nimport json\nfor session in ('first','second'):\n print(json.dumps({{'type':'text','sessionID':session}}),flush=True)\n")
    client.chmod(0o755)
    auth = tmp_path / "fixture-auth.json"
    auth.write_text('{"openai":{"type":"oauth"}}\n')
    monkeypatch.syspath_prepend(str(ROOT / "experiments/agents"))
    module = load("artifact_continuous_ambiguous", "experiments/agents/continuous/continuous_runner.py")
    episodes, repos = stream
    result = module.run_stream(Namespace(agent="opencode", episodes=episodes, repo="demo", repos_root=repos,
        run_root=tmp_path / "runs", train_root=None, codex_bin="unused", opencode_bin=str(client),
        opencode_auth=auth, limit=2, dry_run=False, allow_existing=False))
    assert len(result["episodes"]) == 1 and result["episodes"][0]["status"] == "session_error"
    assert result["session_id"] is None
    assert (tmp_path / "runs/demo/stream_summary.json").exists()
    assert not (tmp_path / "runs/demo/episodes/demo__2").exists()


def test_opencode_writer_receives_the_declared_output_schema(tmp_path):
    client = tmp_path / "schema-client"
    client.write_text(f"#!{sys.executable}\n" + '''import json,sys
prompt=sys.argv[-1]
marker='Required output JSON schema:\\n'
if marker in prompt:
    schema=json.loads(prompt.split(marker,1)[1])
    assert schema['required']==['memory_updates']
    fields=schema['properties']['memory_updates']['items']['required']
    assert 'memory_type' in fields and 'trigger' in fields
    text='{"memory_updates":[]}'
else:
    text='{"updates":[]}'
print(json.dumps({'type':'text','part':{'type':'text','text':text}}),flush=True)
''')
    client.chmod(0o755)
    files = {}
    for name, value in (("episode", {"episode_id": "fixture"}), ("records", []), ("observations", {}),
                        ("auth", {"openai": {"type": "oauth"}})):
        files[name] = tmp_path / f"{name}.json"
        files[name].write_text(json.dumps(value))
    diff = tmp_path / "prod.diff"
    diff.write_text("Fixture production diff\n")
    module = load("artifact_writer_schema", "experiments/agents/online/reflection_runner.py")
    result = module.run_writer(Namespace(agent="opencode", episode=files["episode"], prod_diff=diff,
        retrieved_records=files["records"], base_observations=files["observations"],
        run_root=tmp_path / "run", codex_bin="unused", opencode_bin=str(client), opencode_auth=files["auth"],
        dry_run=False, allow_existing=False))
    assert result["status"] == "ok" and result["accepted_by_host"] is False
    assert json.loads((tmp_path / "run/updates.json").read_text()) == {"memory_updates": []}
