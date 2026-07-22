# Changelog

## Milestone 2 -- `#!` ownership directives, ownership model, import recursion

### Added

- **`#!` directive grammar** (`directives/parser.py`): same-line trailing
  comments of the form `#! <keyword>` (shorthand for the `ownership` key)
  or the explicit `#! ownership: <keyword>` form. Recognized on a
  parameter's trailing comma, a function's `-> ReturnType:` line, and an
  assignment statement.
- **Ownership vocabulary**: `owner`, `refer`, `refer_mut`, `move`
  (`ir/schema.OWNERSHIP_VALUES`).
- **Ownership resolution** (`ownership/resolver.py`): usage-based
  inference when no directive is present (Copy primitives always
  `owner`; a value returned directly or stored into `self.attr` unchanged
  infers `move`; otherwise `refer`) combined with an optional directive,
  which always wins. Disagreement between a directive and what inference
  would have chosen is recorded as a `conflict`, never silently dropped.
  Inference deliberately never produces `refer_mut` on its own --
  Python's local reassignment has no caller-visible mutation semantics,
  so only an explicit directive is trusted for that value.
- **Ownership-aware codegen** (`codegen/rust_writer.py`): parameters,
  return types, and `let` bindings now render as `&T`, `&mut T`, or a
  plain owned `T` based on the resolved ownership, with `// OWNERSHIP
  (inferred ...)` / `// OWNERSHIP CONFLICT: ...` marker comments where
  relevant. A function returning one of its own parameters unchanged
  echoes that parameter's own reference-ness in the return type, so a
  `refer` parameter returned directly produces a valid `-> &T` signature
  instead of a type-mismatched `-> T`.
- **Ownership decision log** (`report/ownership_log.py`): every decision
  made during a run (directive-sourced or inferred) is written to
  `ownership_log.json` and `ownership_log.md`; inferred decisions and
  conflicts are also printed to stdout as warnings.
- **`--warnings-as-fatal` / `--no-warnings-as-fatal`** (`cli.py`, default
  off): turns preflight warnings and inferred/conflicting ownership
  decisions into a hard failure (no Rust written) instead of a printed
  warning.
- **Import recursion** (`imports/resolver.py`, a scope addition beyond
  the original milestone 2 text, added at the user's request): resolves
  and converts both local, project-relative imports and installed
  third-party packages (via `importlib`), breadth-first, up to a
  configurable depth. Each resolved module goes through the same
  preflight -> IR-build -> ambiguity-marking pipeline as the entry file
  and is written under `ir/_imports/<module>.pyrir.json` and
  `_imports/<module>.rs`. Unresolvable, syntactically invalid, or
  depth-exceeded imports are recorded as skipped, never fatal to the
  overall run. New flags: `--recurse-imports`/`--no-recurse-imports`
  (default on) and `--import-depth` (default 5).
- New test modules: `tests/test_directives.py`, `tests/test_ownership.py`,
  `tests/test_imports.py`, plus Milestone 2 coverage added to
  `tests/test_ir_builder.py`, `tests/test_codegen.py`, and
  `tests/test_pipeline.py`.

### Fixed

- `pyproject.toml`'s wheel packaging installed `src/` as a nested
  `src` package while every module used bare top-level imports, and the
  console-script entry point pointed at a nonexistent `cli:app`
  -- the installed `code-convert-helper` command could not run at all. Fixed via a
  `[tool.hatch.build.targets.wheel.sources]` remap and a corrected entry
  point (`cli:app`).
- `cli.py`'s `version` command imported a nonexistent `src` module at
  runtime; now reads the version from installed package metadata.
- Integer literal parsing (`ir/builder.py`) assumed base-10 only; real
  Python source using hex/octal/binary literals or underscore separators
  (`0x1A`, `1_000`) crashed the builder. Fixed via `int(node.value, 0)`.
- A `b"..."` bytes-string literal was captured as a `ConstantExpr` with
  `py_type="str"` but an actual `bytes` value, which crashed JSON
  serialization the first time one appeared anywhere in a converted
  file. Bytes literals are now routed to the "unrecognized expression"
  placeholder instead (out of the v1 core subset's plain-`str` scope).
- `preflight/checks.py`'s undefined-name scanner used an unreliable
  `!=` comparison to detect `from x import *`; replaced with an
  `isinstance` check against `cst.ImportStar`.

### Changed

- `ROADMAP.md`: milestone 2's status updated from "In Progress" to
  "Testing", with a note on the import-recursion scope addition.
- `README.md`, `ARCHITECTURE.md`, `PLUGIN_API.md`, `HANDOFF.md`: updated
  to describe the directive/ownership/import-recursion systems.
- `examples/sample.py`: now includes `#!` directive usage (a clean
  `refer` case and a deliberate directive/inference conflict, to
  demonstrate the conflict marker) alongside the existing v1 features.
- Sphinx docs: added `docs/directives.rst`, `docs/ownership.rst`,
  `docs/imports.rst`; `docs/report.rst` extended with the ownership log;
  all `automodule` directives corrected to the actual (bare, top-level)
  module paths so the docs build cleanly.
