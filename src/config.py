"""Milestone 1: the warnings-as-fatal global toggle."""

from __future__ import annotations

from dataclasses import dataclass

from logging_setup import get_logger


class FatalWarningError(RuntimeError):
    """Raised by :func:`report_warning` when warnings-as-fatal is on."""


@dataclass
class PipelineConfig:
    warnings_as_fatal: bool = False


def report_warning(config: PipelineConfig, message: str, *, code: str = "") -> None:
    logger = get_logger()
    tag = f"[{code}] " if code else ""
    if config.warnings_as_fatal:
        logger.error("%s%s (warnings-as-fatal is on; treating as fatal)", tag, message)
        raise FatalWarningError(f"{tag}{message}")
    logger.warning("%s%s", tag, message)
