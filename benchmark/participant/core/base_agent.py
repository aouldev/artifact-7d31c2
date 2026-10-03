"""Base class for benchmark agents."""

from __future__ import annotations

from typing import Any

from benchmark.participant.core.context import AgentContext
from benchmark.participant.core.models import AgentResult, Episode


class BaseAgent:
    """Minimal interface external agents should implement."""

    name = "base_agent"

    def setup(self, context: AgentContext) -> None:
        """Optional setup. Agents may read train gold through context.train_reader."""

    def predict(self, episode: Episode, context: AgentContext) -> AgentResult | dict[str, Any]:
        """Return a negative prediction or a positive prediction with a test patch."""
        raise NotImplementedError

    def teardown(self, context: AgentContext) -> None:
        """Optional cleanup."""
