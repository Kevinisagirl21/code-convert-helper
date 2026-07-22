# Architecture — abstract logic flow

Seven stages, each with a single responsibility. Every stage's output is the
next stage's only input — no stage reaches back into raw source text once
stage 2 has run.

```
Ingest & preflight
      |
Parse & extract comments
      |
Type inference
      |
Build IR (locked)
      |
Resolve ambiguities
      |
Generate Rust
      |
Verify & report
```

## Stage 0 — Ingest & preflight checks

Before anything is translated, verify the input file is sound enough to
translate at all:

- Parse with Python's `ast` module — hard syntax errors stop here.
- Static scope check — undefined names, unresolved imports (pyflakes-style).
- Best-effort type check against any existing type hints (mypy-strict-style
  checking, scoped to whatever v1 supports — no need to reimplement all of
  mypy, just enough to catch real inconsistencies in the supported subset).
- Scan for out-of-scope constructs (generators, decorators, async, `eval`,
  etc.) and record their locations — this doesn't fail the run, it just
  means those regions will become "unsupported construct" IR nodes later.

Output: a preflight report (pass/fail, list of errors, list of
out-of-scope-but-not-fatal constructs found). A hard failure here means the
tool refuses to proceed — feeding a broken or unsound file into a translator
just produces confidently wrong Rust.

## Stage 1 — Parse & extract comments

Python's `ast` module silently discards comments, so this stage runs two
passes over the same file:

1. `ast.parse` for the actual syntax tree.
2. `tokenize` (or equivalent) for a separate stream of every comment token,
   each carrying its exact line/column and the raw text.

The two are not merged yet — that happens in stage 2's comment-association
pass, once semantic nodes exist to attach to.

## Stage 2 — Type inference

Walks the AST and, for the v1 supported subset only:

- Uses existing type hints directly where present.
- Infers obvious cases locally (e.g. `x = 5` → `int`, `x = "a"` → `str`,
  `x = [1, 2]` → `list[int]`).
- Everything it can't confidently resolve becomes an explicit **type hole**:
  a placeholder with a unique ID, not a guess. Type holes are meant to be
  filled later — either by a human editing generated Rust, or by a smarter
  inference pass in a future revision that knows how to re-open the IR.
