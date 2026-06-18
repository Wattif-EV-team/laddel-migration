"""Logging configuration for laddel-migration."""

from __future__ import annotations

import logging

_DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(level: int | str = logging.INFO) -> None:
    """Configure the root logger once with a console handler.

    Safe to call multiple times; subsequent calls only adjust the level.
    """
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return

    logging.basicConfig(level=level, format=_DEFAULT_FORMAT)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger for the given name (defaults to the package logger)."""
    return logging.getLogger(name if name else "laddel_migration")
