"""Orchestrates the full pipeline described in ``ARCHITECTURE.md``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ambiguity import resolver as ambiguity  # noqa: F401  (re-exported for callers)
from codegen import rust_writer
from imports import resolver as import_resolver
from ir import builder, storage
from ir.schema import ModuleNode
from plugins import crate_substitution
from preflight import checks
from report import ownership_log as ownership_log_mod
from report import split_check, summary


@dataclass
class ConversionResult:
    preflight: checks.PreflightReport
    module: ModuleNode | None
    rust_source: str | None
    run_summary: summary.RunSummary | None
    split_result: split_check.SplitCheckResult | None
    ownership_log: "ownership_log_mod.OwnershipLog | None" = None
    fatal_warnings: list[str] = field(default_factory=list)


def convert_source(
    source: str,
    source_file: str,
    *,
    split_config: split_check.SplitCheckConfig | None = None,
    warnings_as_fatal: bool = False,
) -> ConversionResult:
    report = checks.run_preflight(source)
    if not report.passed:
        return ConversionResult(report, None, None, None, None)

    module = builder.build_module_ir(source, source_file)
    builder.apply_collection_ambiguities(module)
    crate_substitution.annotate_crate_suggestions(module)

    own_log = ownership_log_mod.build_ownership_log(module)
    own_messages = ownership_log_mod.print_ownership_warnings(own_log)

    fatal_warnings: list[str] = []
    if warnings_as_fatal:
        preflight_warning_msgs = [i.message for i in report.warnings()]
        fatal_warnings = preflight_warning_msgs + own_messages

    if fatal_warnings:
        return ConversionResult(
            report, module, None, None, None, ownership_log=own_log, fatal_warnings=fatal_warnings
        )

    rust_source = rust_writer.render_module(module)
    result_summary = summary.build_summary(module)

    split_result = None
    if split_config is not None:
        split_result = split_check.check_output_length(source, rust_source, split_config)
        rust_source = split_check.prepend_split_notice(rust_source, split_result)

    return ConversionResult(
        report, module, rust_source, result_summary, split_result, ownership_log=own_log
    )


def convert_file(
    input_path: Path,
    output_dir: Path,
    *,
    emit_ir: bool = True,
    split_config: split_check.SplitCheckConfig | None = None,
    warnings_as_fatal: bool = False,
    recurse_imports: bool = True,
    import_depth: int = 5,
) -> ConversionResult:
    source = input_path.read_text(encoding="utf-8")
    result = convert_source(
        source, input_path.name, split_config=split_config, warnings_as_fatal=warnings_as_fatal
    )

    if not result.preflight.passed:
        return result
    if warnings_as_fatal and result.fatal_warnings:
        return result

    output_dir.mkdir(parents=True, exist_ok=True)
    assert result.module is not None and result.rust_source is not None

    if emit_ir:
        ir_path = output_dir / "ir" / f"{input_path.stem}.pyrir.json"
        storage.save_module(result.module, ir_path)

    rust_path = output_dir / f"{input_path.stem}.rs"
    rust_path.write_text(result.rust_source, encoding="utf-8")

    if recurse_imports:
        import_result = import_resolver.recurse_and_convert(
            result.module, input_path.parent, output_dir, max_depth=import_depth
        )
        if result.run_summary is not None:
            result.run_summary.imported_modules_converted = import_result.converted
            result.run_summary.imported_modules_skipped = import_result.skipped

    if result.run_summary is not None:
        summary.write_ambiguities_report(result.run_summary, output_dir / "ambiguities.md")

    if result.ownership_log is not None:
        ownership_log_mod.write_ownership_log(result.ownership_log, output_dir)

    return result
