# py2rust

A Python-to-Rust conversion assistant that preserves comments and never
silently resolves a judgment call -- it marks every ambiguity, type hole,
and unsupported construct directly in the generated code instead.

This is a **v1 core-subset prototype**: functions, single-inheritance-free
classes, `if`/`while`/`for`, and the core literal types (`int`, `float`,
`str`, `bool`, `list`, `dict`, `None`). See `PROJECT_OVERVIEW.md` and
`ARCHITECTURE.md` (in the parent design docs) for the full roadmap and the
reasoning behind each design decision.

## Install

```bash
pip install -e ".[dev]"
```

## Usage

```bash
# Stage-0 checks only (syntax, best-effort undefined-name scan, scope scan)
py2rust preflight my_module.py

# Full conversion: writes output/my_module.rs, output/ir/my_module.pyrir.json,
# and output/ambiguities.md
py2rust convert my_module.py --out output

# With the optional output-length split suggestion enabled
py2rust convert my_module.py --out output --split-check

# Inspect a previously generated (locked, read-only) IR file
py2rust inspect-ir output/ir/my_module.pyrir.json
```

## What to expect in the output

Every generated `.rs` file may contain three kinds of marker comments,
each meaning something different:

- `// TYPE HOLE <id>: <evidence>` -- the type couldn't be confidently
  resolved. The generated type name (e.g. `TypeHole_hole_0001`) is
  intentionally not a real Rust type, so the file won't silently compile
  with a wrong guess.
- `// AMBIGUOUS[<category>]: <rationale>` -- more than one reasonable
  Rust translation existed; a conservative default was chosen and marked.
- `// UNSUPPORTED (<reason>)` followed by a `/* ... */` block -- a
  construct outside the v1 core subset, with the exact original Python
  kept verbatim for a future revision (or a human) to pick up.

None of these are configurable via a rules file, by design -- see
`PROJECT_OVERVIEW.md`'s second principle. Tooling behavior (like the
split-check thresholds above) is ordinary configuration; how any given
line of Python gets translated is not.

## Project layout

```
src/
    __init__.py       package version
    cli.py            the `py2rust` command-line interface
    pipeline.py       wires every stage together
    preflight/        stage 0: syntax, scope, out-of-scope-construct scan
    ir/               the IR schema, CST -> IR builder, and (de)serialization
    typing_inference/ literal- and hint-based type inference, type holes
    ambiguity/        ambiguity markers (collection types, class shape, ...)
    codegen/          IR -> Rust text rendering
    plugins/          the subprocess plugin protocol + built-in plugins
    report/           run summary, ambiguities.md, the split-length check
docs/                 Sphinx documentation (see below)
tests/                pytest test suite
examples/             a sample Python file to try the tool on
```

Every module above is a flat top-level import (`from ir import builder`,
`from codegen import rust_writer`, and so on) -- there is no `py2rust`
subpackage wrapping them. `pyproject.toml` maps `src/` as the package
root so this flat layout is also what gets installed.

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

## Extending this prototype

The design docs (`PROJECT_OVERVIEW.md`, `ARCHITECTURE.md`, `PLUGIN_API.md`)
describe several features this prototype leaves room for but doesn't fully
implement yet:

- Multi-file projects and the `_imports/` IR directory layout for
  converted dependencies (this prototype handles one file at a time).
- The docstring-to-rustdoc plugin (`plugins/docs_conversion.py` is
  a documented stub explaining what's needed).
- A daemon-mode plugin protocol (today's subprocess-per-call model in
  `plugins/protocol.py` is deliberately the simple starting point).
- IR schema versioning/upgrade passes for a v2 that adds decorators,
  generators, or `async`.