- Type holes are not just "unknown" — they carry whatever partial
  information inference did manage to gather (e.g. "used with `+` against
  a `str`", "compared with `<` against an `int` elsewhere", "returned from
  a function typed `-> float`"). This partial info travels with the hole
  into codegen and surfaces as a reference comment directly above the
  generated line (see stage 5), rather than being thrown away.

This is also where the comment-association pass runs: each extracted
comment is attached to the IR node it semantically describes, using
heuristics such as:

- A standalone comment immediately before a statement, at the same
  indentation, attaches as that statement's *leading* comment.
- A comment trailing code on the same line attaches as that node's
  *trailing* comment.
- A comment sitting alone inside a block (e.g. explaining an upcoming
  branch) attaches to the nearest enclosing or following statement at the
  same indentation level, with a lower confidence score.

Every association carries a confidence value so low-confidence guesses can
be flagged for review rather than silently trusted.

## Stage 3 — Build IR (locked)

This is the project's core artifact, not an internal implementation detail.

**Schema sketch** (illustrative, not final) — one function, serialized:

```json
{
  "schema_version": "v1_core",
  "node_id": "fn_0007",
  "kind": "FunctionDef",
  "name": "clamp",
  "params": [
    {"name": "value", "type": {"kind": "concrete", "value": "int"}},
    {"name": "lo", "type": {"kind": "concrete", "value": "int"}},
    {"name": "hi", "type": {
      "kind": "hole",
      "id": "hole_003",
      "known_info": ["compared with '>' against param 'value' (int)", "returned from a call site expecting int"]
    }}
  ],
  "return_type": {"kind": "concrete", "value": "int"},
  "comments": {
    "leading": [{"text": "clamp value into [lo, hi]", "confidence": 0.95}]
  },
  "body": ["..."],
  "source_span": {"file": "utils.py", "start_line": 12, "end_line": 18}
}
```

Key properties of the schema:

- **Type slots are explicit values**, either `concrete` or `hole`, never
  silently defaulted to something guessed.
- **Unsupported constructs** (anything stage 0 flagged as out-of-scope) are
  captured as an opaque `SourceFragment` node: exact original text plus
  location, with `kind: "unsupported"`. Nothing is dropped — a future
  revision's job is to find these nodes and replace them with real IR,
  without re-parsing the whole file.
- **Comments live on the node**, not in a side table keyed by line number —
  this is what "attach to whichever node it semantically describes" means
  structurally.

**Read-only by default.** Once written, the IR file is locked (read-only
permissions) for the schema version that produced it. This isn't about
distrust of the user — it's about protecting the invariant that the schema
depends on internal consistency between nodes (e.g. a resolved type slot
that other nodes reference). Hand-editing a locked JSON file outside the
tool's own logic is how that invariant breaks silently.

**How it unlocks for later revisions.** When a future version adds support
for, say, decorators, it doesn't edit the v1 IR file directly. It:

1. Reads the existing locked `v1_core` IR (read-only, as always).
2. Runs a schema-upgrade pass that specifically targets `SourceFragment`
   nodes matching the new capability (e.g. only ones containing decorator
   syntax), re-parsing *just those fragments* into proper IR nodes.
3. Writes a new IR file under a new schema version (e.g. `v2_decorators`),
   locked in turn.

This is the mechanism behind "store the outputs so later revisions can run
in addition to v1" — each revision is additive and versioned, never a
silent in-place mutation of a prior locked file.

**File layout.** IR is one file per source file — including files that
belong to imported local modules or libraries, not just the entry file.
The directory structure mirrors the project:

```
my_project/
  main.py
  utils.py
  ir/
    main.pyrir.json          <- IR for main.py
    utils.pyrir.json         <- IR for utils.py
    _imports/
      some_lib/
        __init__.pyrir.json  <- IR for an imported module's own source
        helpers.pyrir.json
```

Each imported module gets converted through the same stages 0–3 as the
entry project (preflight, parse, infer, build IR) and lands in its own
`_imports/<module_path>/` subtree, kept separate from the user's own files
so it's obvious at a glance what's project code and what's a dependency.
Cross-file references (e.g. a function in `main.py` calling into `utils.py`)
are IR-to-IR references by file + node ID, not string lookups against raw
source, so stage 4 and 5 can resolve a cross-file call's type information
without re-parsing anything.

**Milestone 2 status.** Import recursion is implemented (`imports/resolver.py`,
driven from `pipeline.convert_file`): both local, project-relative modules and
installed third-party packages (found via `importlib`) are followed, up to a
configurable depth (`--import-depth`, default 5), and each resolved module is
converted independently and written under `ir/_imports/` as this section
describes. Cross-file IR-to-IR references (rather than each imported module's
IR standing alone) remain future work.

## Plugin system

The core stages above are fixed; the plugin system is the extension point
for everything that's a matter of ecosystem knowledge or house style
rather than core conversion logic. Full protocol contract lives in
`PLUGIN_API.md` — summary of where plugins attach into this pipeline:

- **After stage 2 (type inference), before stage 4 (ambiguities):** a
  library-substitution plugin can see calls into known Python
  modules/libraries and attach a suggested Rust crate replacement to the
  relevant IR node, using the same "marked suggestion, never silent"
  treatment as any other ambiguity.
- **Anywhere comments/docstrings are attached (stage 2 onward):** a
  docs-conversion plugin can transform a recognized docstring format
  (Sphinx, Google-style, NumPy-style) into a structured doc-comment node
  that stage 5 emits as idiomatic `///` rustdoc.
- Plugins are invoked as a subprocess per call in v1 (simple, no shared
  process state to reason about); a longer-lived daemon mode is a
  reasonable later optimization once the protocol has stabilized.
- **Python is the primary plugin language** (ease of authorship — a thin
  SDK package handles the JSON protocol so a plugin author just writes a
  function), but the protocol itself is language-agnostic, so a compiled
  plugin (Rust, Go, etc.) implements the same stdin/stdout contract
  directly with no special casing by the host.

## Stage 4 — Resolve ambiguities

Walks the IR looking for nodes where more than one reasonable Rust mapping
exists — e.g.:

- Python `list` → `Vec<T>` (default) vs a fixed-size array vs a slice.
- Python `dict` → `HashMap` (default) vs `BTreeMap` (if ordering matters).
- Python class → `struct` + `impl` (default) vs a trait object, depending
  on whether it's used polymorphically elsewhere in the file.
- Optional/`None`-able values → `Option<T>` (usually the only sane choice,
  so this one is rarely actually ambiguous).

**Default (batch) behavior:** pick the conservative default, and attach a
structured ambiguity annotation to the IR node recording what was chosen
and what the alternatives were. No config file ever influences this choice
— by design, since the same Python pattern can need a different Rust shape
depending on surrounding usage, which a static rule can't see.

**Optional interactive mode:** a `--interactive` flag walks through each
ambiguity live, shows the choice and a short rationale, and applies the
user's pick immediately for that run. This choice is never written to a
persisted config — it's a session-scoped decision, not a rule.

**Milestone 2 addition: ownership.** Alongside these existing ambiguity
categories, a parameter/return type/assignment's *ownership* (`owner` /
`refer` / `refer_mut` / `move`) is resolved the same way in spirit: an
explicit `#!` directive always wins, and disagreement with what
usage-based inference would have picked is recorded (never silently
dropped) as a `conflict` on the `OwnershipDecision`. See
`ownership/resolver.py` and `directives/parser.py`.

## Stage 5 — Generate Rust

