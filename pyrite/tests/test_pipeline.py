from pathlib import Path

from pyrite import pipeline
from pyrite.report.split_check import SplitCheckConfig, check_output_length


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


def test_pipeline_stops_on_syntax_error(tmp_path: Path):
    src_file = tmp_path / "broken.py"
    src_file.write_text("def f(:\n    pass\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir)

    assert not result.preflight.passed
    assert result.rust_source is None
    assert not (out_dir / "broken.rs").exists()


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
