"""Trajectory recording utilities."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolCallRecord:
    tool: str
    args: dict[str, Any]
    output: Any
    elapsed_ms: float
    ok: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "output": self.output,
            "elapsed_ms": self.elapsed_ms,
            "ok": self.ok,
            "error": self.error,
        }


@dataclass
class EpisodeTrace:
    episode_id: str
    agent_name: str
    episode_input: dict[str, Any]
    started_at: float = field(default_factory=time.time)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    prediction: dict[str, Any] | None = None
    error: str | None = None

    def add_tool_call(self, record: ToolCallRecord) -> None:
        self.tool_calls.append(record)

    def to_dict(self) -> dict[str, Any]:
        finished_at = time.time()
        return {
            "episode_id": self.episode_id,
            "agent_name": self.agent_name,
            "episode_input": self.episode_input,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "elapsed_ms": (finished_at - self.started_at) * 1000,
            "tool_calls": [record.to_dict() for record in self.tool_calls],
            "prediction": self.prediction,
            "error": self.error,
        }


class TraceRecorder:
    def __init__(self, traces_dir: str | Path) -> None:
        self.traces_dir = Path(traces_dir)
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.current: EpisodeTrace | None = None

    def start_episode(
        self, episode_id: str, agent_name: str, episode_input: dict[str, Any]
    ) -> None:
        self.current = EpisodeTrace(
            episode_id=episode_id,
            agent_name=agent_name,
            episode_input=episode_input,
        )

    def record_tool_call(self, record: ToolCallRecord) -> None:
        if self.current is not None:
            self.current.add_tool_call(record)

    def finish_episode(
        self, prediction: dict[str, Any] | None = None, error: str | None = None
    ) -> None:
        if self.current is None:
            return
        self.current.prediction = prediction
        self.current.error = error
        output_path = self.traces_dir / f"{self.current.episode_id}.json"
        output_path.write_text(
            json.dumps(self.current.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.current = None
