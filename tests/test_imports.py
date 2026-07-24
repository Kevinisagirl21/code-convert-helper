from pathlib import Path

from imports import resolver as import_resolver


def test_resolve_local_module(tmp_path: Path):
    (tmp_path / "helper.py").write_text("def f():\n    pass\n")
    resolved = import_resolver.resolve_import("helper", tmp_path)
    assert resolved is not None
    assert resolved.is_local
    assert resolved.file_path == tmp_path / "helper.py"


def test_resolve_local_package(tmp_path: Path):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("x = 1\n")
    resolved = import_resolver.resolve_import("mypkg", tmp_path)
    assert resolved is not None
    assert resolved.is_local
    assert resolved.file_path == pkg / "__init__.py"


def test_resolve_third_party_installed_package(tmp_path: Path):
    resolved = import_resolver.resolve_import("libcst", tmp_path)
    assert resolved is not None
    assert not resolved.is_local


def test_unresolvable_module_returns_none(tmp_path: Path):
    resolved = import_resolver.resolve_import("totally_not_a_real_module_xyz", tmp_path)
    assert resolved is None


def test_dotted_import_uses_top_level_component(tmp_path: Path):
    (tmp_path / "helper.py").write_text("def f():\n    pass\n")
    resolved = import_resolver.resolve_import("helper.submodule", tmp_path)
    assert resolved is not None
    assert resolved.dotted_name == "helper"


def test_recurse_and_convert_writes_ir_and_rust(tmp_path: Path):
    from ir import builder

    (tmp_path / "helper.py").write_text("def double(n: int) -> int:\n    return n * 2\n")
    entry_module = builder.build_module_ir("import helper\n", "main.py")

    out_dir = tmp_path / "out"
    result = import_resolver.recurse_and_convert(entry_module, tmp_path, out_dir, max_depth=5)

    assert any("helper" in c for c in result.converted)
    assert (out_dir / "ir" / "_imports" / "helper.pyrir.json").exists()
    assert (out_dir / "_imports" / "helper.rs").exists()


def test_recurse_and_convert_skips_unresolvable(tmp_path: Path):
    from ir import builder

    entry_module = builder.build_module_ir("import not_a_real_module_at_all\n", "main.py")
    out_dir = tmp_path / "out"
    result = import_resolver.recurse_and_convert(entry_module, tmp_path, out_dir, max_depth=5)

    assert result.converted == []
    assert any("not_a_real_module_at_all" in s for s in result.skipped)
