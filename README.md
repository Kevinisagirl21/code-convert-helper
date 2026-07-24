# code-convert-helper

A Python-to-Rust conversion assistant that preserves comments and never
silently resolves a judgment call -- it marks every ambiguity, type hole,
and unsupported construct directly in the generated code instead.

This is a **v1 core-subset prototype**: functions, single-inheritance-free
classes, `if`/`while`/`for`, and the core literal types (`int`, `float`,
`str`, `bool`, `list`, `dict`, `None`). See `PROJECT_OVERVIEW.md` and
`ARCHITECTURE.md` (in the parent design docs) for the full roadmap and the
reasoning behind each design decision.

## Milestone 2: `#!` ownership directives + import recursion

On top of the v1 core subset, this build adds:

* **`#!` same-line directives** for ownership: `#! owner`, `#! refer`,
  `#! refer_mut`, `#! move`, attachable to a parameter's trailing comma,
  a function's `-> ReturnType:` line, or an assignment. See
  `ROADMAP.md` milestone 2 and `src/directives/parser.py` for the exact
  grammar.
* **Usage-based ownership inference** when no directive is present
  (`src/ownership/resolver.py`), logged to `ownership_log.json`/`.md`
  and printed as a warning -- or a hard failure under
  `--warnings-as-fatal`.
* **Import recursion**: `code-convert-helper convert` follows local and installed
  third-party imports by default (`--recurse-imports`, default on;
  `--import-depth`, default 5), converting each one under `ir/_imports/`.

## Milestone 3: clippy-clean codegen

Generated Rust for the core subset now aims to be clippy-clean *by
construction*, not just readable:

* `clippy::needless_return` -- a function/method's final `return expr;`
  renders as a bare tail expression (early returns elsewhere are left
  untouched, since they're genuine control flow).
* Unnecessary parentheses -- expressions render with real
  operator-precedence awareness, so parens appear only where Rust's
  grammar actually requires them.
* `clippy::explicit_iter_loop` -- `for x in seq.iter()` renders as
  `for x in &seq`.
* Needless `.to_string()` in `panic!` messages is gone; a literal
  message panics directly, a bare name uses an inlined format capture.
* `clippy::uninlined_format_args` -- `println!("{}", x)` renders as
  `println!("{x}")` for a plain variable.
* `&mut self` is only emitted for a method that actually mutates one of
  its own fields.
* `clippy::assign_op_pattern` -- `x = x + y` renders as `x += y` where
  the shape is unambiguous.

See `verification/README.md` for how to check this with `cargo clippy`
against a real Rust toolchain (not available in this sandbox).

## Install

```bash
pip install -e ".[dev]"
```

## Usage

```bash
# Stage-0 checks only (syntax, best-effort undefined-name scan, scope scan)
code-convert-helper preflight my_module.py

# Full conversion: writes output/my_module.rs, output/ir/my_module.pyrir.json,
# output/ambiguities.md, output/ownership_log.{json,md}, and (by default)
# output/ir/_imports/*.pyrir.json + output/_imports/*.rs for every
# resolvable import.
code-convert-helper convert my_module.py --out output

# With the optional output-length split suggestion enabled
code-convert-helper convert my_module.py --out output --split-check

# Treat preflight warnings and inferred/conflicting ownership decisions
# as hard failures
code-convert-helper convert my_module.py --out output --warnings-as-fatal

# Disable import recursion, or change its depth
code-convert-helper convert my_module.py --out output --no-recurse-imports
code-convert-helper convert my_module.py --out output --import-depth 2

# Inspect a previously generated (locked, read-only) IR file
code-convert-helper inspect-ir output/ir/my_module.pyrir.json
```

## What to expect in the output

Every generated `.rs` file may contain these marker comments:

- `// TYPE HOLE <id>: <evidence>` -- the type couldn't be confidently
  resolved. The generated type name (e.g. `TypeHole_hole_0001`) is
  intentionally not a real Rust type, so the file won't silently compile
  with a wrong guess.
- `// AMBIGUOUS[<category>]: <rationale>` -- more than one reasonable
  Rust translation existed; a conservative default was chosen and marked.
- `// OWNERSHIP (inferred '<value>'): <evidence>` -- no `#!` ownership
  directive was present, so usage-based inference picked `<value>`.
- `// OWNERSHIP CONFLICT: <details>` -- an explicit `#!` directive
  disagreed with what usage-based inference would have chosen. The
  directive's value always wins; the disagreement is still surfaced,
  never silently dropped.
- `// UNSUPPORTED (<reason>)` followed by a `/* ... */` block -- a
  construct outside the v1 core subset, with the exact original Python
  kept verbatim for a future revision (or a human) to pick up.

Beyond the markers, the code itself (Milestone 3) is written to be
clippy-clean: bare tail-expression returns, precedence-correct
parentheses, `&expr` sequence iteration, inlined `panic!`/`println!`
format args, `&mut self` only where a field is actually mutated, and
`+=`-style compound assignment where sound.

None of these are configurable via a rules file, by design -- see
`PROJECT_OVERVIEW.md`'s second principle. Tooling behavior (like the
split-check thresholds, `--warnings-as-fatal`, and import-recursion depth
above) is ordinary configuration; how any given line of Python gets
translated is not.

## Project layout

```
src/
    directives/       Milestone 2: the `#!` same-line directive grammar
    ownership/        Milestone 2: ownership inference + resolution
    imports/          Milestone 2: import resolution + recursive conversion
    preflight/        stage 0: syntax, scope, out-of-scope-construct scan
    ir/               the IR schema, CST -> IR builder, and (de)serialization
    typing_inference/ literal- and hint-based type inference, type holes
    ambiguity/        ambiguity markers (collection types, class shape, ...)
    codegen/          IR -> Rust text rendering (Milestone 3: clippy-clean)
    plugins/          the subprocess plugin protocol + built-in plugins
    report/           run summary, ambiguities.md, ownership_log, split-length check
    pipeline.py       wires every stage together
    cli.py            the `code-convert-helper` command-line interface
docs/                 Sphinx documentation (see below)
tests/                pytest test suite
examples/             a sample Python file to try the tool on
verification/         Milestone 3: Cargo project for local `cargo clippy` verification
```

## Building the docs

```bash
pip install -e ".[dev]"
sphinx-build -b html docs docs/_build/html
```

## Running the tests

```bash
pip install -e ".[dev]"
pytest
```
