"""Import recursion: resolving a Python ``import`` to a real source file."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

from codegen import rust_writer
from ir import builder, schema, storage
from plugins import crate_substitution
from preflight import checks


@dataclass
class ResolvedModule:
    dotted_name: str
    file_path: Path
    is_local: bool


@dataclass
class ImportRecursionResult:
    converted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def resolve_import(module_name: str, entry_dir: Path) -> ResolvedModule | None:
    top_level = module_name.split(".")[0]
    if not top_level:
        return None

    local_file = entry_dir / f"{top_level}.py"
    if local_file.is_file():
        return ResolvedModule(dotted_name=top_level, file_path=local_file, is_local=True)

    local_pkg_init = entry_dir / top_level / "__init__.py"
    if local_pkg_init.is_file():
        return ResolvedModule(dotted_name=top_level, file_path=local_pkg_init, is_local=True)

    try:
        spec = importlib.util.find_spec(top_level)
    except (ImportError, ValueError, ModuleNotFoundError, AttributeError):
        spec = None

    if spec is not None and spec.origin and spec.origin not in ("built-in", "frozen"):
        origin = Path(spec.origin)
        if origin.suffix == ".py" and origin.is_file():
            return ResolvedModule(dotted_name=top_level, file_path=origin, is_local=False)

    return None


def recurse_and_convert(
    entry_module: schema.ModuleNode,
    entry_dir: Path,
    output_dir: Path,
    *,
    max_depth: int = 5,
) -> ImportRecursionResult:
    converted: list[str] = []
    skipped: list[str] = []
    visited: set[str] = set()

    queue: list[tuple[str, int]] = [
        (node.module, 1) for node in entry_module.body if isinstance(node, schema.ImportNode)
    ]

    ir_imports_dir = output_dir / "ir" / "_imports"
    rust_imports_dir = output_dir / "_imports"

    while queue:
        module_name, depth = queue.pop(0)
        top_level = module_name.split(".")[0]
        if not top_level or top_level in visited:
            continue
        visited.add(top_level)

        if depth > max_depth:
            skipped.append(f"{module_name} (max import depth {max_depth} exceeded)")
            continue

        resolved = resolve_import(module_name, entry_dir)
        if resolved is None:
            skipped.append(f"{module_name} (not found locally or via site-packages/stdlib)")
            continue

        try:
            sub_source = resolved.file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            skipped.append(f"{module_name} (could not read {resolved.file_path}: {exc})")
            continue

        try:
            sub_report = checks.run_preflight(sub_source)
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{module_name} (preflight crashed on {resolved.file_path}: {exc})")
            continue
        if not sub_report.passed:
            skipped.append(f"{module_name} (syntax error in {resolved.file_path})")
            continue

        try:
            sub_module = builder.build_module_ir(sub_source, resolved.file_path.name)
            builder.apply_collection_ambiguities(sub_module)
            crate_substitution.annotate_crate_suggestions(sub_module)
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{module_name} (IR build failed on {resolved.file_path}: {exc})")
            continue

        ir_path = ir_imports_dir / f"{resolved.dotted_name}.pyrir.json"
        storage.save_module(sub_module, ir_path)

        try:
            sub_rust = rust_writer.render_module(sub_module)
            rust_imports_dir.mkdir(parents=True, exist_ok=True)
            (rust_imports_dir / f"{resolved.dotted_name}.rs").write_text(sub_rust, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

        kind = "local" if resolved.is_local else "third-party"
        converted.append(f"{module_name} -> {resolved.file_path} ({kind}, depth {depth})")

        for node in sub_module.body:
            if isinstance(node, schema.ImportNode):
                queue.append((node.module, depth + 1))

    return ImportRecursionResult(converted=converted, skipped=skipped)