Walks the finalized IR and emits Rust text (not via an AST-to-AST library
like `syn`/`quote`, which strip comments — instead a template/pretty-printer
approach that has full control over exactly where comments and markers
land):

- Comments are reinserted at the position dictated by their attached node
  (leading comment above the item, trailing comment same-line where valid
  Rust syntax allows it).
- Every stage-4 ambiguity becomes a visible marker at the exact site, e.g.:

  ```rust
  // AMBIGUOUS[collection-type]: chosen Vec<i32>; consider BTreeMap if
  // insertion order doesn't matter here — see ambiguities.md #12
  let items: Vec<i32> = ...;
  ```

- Unresolved type holes are emitted as an unmistakable placeholder (not a
  silently-wrong concrete type) with a reference comment directly above the
  line carrying whatever partial information stage 2 gathered, e.g.:

  ```rust
  // TYPE HOLE hole_003: compared with '>' against param 'value' (int);
  // returned from a call site expecting int — likely `i32` or `i64`
  let hi: /* unresolved */ TypeHole003 = ...;
  ```

- A library-substitution plugin suggestion (see "Plugin system") is marked
  the same way as any other ambiguity — never silently swapped in:

  ```rust
  // SUGGESTED CRATE: Python `requests.get(...)` -> consider `reqwest`
  // (blocking client, add `reqwest = { version = "*", features = ["blocking"] }`)
  let response = /* unconverted: requests.get(url) */;
  ```

- A parameter, return type, or `let` binding's resolved ownership controls
  whether it renders as `&T`, `&mut T`, or a plain owned `T`; an inferred
  (no-directive) decision or a directive/inference conflict gets its own
  marker comment, e.g.:

  ```rust
  // OWNERSHIP (inferred 'refer'): 'name' is only read or passed along; never stored or returned
  fn greet(name: &String) -> &String { ... }
  ```

- A companion `ambiguities.md` report lists every marker (type holes,
  idiom choices, and plugin suggestions alike) with line number and
  rationale, for easy scanning without grepping the whole file. A separate
  `ownership_log.{json,md}` does the same specifically for every ownership
  decision made during the run.

## Stage 6 — Verify & report

- Sanity-check the generated Rust is at least syntactically parseable
  (e.g. via the `syn` crate in parse-only mode) — this is not a full
  compile, since intentional type holes may remain, just a check that the
  tool didn't emit malformed Rust.
- Emit a final summary: nodes converted, ambiguities flagged, type holes
  remaining, unsupported constructs preserved for a future revision.
- **Output length check (optional).** Compare generated line count against
  the source file's line count. If it diverges past a configured
  threshold — by ratio (e.g. output over 1.5x input) or by absolute count
  (e.g. over 500 lines), whichever triggers first, both with sane defaults
  overridable in tool config — prepend a leading comment to the output
  file noting the divergence and suggesting the user consider splitting
  it. This is about file organization, not translation correctness, so it
  lives in ordinary tool config rather than being a fixed rule.

## Suggested implementation stack

Given the primary output is Rust, building the tool itself in Rust is a
natural fit (and doubles as a proof that the approach works):

- **Front-end parsing:** `rustpython-parser` crate — gives a full Python
  AST without shelling out to a Python interpreter.
- **Comment extraction:** Python's tokenizer behavior can be replicated
  from the same crate's lexer, or by running a small embedded tokenizer
  pass matched to it.
- **IR serialization:** plain `serde` + JSON (or RON, if human-editability
  during debugging matters more than tooling ubiquity — worth prototyping
  both).
- **Rust codegen:** hand-written templating rather than `syn`/`quote`
  (which normalize away comments and exact formatting) — codegen needs to
  own comment placement precisely.
- **Parse-check on output:** `syn` in parse-only mode, used purely as a
  validator, not a generator.

This is a recommendation, not a constraint — happy to reconsider if there's
a reason to prefer implementing the tool in Python instead (e.g. faster
iteration while the IR schema is still in flux). (In practice, this
prototype's implementation to date has been in Python, for faster
iteration while the schema was still in flux — see `HANDOFF.md`.)

## Open questions for the next round

- Exact confidence threshold below which a comment association gets
  flagged for review rather than silently trusted.
- Plugin invocation model: v1 uses one-shot subprocess calls for
  simplicity — worth deciding now whether a future daemon mode changes the
  protocol shape, or can be added without breaking v1 plugins.
- Plugin discovery/registration: how does the host find installed plugins
  (a manifest file per plugin, a directory convention, explicit
  registration in tool config)?
- Curated crate-substitution table format and update process — bundled
  and versioned with the tool, or fetched/updateable separately?
- Docs-conversion plugin scope for v1 — Sphinx only, or Sphinx plus
  Google/NumPy docstring styles from the start?
- Now that import recursion is implemented, cross-file IR-to-IR references
  (a call in the entry file resolving a converted import's function type
  directly, rather than each imported module's IR standing alone) are the
  natural next step.
