# Changelog

## Milestone 3 -- clippy-clean codegen for the core subset

### Changed

- **`codegen/rust_writer.py`** reworked to emit clippy-clean Rust by
  construction for the MVP subset, per `ROADMAP.md` milestone 3's "Done
  When" criterion:
  - `clippy::needless_return`: a function or method's final `return
    expr;` statement now renders as a bare tail expression. Early
    returns (e.g. guard clauses inside an `if` with no matching `else`)
    are untouched -- only the function body's own last statement is
    ever converted, so real control-flow returns are never misread as
    "needless".
  - Unnecessary parentheses: `render_expr` is now genuinely
    precedence-aware (`or` < `and` < comparison < `+`/`-` < `*`/`/`/`%`
    < unary < atom) and only wraps a sub-expression in parens where
    Rust's grammar actually requires it -- including the one subtle
    case that must still be wrapped: a non-associative operator's
    (`-`, `/`, `%`) right-hand operand at *equal* precedence (`a - (b -
    c)` is not `a - b - c`).
  - `clippy::explicit_iter_loop`: `for x in seq.iter()` -> `for x in
    &seq`. (`ambiguity/resolver.py`'s rationale text already described
    `&expr` as the chosen translation; codegen now actually emits it.)
  - Needless `.to_string()` in `panic!`: a literal string `raise`
    message panics directly on the literal; a bare-name message uses
    an inlined format capture (`panic!("{name}")`); only a genuinely
    computed expression falls back to the positional `panic!("{}",
    expr)` form, which can't be inlined.
  - `clippy::uninlined_format_args`: `println!("{}", x)` for a plain
    variable now renders as `println!("{x}")`.
  - `&mut self` is only emitted for a method that actually mutates one
    of the struct's own fields (a `self.attr = ...` assignment) --
    fixes a bug in `_method_needs_mut_self` that treated *any*
    assignment in the method body (including an unrelated local
    accumulator) as requiring `&mut self`.
  - `clippy::assign_op_pattern`: `x = x + y` (the common accumulator or
    `self.attr` mutation shape) now renders as `x += y` wherever the
    binary op's left operand matches the assignment target exactly;
    anything else falls back to a plain reassignment rather than risk
    misreading the shape.
- `cli.py`'s `convert` command docstring documents the clippy-clean
  rendering guarantees above.
- `tests/test_codegen.py` updated/extended to assert the new rendering
  (tail-position bare returns, precedence-correct parens, `&expr`
  iteration, inlined `panic!`/`println!`, `&mut self` only on real
  field mutation, `+=` compound assignment) in place of the old
  unconditional-parens/`.iter()`/`.to_string()` assertions.
- `ROADMAP.md`: milestone 3's status updated from "In Progress" to
  "Testing", with a note on the exact fixes applied this pass.

### Added

- `verification/clippy_check/`: a small, self-contained Cargo project
  assembled from real generated output (`output/sample.rs`'s
  `clamp`/`sum_up_to`/`greet`/`build_greeting`/`Counter`, plus a few
  extra snippets exercising precedence, `#! refer_mut`, and collection
  iteration) with every function called from `main()` so nothing is
  flagged as dead code. `verification/README.md` explains why this
  exists (no Rust toolchain is available in this environment to run
  `cargo clippy` directly) and how to run `cargo clippy --all-targets
  -- -D warnings` against it locally to confirm zero warnings.

### Notes

- `fetch_data` (the `requests.get(...)` example in `examples/sample.py`)
  is intentionally excluded from the verification project: it has
  unresolved `TYPE HOLE`s and an unconverted `requests` crate call,
  which is the correct, by-design outcome for a stdlib call outside the
  v1 core subset (see `PROJECT_OVERVIEW.md`) -- it's meant to fail
  loudly at compile time, not compile cleanly, so it isn't part of the
  "zero clippy warnings" surface.
- A pre-existing bug was found (not fixed, out of this milestone's
  scope) in `typing_inference/resolver.py::TypeResolver.resolve_for_target`:
  it derives a `for`-loop element type from a `Vec<T>`-shaped hint, but
  `type_from_annotation` doesn't recognize a generic annotation like
  `list[int]` (only bare `int`/`float`/`str`/`bool`/`None`), so a
  hinted `list[int]` resolves to a `TypeHole` and the subsequent
  `seq_type.value` access raises `AttributeError` instead of the
  intended `MandatoryHintError`. Affects
  `tests/test_resolver.py::test_for_sequence_derives_element_type_from_hinted_list_name`
  and `::test_for_sequence_derives_from_self_attr_list`. `resolver.py`
  is a Milestone 1 (v2) module not currently wired into the active
  pipeline (`pipeline.py` uses `typing_inference.infer`, not
  `.resolver`), so this doesn't affect current conversions -- flagged
  here for whoever picks up Milestone 1/2's v2 hint-resolution work.

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
