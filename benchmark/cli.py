"""Compatibility entrypoint for the public participant CLI."""

from benchmark.participant.cli import build_parser, main, run_command

__all__ = ["build_parser", "main", "run_command"]
