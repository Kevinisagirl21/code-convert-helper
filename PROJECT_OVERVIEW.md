# Project overview — Python → Rust conversion assistant

*(working name: "py2rust" — a placeholder; rename freely)*

## The problem

Porting a codebase to Rust by hand is slow and error-prone in a specific way: the
mechanical parts (loops, arithmetic, string handling, basic control flow) are
tedious but not hard, while the parts that actually need a human — choosing
`Vec<T>` vs a slice, `String` vs `&str`, `HashMap` vs `BTreeMap`, whether a
class becomes a struct+impl or a trait object — get buried in the tedium.
Existing transpilers tend to either (a) go for full automation and produce
Rust nobody would choose to write by hand, silently picking one answer for
every ambiguous case, or (b) require so much upfront configuration that
they're slower than just doing the port manually.

This tool's bet: separate the mechanical translation (safe to automate) from
the judgment calls (never silently resolved — always surfaced), and make the
intermediate step reusable so later, smarter revisions of the tool don't force
a full restart.

## Core philosophy

1. **Never guess silently.** Where more than one idiomatic Rust translation
   exists for a Python pattern, the tool picks a conservative default *and*
   marks the site inline in the output so it's trivially greppable. Nothing
   ambiguous is quietly resolved and hidden.
2. **No config files for conversion rules — but config files are fine for
   tooling behavior.** The same Python syntax can need a completely
   different Rust shape depending on surrounding context (ownership,
   mutation, lifetime), so a static rules file governing *how ambiguity
   gets resolved* would be actively unsafe — it would misfire the moment
   context changed. That specific decision is always either a conservative
   marked default or a live, session-scoped interactive choice, never a
   persisted rule. This restriction does not extend to tooling/plugin
   *behavior* — thresholds, curated substitution tables, plugin
   registration, and similar settings are ordinary config and welcome
   (see "Plugin system" below).
3. **Comments are first-class, not metadata.** Comments are associated with
   the code they semantically describe (not just their line number), carried
   through the whole pipeline, and re-emitted attached to the right Rust
   construct.
4. **The intermediate representation (IR) is a real, versioned artifact** —
   written to disk, inspectable, and read-only by default. It is not a
   throwaway internal data structure. This is what lets future revisions
   (async, generators, decorators, dynamic features, C/C++ front-ends) build
   *on top of* a v1 conversion instead of reprocessing from scratch.
5. **Fail loud, not quiet.** Anything the tool can't confidently handle —
   unsupported syntax, unresolved types, ambiguous idiom choices — shows up
   as an explicit marker in the output or a line in the summary report.
   Silence is never a sign that a file converted cleanly by default.

## V1 scope

| In scope for v1 | Out of scope for v1 (captured, not lost — see Architecture doc) |
|---|---|
| Functions, function calls, return values | Generators / `yield` |
| Classes (single inheritance), methods, `__init__` | Multiple inheritance, metaclasses |
| Control flow: `if`/`elif`/`else`, `while`, `for` over sequences | `async`/`await` |
| Core types: `int`, `float`, `str`, `bool`, `list`, `dict`, `tuple`, `None` | Decorators |
| Arithmetic, comparison, boolean, string operators | `eval`/`exec`, monkey-patching, reflection |
| Type hints where present; local inference for simple literal assignment | Context managers (`with`), custom exception hierarchies |
| Basic exceptions: `try`/`except`/`raise` for control flow (best-effort) | Full stdlib modeling (only trivial stand-ins in v1) |

Anything in the right-hand column that appears in a v1 run is not silently
dropped or mistranslated — it's captured verbatim in the IR as an explicit
"unsupported construct" node (see Architecture doc) with its exact source
text and location, flagged in the summary report, and left out of Rust
codegen (or emitted as a loudly-commented stub) until a later revision adds
support for it.

## Why Python-only first

C and C++ bring a preprocessor, manual memory management, and header/TU
boundaries that are a genuinely different translation problem (arguably
closer to what already-existing C→Rust tools like `c2rust` solve). Python
brings dynamic typing and duck typing, which is a different genuinely hard
problem: inferring enough concrete type information to emit Rust at all.
Solving one problem well first, with an architecture that generalizes,
beats spreading effort across three very different front-ends. C/C++
front-ends become an additive "new front-end plugged into the same IR"
exercise later — not a rewrite.

## Plugin system

The core pipeline (stages 0–6 in `ARCHITECTURE.md`) stays fixed and
opinionated. Everything that's inherently a matter of taste, ecosystem
knowledge, or house style is pushed out to plugins instead of grown into
the core. Full contract in `PLUGIN_API.md`; summary here:

- Plugins are ordinary scripts or executables that speak a small,
  documented JSON protocol over stdin/stdout. **Python is the primary,
  easiest path** (a thin SDK package handles the protocol so plugin authors
  just write a function), but because the contract is just JSON-over-stdio,
  a **compiled-language plugin** (Rust, Go, whatever) works identically to
  the host — there's no special-casing of scripted vs. compiled plugins.
- Two built-in plugins ship with the tool as reference implementations and
  real functionality on day one:
  1. **Crate substitution** — suggests an existing Rust crate in place of
     a Python stdlib/library call (e.g. `requests` → `reqwest`) from a
     built-in curated table. Always emitted as a *marked suggestion* at
     the call site, never a silent substitution — this follows the same
     "never guess silently" rule as core ambiguity handling, even though
     the mapping table itself is ordinary, editable config data.
  2. **Docs conversion** — converts Python docstrings (Sphinx, Google-style,
     NumPy-style) into native output-language doc comments (rustdoc `///`),
     as its own pass over the IR's comment/docstring nodes.

## Output length check (optional)

After Rust generation, the tool compares output line count against input
line count. If the two diverge past a configurable threshold — checked
both as a ratio (output notably larger/smaller than input) and as an
absolute line count — the generated file gets a leading comment suggesting
the user consider splitting it, rather than silently handing back one very
large file. This is opt-in and its thresholds live in ordinary tool config
(not a conversion rule), since it's a judgment about file organization, not
about how any given line of code gets translated.

## Non-goals (for now)

- Producing perfectly idiomatic, hand-polished Rust on the first pass. The
  goal is a strong, honest first draft with all judgment calls surfaced —
  not a black box that claims perfection.
- Full stdlib or third-party package translation. v1 assumes core-language
  code; heavy stdlib/dependency use is an explicit "out of scope, marked"
  case.
- Performance-tuned output. Correctness and clarity of what needs human
  attention come first.

## Immediate next steps

1. Lock the v1 Python grammar subset precisely (a formal list of supported
   `ast` node types).
2. Design the versioned IR schema (draft in `ARCHITECTURE.md`).
3. Build the preflight checker (syntax + scope + best-effort type check)
   as a standalone, testable stage — it has value even before any Rust
   codegen exists.
4. Prototype comment-association heuristics on a handful of real-world
   Python files and check the attachment accuracy by eye before writing
   any Rust output logic.
