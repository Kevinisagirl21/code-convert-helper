"""The ``py2rust`` command-line interface.

Built with `typer <https://typer.tiangolo.com/>`_ -- arguments and options
are driven by type hints, which fits a project whose whole subject is
type inference. Run ``py2rust --help`` for the full command list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

import pipeline
from ir import storage
from preflight import checks
from report.split_check import SplitCheckConfig

app = typer.Typer(
    name="py2rust",
    help="Convert a Python file's core-subset code to Rust, preserving comments.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def preflight(file: Path = typer.Argument(..., exists=True, help="Python file to check.")) -> None:
    """Run stage-0 preflight checks only, and print the report."""

    source = file.read_text(encoding="utf-8")
    report = checks.run_preflight(source)

    if not report.passed:
        console.print(f"[bold red]FAILED[/bold red] -- {file} has a syntax error:")
        for issue in report.errors():
            console.print(f"  [red]error[/red] {issue.message}")
        raise typer.Exit(code=1)

    console.print(f"[bold green]PASSED[/bold green] -- {file} parses cleanly.")
    if report.issues:
        table = Table(title="Preflight findings")
        table.add_column("Severity")
        table.add_column("Message")
        for issue in report.issues:
            style = {"warning": "yellow", "info": "cyan"}.get(issue.severity, "white")
            table.add_row(f"[{style}]{issue.severity}[/{style}]", issue.message)
        console.print(table)
    else:
        console.print("No warnings or notable findings.")


@app.command()
def convert(
    file: Path = typer.Argument(..., exists=True, help="Python file to convert."),
    out: Path = typer.Option(Path("output"), "--out", "-o", help="Output directory."),
    emit_ir: bool = typer.Option(True, help="Write the locked IR artifact alongside the output."),
    split_check: bool = typer.Option(
        False, "--split-check/--no-split-check", help="Enable the output-length split suggestion."
    ),
    split_ratio: float = typer.Option(1.5, help="Split-check ratio threshold (output/input lines)."),
    split_lines: int = typer.Option(500, help="Split-check absolute line-count threshold."),
) -> None:
    """Convert FILE to Rust, writing output, IR, and an ambiguity report."""

    split_config = SplitCheckConfig(
        ratio_threshold=split_ratio, absolute_line_threshold=split_lines, enabled=split_check
    )
    result = pipeline.convert_file(file, out, emit_ir=emit_ir, split_config=split_config)

    if not result.preflight.passed:
        console.print(f"[bold red]Preflight failed for {file}:[/bold red]")
        for issue in result.preflight.errors():
            console.print(f"  [red]error[/red] {issue.message}")
        raise typer.Exit(code=1)

    rust_path = out / f"{file.stem}.rs"
    console.print(f"[bold green]Converted[/bold green] {file} -> {rust_path}")

    if result.run_summary is not None:
        s = result.run_summary
        console.print(
            f"  functions: {s.functions_converted}  classes: {s.classes_converted}  "
            f"type holes: {len(s.type_holes)}  ambiguities: {len(s.ambiguities)}  "
            f"unsupported: {len(s.unsupported)}"
        )
        console.print(f"  full report: {out / 'ambiguities.md'}")

    if result.split_result is not None and result.split_result.triggered:
        console.print(f"  [yellow]split suggestion:[/yellow] {result.split_result.reason}")

    for issue in result.preflight.warnings():
        console.print(f"  [yellow]warning[/yellow] {issue.message}")


@app.command("inspect-ir")
def inspect_ir(ir_file: Path = typer.Argument(..., exists=True, help="A .pyrir.json IR file.")) -> None:
    """Pretty-print a locked IR file for inspection (read-only, non-destructive)."""

    module = storage.load_module(ir_file)
    console.print(f"[bold]{module.source_file}[/bold]  (schema {module.schema_version})")
    for top in module.body:
        console.print(f"  {top.kind}: {getattr(top, 'name', getattr(top, 'module', ''))}")


@app.command()
def version() -> None:
    """Print the py2rust version."""

    from src import __version__

    console.print(f"py2rust {__version__}")


if __name__ == "__main__":
    app()
