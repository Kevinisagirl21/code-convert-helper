"""Milestone 1: logging infrastructure.

Stdlib ``logging``, writing a per-run log file alongside the pipeline's
other output artifacts (IR, generated Rust, ambiguities.md), with
warnings and errors also mirrored to stdout so the CLI still feels
responsive without having to tail a file.

This module only wires the plumbing -- a single ``get_logger()`` that
other stages call into. It intentionally does not try to achieve
"comprehensive" logging by itself; Milestone 2's ownership-directive
resolver is what actually has interesting decisions to log. Milestone 1
just makes sure a logger exists, is configured consistently, and is
already being used by preflight (see :mod:`preflight.checks`) so the
pattern is proven before more stages lean on it.
"""

from __future__ import annotations

import logging
from pathlib import Path

_LOGGER_NAME = "py2rust"
_configured = False


def get_logger() -> logging.Logger:
    """Return the shared ``py2rust`` logger.

    Safe to call before :func:`configure_logging` -- in that case it
    just returns a logger with no handlers attached yet (Python's
    logging module silently no-ops in that case), which is fine for
    contexts like unit tests that don't care about log output.
    """

    return logging.getLogger(_LOGGER_NAME)


def configure_logging(output_dir: Path | None = None, *, verbose_stdout: bool = False) -> logging.Logger:
    """Configure the shared logger for one pipeline run.

    Parameters
    ----------
    output_dir:
        If given, a ``py2rust.log`` file is written under this directory
        alongside the run's other artifacts (IR, generated Rust,
        ambiguities.md). If ``None``, only the stdout handler is set up
        (useful for ``preflight``-only runs that don't have an output
        directory at all).
    verbose_stdout:
        If ``True``, INFO-and-above also goes to stdout. Otherwise only
        WARNING-and-above is mirrored to stdout -- the full detail still
        goes to the log file, but everyday runs aren't spammed.
    """

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
