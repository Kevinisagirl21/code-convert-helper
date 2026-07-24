from pathlib import Path

from src import pipeline
from report.split_check import SplitCheckConfig, check_output_length


def test_pipeline_end_to_end_writes_all_outputs(tmp_path: Path):
    src_file = tmp_path / "sample.py"
    src_file.write_text("def add(a: int, b: int) -> int:\n    return a + b\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir, recurse_imports=False)

    assert result.preflight.passed
    assert (out_dir / "sample.rs").exists()
    assert (out_dir / "ir" / "sample.pyrir.json").exists()
    assert (out_dir / "ambiguities.md").exists()
    assert "fn add" in (out_dir / "sample.rs").read_text()


def test_pipeline_stops_on_syntax_error(tmp_path: Path):
    src_file = tmp_path / "broken.py"
    src_file.write_text("def f(:\n    pass\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir, recurse_imports=False)

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
    output_src = "\n".join(["b"] * 401)
    result = check_output_length(input_src, output_src, SplitCheckConfig(absolute_line_threshold=400))
    assert result.triggered


def test_ownership_log_written_and_populated(tmp_path: Path):
    src_file = tmp_path / "sample.py"
    src_file.write_text("def f(name: str):\n    print(name)\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir, recurse_imports=False)

    assert result.preflight.passed
    assert result.ownership_log is not None
    assert len(result.ownership_log.entries) >= 1
    assert (out_dir / "ownership_log.json").exists()
    assert (out_dir / "ownership_log.md").exists()
    assert "refer" in (out_dir / "ownership_log.md").read_text()


def test_warnings_as_fatal_off_by_default_allows_inferred_ownership(tmp_path: Path):
    src_file = tmp_path / "sample.py"
    src_file.write_text("def f(name: str):\n    print(name)\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir, recurse_imports=False)

    assert result.rust_source is not None
    assert result.fatal_warnings == []


def test_warnings_as_fatal_on_stops_conversion_for_inferred_ownership(tmp_path: Path):
    src_file = tmp_path / "sample.py"
    src_file.write_text("def f(name: str):\n    print(name)\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(
        src_file, out_dir, warnings_as_fatal=True, recurse_imports=False
    )

    assert result.rust_source is None
    assert result.fatal_warnings
    assert not (out_dir / "sample.rs").exists()


def test_warnings_as_fatal_on_with_all_directives_present_still_converts(tmp_path: Path):
    src_file = tmp_path / "sample.py"
    src_file.write_text(
        "def f(\n"
        "    name: str,  #! move\n"
        ") -> str:  #! owner\n"
        "    return name\n"
    )

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(
        src_file, out_dir, warnings_as_fatal=True, recurse_imports=False
    )

    assert result.rust_source is not None
    assert result.fatal_warnings == []


def test_recurse_imports_converts_local_module(tmp_path: Path):
    (tmp_path / "helper.py").write_text("def double(n: int) -> int:\n    return n * 2\n")
    src_file = tmp_path / "main.py"
    src_file.write_text("import helper\n\ndef f(n: int) -> int:\n    return helper.double(n)\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir, recurse_imports=True, import_depth=5)

    assert result.preflight.passed
    assert (out_dir / "ir" / "_imports" / "helper.pyrir.json").exists()
    assert (out_dir / "_imports" / "helper.rs").exists()
    assert any("helper" in m for m in result.run_summary.imported_modules_converted)


def test_recurse_imports_off_does_not_touch_imports_dir(tmp_path: Path):
    (tmp_path / "helper.py").write_text("def double(n: int) -> int:\n    return n * 2\n")
    src_file = tmp_path / "main.py"
    src_file.write_text("import helper\n\ndef f(n: int) -> int:\n    return helper.double(n)\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir, recurse_imports=False)

    assert result.preflight.passed
    assert not (out_dir / "ir" / "_imports").exists()


def test_import_depth_limit_skips_beyond_max_depth(tmp_path: Path):
    (tmp_path / "a.py").write_text("import b\n\ndef fa():\n    pass\n")
    (tmp_path / "b.py").write_text("import c\n\ndef fb():\n    pass\n")
    (tmp_path / "c.py").write_text("def fc():\n    pass\n")
    src_file = tmp_path / "main.py"
    src_file.write_text("import a\n\ndef f():\n    pass\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir, recurse_imports=True, import_depth=1)

    assert (out_dir / "ir" / "_imports" / "a.pyrir.json").exists()
    assert not (out_dir / "ir" / "_imports" / "b.pyrir.json").exists()
    assert any("depth" in m for m in result.run_summary.imported_modules_skipped)


def test_unresolvable_import_is_skipped_not_fatal(tmp_path: Path):
    src_file = tmp_path / "main.py"
    src_file.write_text("import totally_not_a_real_module_xyz\n\ndef f():\n    pass\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir, recurse_imports=True)

    assert result.preflight.passed
    assert result.rust_source is not None
    assert any("totally_not_a_real_module_xyz" in m for m in result.run_summary.imported_modules_skipped)
