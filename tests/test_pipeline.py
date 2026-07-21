from pathlib import Path

from src import pipeline
from config import FatalWarningError, PipelineConfig, report_warning
from report.split_check import SplitCheckConfig, check_output_length


def test_pipeline_end_to_end_writes_all_outputs(tmp_path: Path):
    src_file = tmp_path / "sample.py"
    src_file.write_text("def add(a: int, b: int) -> int:\n    return a + b\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir)

    assert result.preflight.passed
    assert (out_dir / "sample.rs").exists()
    assert (out_dir / "ir" / "sample.pyrir.json").exists()
    assert (out_dir / "ambiguities.md").exists()
    assert "fn add" in (out_dir / "sample.rs").read_text()


def test_pipeline_writes_a_per_run_log_file(tmp_path: Path):
    """Milestone 1: logging infra -- a py2rust.log should land alongside
    the other output artifacts for every run."""

    src_file = tmp_path / "sample.py"
    src_file.write_text("def add(a: int, b: int) -> int:\n    return a + b\n")

    out_dir = tmp_path / "out"
    pipeline.convert_file(src_file, out_dir)

    assert (out_dir / "py2rust.log").exists()


def test_pipeline_stops_on_syntax_error(tmp_path: Path):
    src_file = tmp_path / "broken.py"
    src_file.write_text("def f(:\n    pass\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir)

    assert not result.preflight.passed
    assert result.rust_source is None
    assert not (out_dir / "broken.rs").exists()


def test_pipeline_stops_on_missing_mandatory_hint(tmp_path: Path):
    """v2's central Milestone 1 behavior: a missing hint is a hard
    preflight rejection, just like a syntax error -- no Rust is written."""

    src_file = tmp_path / "unhinted.py"
    src_file.write_text("def f(x):\n    return x\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir)

    assert not result.preflight.passed
    assert result.rust_source is None
    assert not (out_dir / "unhinted.rs").exists()
    messages = [e.message for e in result.preflight.errors()]
    assert any("type hint" in m for m in messages)


def test_split_check_ratio_trigger():
    result = check_output_length("a\nb\n", "\n".join(["x"] * 10), SplitCheckConfig())
    assert result.triggered
    assert "1.5x" in result.reason or "x" in result.reason


def test_split_check_disabled_never_triggers():
    result = check_output_length("a\n", "\n".join(["x"] * 1000), SplitCheckConfig(enabled=False))
    assert not result.triggered


def test_split_check_absolute_threshold():
    input_src = "\n".join(["a"] * 400)
    output_src = "\n".join(["b"] * 401)  # ratio ~1.0, under absolute default of 500 -> shouldn't trigger yet
    result = check_output_length(input_src, output_src, SplitCheckConfig(absolute_line_threshold=400))
    assert result.triggered


# -- warnings-as-fatal toggle (Milestone 1) ----------------------------------


def test_warnings_as_fatal_off_by_default_does_not_raise():
    config = PipelineConfig()
    assert config.warnings_as_fatal is False
    report_warning(config, "a non-fatal warning")  # must not raise


def test_warnings_as_fatal_on_raises():
    config = PipelineConfig(warnings_as_fatal=True)
    try:
        report_warning(config, "this should become fatal")
        assert False, "expected FatalWarningError"
    except FatalWarningError as exc:
        assert "this should become fatal" in str(exc)
