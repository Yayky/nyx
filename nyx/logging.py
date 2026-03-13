"""Logging configuration helpers for Nyx.

Phase 1 uses standard library logging only. This module centralizes startup
logging so CLI, daemon, and future modules share the same formatting and level
behavior.
"""

from __future__ import annotations

import logging


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure root logging once and return the Nyx application logger.

    Args:
        level: Root logging level for the process.

    Returns:
        The ``nyx`` logger instance after global logging setup has been applied.
    """

    root_logger = logging.getLogger()
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if root_logger.handlers:
        root_logger.setLevel(level)
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)
        return logging.getLogger("nyx")

    logging.basicConfig(level=level, format=formatter._fmt, datefmt=formatter.datefmt)
    return logging.getLogger("nyx")
