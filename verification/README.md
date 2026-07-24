# Milestone 3 clippy verification

This sandbox has no Rust toolchain (network egress is restricted to
package registries, not `rustup`/`static.rust-lang.org`), so `cargo
clippy` could not be run here directly. `clippy_check/` is a small,
self-contained Cargo project assembled from the actual generated output
in `output/sample.rs`, so you can verify Milestone 3's "Done When"
criterion locally:

```bash
cd verification/clippy_check
cargo clippy --all-targets -- -D warnings
```

Expected result: **zero warnings**. Every function in `src/main.rs` is
called from `main()` so nothing is flagged as dead code either.

## What's covered

* `clamp`, `sum_up_to`, `greet`, `build_greeting`, `Counter` -- taken
  verbatim from `output/sample.rs` (converted from `examples/sample.py`).
* `precedence_check`, `bool_precedence_check` -- exercise the new
  precedence-aware expression rendering (parens kept only where
  regrouping would change the result).
* `refer_mut_example` -- exercises an explicit `#! refer_mut` directive.
* `collection_example` -- exercises sequence iteration (`for x in
  &items`) and the accumulator `+=` fix.

`fetch_data` (the `requests.get(...)` example) is intentionally **not**
included -- it has unresolved `TYPE HOLE`s and an unconverted `requests`
crate call, which is the correct, by-design outcome for a stdlib call
outside the v1 core subset (see `PROJECT_OVERVIEW.md`): it's meant to
fail loudly at compile time, not compile cleanly.

## Fixes verified by this project

1. `clippy::needless_return` -- a function/method's final `return expr;`
   renders as a bare tail expression.
2. Unnecessary parentheses -- binary/comparison/boolean/unary
   expressions are rendered with real operator-precedence awareness.
3. `clippy::explicit_iter_loop` -- `for x in seq.iter()` -> `for x in &seq`.
4. Unneeded `.to_string()` in `panic!` messages.
5. `clippy::uninlined_format_args` -- `println!("{}", x)` -> `println!("{x}")`.
6. `&mut self` only emitted for methods that actually mutate a field.
7. `clippy::assign_op_pattern` -- `x = x + y` -> `x += y` where sound.
