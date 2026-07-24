"""Milestone 1: logging infrastructure."""

from __future__ import annotations

import logging
from pathlib import Path

_LOGGER_NAME = "py2rust"
_configured = False


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def configure_logging(output_dir: Path | None = None, *, verbose_stdout: bool = False) -> logging.Logger:
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(output_dir / "py2rust.log", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    stdout_handler = logging.StreamHandler()
    stdout_handler.setLevel(logging.INFO if verbose_stdout else logging.WARNING)
    stdout_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(stdout_handler)

    _configured = True
    return logger
