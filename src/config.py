"""Milestone 1: the warnings-as-fatal global toggle.

Modeled as an explicit :class:`PipelineConfig` object created once (in
:mod:`cli`, or directly by a test/caller) and threaded down through the
pipeline call chain, rather than a bare module-level global -- this
codebase already threads a few things through function calls (e.g. the
split-check config in :mod:`report.split_check`), and an explicit object
is easier to test in isolation (no global state to reset between tests).

The toggle itself is a single ``bool``: when ``True`` ("strict mode"),
anything that would otherwise be a non-fatal warning is escalated to a
hard failure via :func:`report_warning`. Milestone 1 only wires this
mechanism -- the only real caller so far is a placeholder demonstrating
the pattern; Milestone 2's missing-ownership-directive fallback is the
first real "would be a warning, unless strict mode" case.
"""

from __future__ import annotations

from dataclasses import dataclass

from logging_setup import get_logger


class FatalWarningError(RuntimeError):
    """Raised by :func:`report_warning` when warnings-as-fatal is on."""


@dataclass
class PipelineConfig:
    """Process-wide-in-effect, but explicitly-passed, run configuration.

    Attributes
    ----------
    warnings_as_fatal:
        The Milestone 1 global toggle (default ``False``, matching the
        ROADMAP's confirmed assumptions). Set from the CLI's
        ``--strict`` flag.
    """

    warnings_as_fatal: bool = False


def report_warning(config: PipelineConfig, message: str, *, code: str = "") -> None:
    """Report a warning, honoring the warnings-as-fatal toggle.

    If ``config.warnings_as_fatal`` is ``False`` (the default), this logs
    the warning (and it's the caller's job to also surface it to the user
    however that stage normally reports issues, e.g. as a
    ``PreflightIssue``). If ``True``, this raises :class:`FatalWarningError`
    instead, turning the warning into a hard stop.
    """

    logger = get_logger()
    tag = f"[{code}] " if code else ""
    if config.warnings_as_fatal:
        logger.error("%s%s (warnings-as-fatal is on; treating as fatal)", tag, message)
        raise FatalWarningError(f"{tag}{message}")
    logger.warning("%s%s", tag, message)
