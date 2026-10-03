"""Runtime context exposed to agents."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from benchmark.participant.loaders.train_reader import TrainReader


@dataclass(frozen=True)
class AgentContext:
    run_dir: Path
    train_reader: TrainReader
    tools: object
    logger: logging.Logger
