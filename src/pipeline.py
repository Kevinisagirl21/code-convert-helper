"""Orchestrates the full pipeline described in ``ARCHITECTURE.md``.

::

    ingest & preflight -> parse & build IR -> mark ambiguities
        -> crate-substitution annotation -> generate Rust
        -> optional split check -> write output + reports

Each stage is independently testable in its own module; this module's
only job is calling them in the right order and packaging up the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ambiguity import resolver as ambiguity  # noqa: F401  (re-exported for callers)
from codegen import rust_writer
from ir import builder, storage
from ir.schema import ModuleNode
from plugins import crate_substitution
from preflight import checks
from report import split_check, summary


@dataclass
class ConversionResult:
    preflight: checks.PreflightReport
    module: ModuleNode | None
    rust_source: str | None
    run_summary: summary.RunSummary | None
    split_result: split_check.SplitCheckResult | None


def convert_source(
    source: str,
    source_file: str,
    *,
    split_config: split_check.SplitCheckConfig | None = None,
) -> ConversionResult:
    """Run the full pipeline over Python ``source`` already in memory.

    Does not touch the filesystem -- see :func:`convert_file` for the
    CLI-facing version that reads/writes real files. Kept separate so the
    pipeline itself stays trivially unit-testable with plain strings.
    """

    report = checks.run_preflight(source)
    if not report.passed:
        return ConversionResult(report, None, None, None, None)

    module = builder.build_module_ir(source, source_file)
    builder.apply_collection_ambiguities(module)
    crate_substitution.annotate_crate_suggestions(module)

    rust_source = rust_writer.render_module(module)

    result_summary = summary.build_summary(module)

    split_result = None
    if split_config is not None:
        split_result = split_check.check_output_length(source, rust_source, split_config)
        rust_source = split_check.prepend_split_notice(rust_source, split_result)

    return ConversionResult(report, module, rust_source, result_summary, split_result)


def convert_file(
    input_path: Path,
    output_dir: Path,
    *,
    emit_ir: bool = True,
    split_config: split_check.SplitCheckConfig | None = None,
) -> ConversionResult:
    """Run the full pipeline against a file on disk and write all outputs.

    Writes, under ``output_dir``:

    * ``<stem>.rs`` -- the generated Rust.
    * ``ir/<stem>.pyrir.json`` -- the locked IR artifact (if ``emit_ir``).
    * ``ambiguities.md`` -- the run's flagged-item report.
    """

    source = input_path.read_text(encoding="utf-8")
    result = convert_source(source, input_path.name, split_config=split_config)

    if not result.preflight.passed:
        return result

    output_dir.mkdir(parents=True, exist_ok=True)
    assert result.module is not None and result.rust_source is not None

    if emit_ir:
        ir_path = output_dir / "ir" / f"{input_path.stem}.pyrir.json"
        storage.save_module(result.module, ir_path)

    rust_path = output_dir / f"{input_path.stem}.rs"
    rust_path.write_text(result.rust_source, encoding="utf-8")

    if result.run_summary is not None:
        summary.write_ambiguities_report(result.run_summary, output_dir / "ambiguities.md")

    return result
