"""Optional stage-6 feature: suggest splitting a file that grew too much.

This is a judgment about file organization, not about how any given line
of code gets translated -- so unlike conversion-ambiguity logic, its
thresholds are ordinary, editable configuration (see
``PROJECT_OVERVIEW.md`` for the distinction).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SplitCheckConfig:
    """Thresholds for the split-suggestion check. Both are checked;
    whichever triggers first wins."""

    ratio_threshold: float = 1.5
    absolute_line_threshold: int = 500
    enabled: bool = True


@dataclass
class SplitCheckResult:
    triggered: bool
    reason: str = ""
    input_lines: int = 0
    output_lines: int = 0


def check_output_length(
    input_source: str, output_source: str, config: SplitCheckConfig
) -> SplitCheckResult:
    """Compare line counts and decide whether to suggest a split."""

    input_lines = input_source.count("\n") + 1
    output_lines = output_source.count("\n") + 1

    if not config.enabled:
        return SplitCheckResult(False, input_lines=input_lines, output_lines=output_lines)

    ratio = output_lines / max(input_lines, 1)
    if ratio > config.ratio_threshold:
        return SplitCheckResult(
            True,
            reason=(
                f"output is {ratio:.1f}x the input's line count "
                f"(threshold {config.ratio_threshold}x)"
            ),
            input_lines=input_lines,
            output_lines=output_lines,
        )
    if output_lines > config.absolute_line_threshold:
        return SplitCheckResult(
            True,
            reason=(
                f"output has {output_lines} lines "
                f"(threshold {config.absolute_line_threshold})"
            ),
            input_lines=input_lines,
            output_lines=output_lines,
        )
    return SplitCheckResult(False, input_lines=input_lines, output_lines=output_lines)


def prepend_split_notice(output_source: str, result: SplitCheckResult) -> str:
    """Add a leading comment suggesting a split, if the check triggered."""

    if not result.triggered:
        return output_source
    notice = (
        "// SPLIT SUGGESTION: this file grew significantly during conversion "
        f"({result.reason}). Consider splitting it into smaller modules.\n"
    )
    return notice + output_source
