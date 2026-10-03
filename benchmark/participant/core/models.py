"""Public data models shared by participant agents and the runner.

These models intentionally describe only the agent-visible protocol. Private
validation ground truth and official scoring live outside the participant image.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MaintenanceLabel = Literal["positive", "negative"]
VALID_MAINTENANCE_LABELS = {"positive", "negative"}


@dataclass(frozen=True)
class Gold:
    """Training gold exposed through the public train reader only."""

    maintenance_label: MaintenanceLabel
    test_patch: str = ""
    test_files: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Gold":
        label = data.get("maintenance_label")
        if label not in VALID_MAINTENANCE_LABELS:
            raise ValueError(f"invalid gold maintenance_label: {label!r}")
        return cls(
            maintenance_label=label,
            test_patch=str(data.get("test_patch") or ""),
            test_files=[str(path) for path in data.get("test_files") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "maintenance_label": self.maintenance_label,
            "test_patch": self.test_patch,
            "test_files": self.test_files,
        }


@dataclass(frozen=True)
class Episode:
    """One production-change episode shown to an agent."""

    episode_id: str
    repo: str
    base_commit: str
    prod_files: list[str]
    prod_diff: str
    commit_message: str = ""
    timestamp: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    gold: Gold | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Episode":
        gold_data = data.get("gold")
        return cls(
            episode_id=str(data["episode_id"]),
            repo=str(data["repo"]),
            base_commit=str(data["base_commit"]),
            prod_files=[str(path) for path in data.get("prod_files") or []],
            prod_diff=str(data.get("prod_diff") or ""),
            commit_message=str(data.get("commit_message") or ""),
            timestamp=str(data["timestamp"]) if data.get("timestamp") is not None else None,
            metadata=dict(data.get("metadata") or {}),
            gold=Gold.from_dict(gold_data) if isinstance(gold_data, dict) else None,
        )

    def to_dict(self, include_gold: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "episode_id": self.episode_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "timestamp": self.timestamp,
            "prod_files": self.prod_files,
            "prod_diff": self.prod_diff,
            "commit_message": self.commit_message,
            "metadata": self.metadata,
        }
        if include_gold and self.gold is not None:
            payload["gold"] = self.gold.to_dict()
        return payload

    def without_gold(self) -> "Episode":
        return Episode(
            episode_id=self.episode_id,
            repo=self.repo,
            base_commit=self.base_commit,
            prod_files=list(self.prod_files),
            prod_diff=self.prod_diff,
            commit_message=self.commit_message,
            timestamp=self.timestamp,
            metadata=dict(self.metadata),
            gold=None,
        )


@dataclass(frozen=True)
class AgentResult:
    """Prediction returned by a participant agent for one episode."""

    maintenance_label: MaintenanceLabel
    test_patch: str = ""
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentResult":
        label = data.get("maintenance_label")
        if label not in VALID_MAINTENANCE_LABELS:
            raise ValueError(f"invalid prediction maintenance_label: {label!r}")
        test_patch = str(data.get("test_patch") or "")
        if label == "negative" and test_patch:
            raise ValueError("negative prediction must have an empty test_patch")
        return cls(
            maintenance_label=label,
            test_patch=test_patch,
            rationale=str(data.get("rationale") or ""),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "maintenance_label": self.maintenance_label,
            "test_patch": self.test_patch,
            "rationale": self.rationale,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class PredictionRecord:
    """Submission row emitted by the public runner."""

    episode_id: str
    repo: str
    base_commit: str
    result: AgentResult | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "result": self.result.to_dict() if self.result is not None else None,
            "metadata": self.metadata,
        }
