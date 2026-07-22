"""Import recursion: resolving a Python ``import`` to a real source file.

This is the resolution half only -- finding the file on disk for a given
:class:`~ir.schema.ImportNode`. The recursive walk itself (depth
tracking, the visited set, writing converted modules under
``ir/_imports/``) lives in :mod:`pipeline`, which is the natural owner of
"drive stages 0-4 repeatedly" -- this module stays a small, independently
testable lookup step, in the same spirit as ``ARCHITECTURE.md``'s
one-responsibility-per-stage design.

Resolution order, per the two kinds of imports Milestone 2 is scoped to
support:

1. **Local, project-relative modules** -- a file or package sitting next
   to the entry file (``<entry_dir>/<name>.py`` or
   ``<entry_dir>/<name>/__init__.py``).
2. **Installed third-party packages** -- anything ``importlib`` can find
   on ``sys.path`` (this also covers the stdlib, which is why callers
   still need to expect a lot of ``UnsupportedStmt`` output from stdlib
   modules -- they're real, often large, C-accelerated or highly dynamic
   codebases, not v1-core-subset Python).

Nothing here ever raises on a module it can't find or can't read --
returning ``None`` and letting the caller record it as skipped is the
correct outcome, matching the "never fail the whole run over one
external thing" principle already used for plugins and unsupported
syntax.
"""

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
    """A located Python source file backing an ``import`` statement."""

    dotted_name: str
    file_path: Path
    is_local: bool


@dataclass
class ImportRecursionResult:
    """Which imported modules got converted vs. skipped, for the run summary."""

    converted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def resolve_import(module_name: str, entry_dir: Path) -> ResolvedModule | None:
    """Find a real ``.py`` source file for ``module_name``.

    Only the top-level component of a dotted import (``a.b.c`` -> ``a``)
    is resolved -- following a full submodule path is future work; v1
    treats the top-level package/module as the recursion unit, which
    matches how ``ARCHITECTURE.md``'s ``_imports/`` layout is keyed.
    """

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
    """Follow every import reachable from ``entry_module``, breadth-first.

    Each resolved module is run through the same preflight -> IR-build ->
    ambiguity-marking pipeline as the entry file (stages 0-4 of
    ``ARCHITECTURE.md``) and written under ``output_dir/ir/_imports/`` as
    its own locked IR file, plus a best-effort ``.rs`` rendering under
    ``output_dir/_imports/`` -- matching the ``_imports/`` layout
    ``ARCHITECTURE.md`` describes for converted dependencies.

    ``max_depth`` counts import hops from the entry file (the entry
    file's own direct imports are depth 1); a depth-exceeded or
    unresolvable import is recorded as skipped, never a hard failure --
    recursing into real third-party code will commonly produce IR that's
    mostly ``UnsupportedStmt`` nodes (most real-world libraries use far
    more than the v1 core subset), and that's expected, not an error.

    A flat ``visited`` set (keyed by each import's top-level component)
    guards against reconverting the same module twice, including via a
    circular import.
    """

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
            # Real-world third-party code can hit corners this prototype's
            # builder doesn't handle yet -- one such module must never take
            # down the whole conversion run (same principle as a failing
            # plugin in plugins/protocol.py). Record it as skipped instead.
            skipped.append(f"{module_name} (IR build failed on {resolved.file_path}: {exc})")
            continue

        ir_path = ir_imports_dir / f"{resolved.dotted_name}.pyrir.json"
        storage.save_module(sub_module, ir_path)

        try:
            sub_rust = rust_writer.render_module(sub_module)
            rust_imports_dir.mkdir(parents=True, exist_ok=True)
            (rust_imports_dir / f"{resolved.dotted_name}.rs").write_text(sub_rust, encoding="utf-8")
        except Exception:  # noqa: BLE001 -- best-effort; the locked IR is the artifact that matters
            pass

        kind = "local" if resolved.is_local else "third-party"
        converted.append(f"{module_name} -> {resolved.file_path} ({kind}, depth {depth})")

        for node in sub_module.body:
            if isinstance(node, schema.ImportNode):
                queue.append((node.module, depth + 1))

    return ImportRecursionResult(converted=converted, skipped=skipped)
