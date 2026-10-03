"""Benchmark runner."""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.participant.core.base_agent import BaseAgent
from benchmark.participant.core.context import AgentContext
from benchmark.participant.core.models import AgentResult, Episode, PredictionRecord
from benchmark.participant.core.trace import TraceRecorder
from benchmark.participant.loaders.dataset import EpisodeDataset, write_jsonl
from benchmark.participant.loaders.train_reader import TrainReader
from benchmark.participant.tools.gateway import ToolGateway
from benchmark.participant.tools.snapshot import GitCommitReader

FORBIDDEN_VALIDATION_TOP_LEVEL_KEYS = {
    "gold",
    "ground_truth",
    "maintenance_label",
    "episode_type",
    "test_change",
    "test_patch",
    "test_diff",
    "test_files",
    "test_files_changed",
}
FORBIDDEN_VALIDATION_METADATA_KEYS = {
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


@dataclass(frozen=True)
class RunConfig:
    train_path: Path
    episodes_path: Path
    repos_root: Path
    output_dir: Path
    agent_class: str
    run_id: str | None = None
    limit: int | None = None


def load_agent(class_path: str) -> BaseAgent:
    module_name, class_name = class_path.rsplit(":", 1)
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    agent = cls()
    if not isinstance(agent, BaseAgent):
        raise TypeError(f"{class_path} is not a BaseAgent")
    return agent


def _setup_logger(run_dir: Path) -> logging.Logger:
    logger = logging.getLogger(f"benchmark.{run_dir.name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    run_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(logs_dir / "runtime.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_train_episodes(episodes: list[Episode]) -> None:
    missing_gold = [episode.episode_id for episode in episodes if episode.gold is None]
    if missing_gold:
        raise ValueError(f"train episodes missing gold: {missing_gold[:5]}")


def _validate_validation_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        episode_id = row.get("episode_id", "<unknown>")
        forbidden = sorted(FORBIDDEN_VALIDATION_TOP_LEVEL_KEYS & set(row))
        if forbidden:
            raise ValueError(f"validation episode {episode_id} leaks forbidden fields: {forbidden}")
        metadata = row.get("metadata")
        if isinstance(metadata, dict):
            metadata_forbidden = sorted(FORBIDDEN_VALIDATION_METADATA_KEYS & set(metadata))
            if metadata_forbidden:
                raise ValueError(
                    f"validation episode {episode_id} leaks metadata fields: {metadata_forbidden}"
                )


def _validate_no_overlap(train: list[Episode], validation: list[Episode]) -> None:
    train_ids = {episode.episode_id for episode in train}
    validation_ids = {episode.episode_id for episode in validation}
    overlap = sorted(train_ids & validation_ids)
    if overlap:
        raise ValueError(f"train/validation episode_id overlap: {overlap[:5]}")


def _load_validation_dataset(path: Path) -> EpisodeDataset:
    from benchmark.participant.loaders.dataset import read_jsonl

    rows = read_jsonl(path)
    _validate_validation_rows(rows)
    return EpisodeDataset([Episode.from_dict(row) for row in rows])


class BenchmarkRunner:
    def __init__(self, config: RunConfig) -> None:
        self.config = config

    def run(self) -> dict[str, Any]:
        train_dataset = EpisodeDataset.from_jsonl(self.config.train_path)
        validation_dataset = _load_validation_dataset(self.config.episodes_path)
        if self.config.limit is not None:
            if self.config.limit <= 0:
                raise ValueError("limit must be positive")
            validation_dataset = EpisodeDataset(validation_dataset.episodes[: self.config.limit])
        _validate_train_episodes(train_dataset.episodes)
        _validate_no_overlap(train_dataset.episodes, validation_dataset.episodes)

        run_id = self.config.run_id or time.strftime("run_%Y%m%d_%H%M%S")
        run_dir = self.config.output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        logger = _setup_logger(run_dir)

        trace_recorder = TraceRecorder(run_dir / "traces")
        reader = GitCommitReader(self.config.repos_root)
        tools = ToolGateway(reader, trace_recorder)
        context = AgentContext(
            run_dir=run_dir,
            train_reader=TrainReader(train_dataset.episodes),
            tools=tools,
            logger=logger,
        )
        agent = load_agent(self.config.agent_class)

        config_payload = {
            "train_path": str(self.config.train_path),
            "episodes_path": str(self.config.episodes_path),
            "repos_root": str(self.config.repos_root),
            "agent_class": self.config.agent_class,
            "agent_name": agent.name,
            "limit": self.config.limit,
            "train_count": len(train_dataset),
            "validation_count": len(validation_dataset),
            "train_sha256": _sha256_file(self.config.train_path),
            "episodes_sha256": _sha256_file(self.config.episodes_path),
        }
        (run_dir / "config.json").write_text(
            json.dumps(config_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        predictions: list[dict[str, Any]] = []
        logger.info("setup agent %s", agent.name)
        agent.setup(context)
        try:
            for episode in validation_dataset:
                no_gold_episode = episode.without_gold()
                tools.bind_episode(no_gold_episode)
                trace_recorder.start_episode(
                    episode_id=episode.episode_id,
                    agent_name=agent.name,
                    episode_input=no_gold_episode.to_dict(include_gold=False),
                )
                started = time.time()
                prediction_dict: dict[str, Any] | None = None
                error: str | None = None
                try:
                    raw_result = agent.predict(no_gold_episode, context)
                    result = (
                        raw_result
                        if isinstance(raw_result, AgentResult)
                        else AgentResult.from_dict(raw_result)
                    )
                    record = PredictionRecord(
                        episode_id=episode.episode_id,
                        repo=episode.repo,
                        base_commit=episode.base_commit,
                        result=result,
                        metadata={
                            "agent_name": agent.name,
                            "elapsed_ms": (time.time() - started) * 1000,
                            "tool_calls": (
                                len(trace_recorder.current.tool_calls)
                                if trace_recorder.current
                                else 0
                            ),
                        },
                    )
                    prediction_dict = record.to_dict()
                    predictions.append(prediction_dict)
                except Exception as exc:  # noqa: BLE001 - benchmark should record agent errors.
                    error = str(exc)
                    logger.exception("episode failed: %s", episode.episode_id)
                    prediction_dict = PredictionRecord(
                        episode_id=episode.episode_id,
                        repo=episode.repo,
                        base_commit=episode.base_commit,
                        result=None,
                        metadata={"agent_name": agent.name, "error": error},
                    ).to_dict()
                    predictions.append(prediction_dict)
                finally:
                    trace_recorder.finish_episode(prediction=prediction_dict, error=error)
                    tools.clear_episode()
        finally:
            agent.teardown(context)

        predictions_path = run_dir / "predictions.jsonl"
        write_jsonl(predictions_path, predictions)
        summary = {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "train_count": len(train_dataset),
            "validation_count": len(validation_dataset),
            "prediction_count": len(predictions),
            "predictions_sha256": _sha256_file(predictions_path),
        }
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest = {
            "schema_version": 1,
            "kind": "participant_run",
            "config": config_payload,
            "summary": summary,
            "outputs": {
                "predictions": str(predictions_path),
                "traces": str(run_dir / "traces"),
                "logs": str(run_dir / "logs"),
            },
        }
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("finished run %s", run_id)
        return summary
