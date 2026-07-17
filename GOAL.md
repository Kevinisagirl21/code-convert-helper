# GOAL: a future Rust implementation of py2rust

> **This file is intentionally kept outside the `py2rust` repository.**
> It's a long-range aspiration, not a description of the current
> prototype, and it shouldn't be committed alongside `ARCHITECTURE.md`,
> `PROJECT_OVERVIEW.md`, or `PLUGIN_API.md` — those describe what v1
> actually is (a Python tool). This file describes something the project
> might become later.

## Why this exists

An earlier draft of `ARCHITECTURE.md` suggested implementing py2rust
itself in Rust. The v1 prototype was ultimately built in Python instead
(see `ARCHITECTURE.md`'s current "Implementation stack" section for what
was actually used and why). That original Rust-oriented plan is
preserved here rather than deleted outright, in case it's worth
revisiting once the Python prototype's IR schema and ambiguity-marking
approach have proven themselves out.

## The original idea

Given the primary *output* of the tool is Rust, there's a case for
building the tool itself in Rust too — partly as a dogfooding proof that
the approach works, partly for distribution (a single static binary,
no Python runtime required for end users converting their own code).

Sketch of what that stack could look like:

- **Front-end parsing:** the `rustpython-parser` crate, to get a full
  Python AST without shelling out to a Python interpreter.
- **Comment extraction:** replicate Python's tokenizer behavior from the
  same crate's lexer, or run a small embedded tokenizer pass matched
  to it — needed because a bare AST (unlike `libcst`'s CST) doesn't keep
  comments attached to the nodes they describe.
- **IR serialization:** plain `serde` + JSON (or RON, if
  human-editability during debugging matters more than tooling
  ubiquity — worth prototyping both).
- **Rust codegen:** hand-written templating rather than `syn`/`quote`
  (which normalize away comments and exact formatting) — codegen needs
  to own comment placement precisely, same requirement as the current
  Python `codegen/rust_writer.py`.
- **Parse-check on output:** `syn` in parse-only mode, used purely as a
  validator, not a generator.

## Why it's not what v1 did

`libcst` (Python) keeps every comment attached to the CST node it
belongs to natively, which is exactly the "comments are first-class"
requirement in `PROJECT_OVERVIEW.md`. Getting equivalent behavior out of
`rustpython-parser`'s plain AST would mean building a custom
token-to-node association pass from scratch — solvable, but a real
chunk of extra work with no working reference implementation to model
it on. Python also let the prototype iterate on the IR schema quickly
while it was still in flux. Once (if) the schema and ambiguity-marking
model stop changing week to week, a Rust rewrite for distribution
reasons becomes a much more contained, well-scoped project.

## If this gets picked up later

Whoever picks this up should:

1. Treat the current Python `ir/schema.py` as the schema to port, not a
   starting point to redesign from scratch — it's already been
   validated against real code via the test suite.
2. Solve comment-association first, in isolation, before anything else
   — it's the one piece that doesn't already have a known-working Rust
   library to lean on.
3. Re-check whether `rustpython-parser` is still the best-maintained
   option for a full Python AST in Rust at that time; this recommendation
   reflects the state of that ecosystem when the idea was first written
   down, not necessarily its state whenever this is revisited.
