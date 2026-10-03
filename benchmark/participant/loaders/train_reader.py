"""Train gold access interface for agents."""

from __future__ import annotations

from collections.abc import Iterator

from benchmark.participant.core.models import Episode


class TrainReader:
    """Read-only interface for train episodes with gold labels and patches."""

    def __init__(self, episodes: list[Episode]) -> None:
        self._episodes = list(episodes)

    def iter(self) -> Iterator[Episode]:
        yield from self._episodes

    def by_repo(self, repo: str) -> list[Episode]:
        return [episode for episode in self._episodes if episode.repo == repo]

    def __len__(self) -> int:
        return len(self._episodes)
