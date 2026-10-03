"""Dataset loading utilities."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from benchmark.participant.core.models import Episode


def read_jsonl(path: str | Path) -> list[dict]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    rows: list[dict] = []
    with file_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {file_path}:{line_number}: {exc}") from exc
    return rows


def write_jsonl(path: str | Path, rows: Iterable[dict]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class EpisodeDataset:
    def __init__(self, episodes: list[Episode]) -> None:
        self.episodes = episodes

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "EpisodeDataset":
        return cls([Episode.from_dict(row) for row in read_jsonl(path)])

    def by_repo(self) -> dict[str, list[Episode]]:
        grouped: dict[str, list[Episode]] = {}
        for episode in self.episodes:
            grouped.setdefault(episode.repo, []).append(episode)
        return grouped

    def __iter__(self):
        return iter(self.episodes)

    def __len__(self) -> int:
        return len(self.episodes)
