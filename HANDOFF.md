# Handoff notes -- current status

This captures exactly where the `pyrite` prototype stands, for continuity
when this moves into a Claude Project. Read this alongside
`PROJECT_OVERVIEW.md`, `ARCHITECTURE.md`, and `PLUGIN_API.md` (the design
docs) and `CODEBASE_SOURCE.md` (a full dump of the current source tree).

## What's built and passing (49 tests)

The full pipeline works end to end via the `pyrite` CLI:

- `preflight/checks.py` -- syntax, best-effort undefined-name scan,
  out-of-scope-construct detection
- `ir/schema.py`, `ir/builder.py`, `ir/storage.py` -- CST-based IR
  construction (using `libcst`, which preserves comments natively) and
  read-only, versioned JSON serialization
- `typing_inference/infer.py` -- literal- and hint-based type inference,
  with genuine type holes carrying partial evidence rather than guesses
- `ambiguity/resolver.py` -- collection-type, class-shape,
  error-handling, and iteration-style markers
- `codegen/rust_writer.py` -- hand-rolled IR-to-Rust text rendering
- `plugins/` -- a real subprocess JSON protocol
  (`plugins/protocol.py`), a working built-in crate-substitution plugin
  (`plugins/crate_substitution.py`), and a documented (not faked) stub
  for docstring conversion (`plugins/docs_conversion.py`)
- `report/summary.py`, `report/split_check.py` -- the ambiguities.md
  report and the optional output-length split suggestion
- `cli.py` -- `pyrite preflight`, `pyrite convert`, `pyrite inspect-ir`

## Where the current session left off

You fed back real `rust-analyzer`/`rustc` diagnostics from converting
`examples/sample.py` and asked for: a real Cargo project as output
(`Cargo.toml` + `src/main.rs`), every reported error fixed, zero errors as
the end state, and a sample file that showcases every supported feature.

**Fixed already** (present in the code in this handoff):

- The return-type bug: an unannotated function was silently defaulting
  to `()` instead of inferring from its actual `return` statements. Now
  `infer.infer_return_type(...)` (in `typing_inference/infer.py`, wired
  up in `ir/builder.py` around line 300) walks the function's returns and
  only falls back to `()` when there's genuinely no returned value --
  otherwise it infers a concrete type or an honest hole.

**Not yet done** -- these are the next steps:

1. **Cargo project scaffolding.** `pipeline.py` still only writes a bare
   `.rs` file. It needs to write `Cargo.toml` + `src/main.rs` (or
   `lib.rs`) under the output directory so `cargo check`/`cargo build`
   works directly on the output.
2. **Redundant-parens warnings.** `codegen/rust_writer.py`'s
   `render_expr` wraps every `BinOpExpr`/`CompareExpr`/`BoolOpExpr` in
   parens unconditionally. That's correct when nested inside another
   expression, but produces `unused_parens` warnings when it's the
   direct condition of an `if`/`while` or the direct value of a
   `let`/`return`. Fix: add a `top_level: bool` parameter to
   `render_expr` (or an equivalent), and have the `if`/`while` condition
   and assignment/return value call sites render top-level (no outer
   parens), while all recursive sub-expression calls keep wrapping.
3. **`fn main()` synthesis.** Nothing currently produces a `main`
   function, so `cargo build` on a binary fails with "main function not
   found." Planned approach: recognize Python's
   `if __name__ == "__main__":` top-level block and translate its body
   directly into `fn main()`, rather than treating it as a generic/
   unsupported `IfStmt`. This also gives the sample file a natural,
   idiomatic place to demonstrate every feature.
4. **The `requests.get(...)` problem -- needs your decision.** The
   crate-substitution plugin only ever suggests a replacement crate as a
   marked comment; it never implements the call. That means any sample
   code that actually calls `requests.get(...)` cannot compile as
   generated today, because nothing defines `requests` or performs a
   real HTTP request in the output. Three ways to resolve this, still
   open:
   - (a) Actually wire in `reqwest` as a real Cargo dependency and
     generate a real (blocking) HTTP call -- most impressive for a demo,
     but a bigger step than "suggest, never implement," so it would need
     its own clearly-scoped plugin behavior (e.g. a distinct "verified
     safe to implement" tier in the curated table, separate from a bare
     suggestion).
   - (b) Stub the function body with a hardcoded placeholder return
     value, keeping the `SUGGESTED CRATE` comment purely as an
     illustration, not something the generated code depends on.
   - (c) Drop networked/external-library code from the showcase sample
     entirely, and demonstrate the crate-substitution plugin some other
     way (e.g. in a doc example rather than compiled code).
5. **Update `examples/sample.py`** to showcase every v1 feature
   (functions with/without hints, classes, `if`/`elif`/`else`, `while`,
   `for` over both `range()` and a sequence, arithmetic/comparison/bool
   ops, the accumulator-mutability case, `raise`, an unsupported
   construct captured verbatim, and -- pending item 4 above -- the
   crate-substitution suggestion), plus a real `__main__` block once
   item 3 is done.
6. **Verify zero errors/warnings for real.** A working `rustc`/`cargo`
   toolchain (1.75.0) was installed via `apt-get install rustc cargo` in
   the sandbox specifically so this could be checked with a real
   compiler rather than by hand -- worth doing the same in whatever
   environment continues this work, and running `cargo check` (not just
   `cargo build`) to catch warnings too.

## Suggested first message in the new project

Something like: "Continue from HANDOFF.md -- let's resolve the
`requests.get` open question first, then do items 1-3 and regenerate the
sample to verify zero errors with `cargo check`."
