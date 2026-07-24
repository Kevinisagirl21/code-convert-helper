# Plugin API

The core pipeline (`ARCHITECTURE.md`) is fixed and opinionated. Plugins are
where ecosystem knowledge and house style live instead — library
substitutions, docs conversion, and whatever else future users need,
without growing the core.

## Design goals

1. **Language-agnostic.** Python should be the easy path; a compiled
   plugin should be exactly as capable, with no second-class protocol.
2. **No silent behavior.** A plugin can *suggest* — it cannot make the
   host silently swap something in. Every plugin contribution goes through
   the same "marked, never hidden" treatment as core ambiguity handling.
3. **Simple before scalable.** v1 uses one-shot subprocess calls: the host
   writes a JSON request to the plugin process's stdin, reads one JSON
   response from stdout, and the process exits. No persistent daemon, no
   shared memory, nothing to get out of sync. A daemon mode is a fine
   later optimization once the contract has proven itself.

## Protocol contract

The host invokes a plugin as `<plugin_executable> <hook_name>`, writes a
single JSON object to stdin, and expects a single JSON object back on
stdout before the process exits. Non-zero exit or malformed JSON means the
plugin's suggestion is skipped for that node — a plugin failing never
fails the overall conversion.

**Request** (shape varies slightly by hook, this is the substitution-hook
example):

```json
{
  "protocol_version": "1",
  "hook": "library_substitution",
  "context": {
    "call": "requests.get",
    "args_summary": ["url: str"],
    "ir_node_id": "call_0042",
    "source_span": {"file": "utils.py", "start_line": 40, "end_line": 40}
  }
}
```

**Response:**

```json
{
  "protocol_version": "1",
  "suggestion": {
    "summary": "consider `reqwest` (blocking client)",
    "detail": "add `reqwest = { version = \"*\", features = [\"blocking\"] }`",
    "confidence": "curated"
  }
}
```

or, if the plugin has nothing to add for this node:

```json
{"protocol_version": "1", "suggestion": null}
```

A `confidence` field of `"curated"` (from a maintained mapping table) vs.
`"heuristic"` (guessed by the plugin itself, e.g. from a package search) is
carried through to the final marker in generated Rust, so the user can
tell a well-trodden suggestion from a speculative one at a glance.

## Hook points

| Hook name | Fires on | Purpose |
|---|---|---|
| `library_substitution` | Each recognized call into an imported module | Suggest a Rust crate/macro replacement |
| `docstring_conversion` | Each IR node carrying a docstring | Convert a recognized docstring format into a structured doc-comment node |

More hooks can be added over time (this table is expected to grow); adding
one is additive to the protocol version, not a breaking change to existing
plugins.

## Python SDK (primary path)

A small pip package (`code-convert-helper-plugin-sdk`) handles the JSON framing so a
Python plugin author only writes the decision logic:

```python
from code-convert-helper_plugin_sdk import plugin, Suggestion

@plugin.hook("library_substitution")
def suggest(context):
    if context.call == "requests.get":
        return Suggestion(
            summary="consider `reqwest` (blocking client)",
            detail='add `reqwest = { version = "*", features = ["blocking"] }`',
            confidence="curated",
        )
    return None
```

The SDK owns reading stdin, calling the decorated function, writing stdout,
and catching exceptions so a plugin bug degrades to "no suggestion" rather
than crashing the run.

## Compiled-language plugins

There's no separate API for this — a Rust (or Go, or anything else)
executable that reads the same JSON request from stdin and writes the same
JSON response to stdout on exit is a fully valid plugin. A `code-convert-helper-plugin`
Rust crate providing equivalent ergonomics to the Python SDK (a trait +
`main()` boilerplate) is worth publishing once the protocol stabilizes, but
isn't required — the contract itself is the only requirement.

## Built-in plugin: crate substitution

Ships with a curated table (ordinary editable tool config, e.g.
`crates_map.toml`) mapping well-known Python stdlib/library calls to
established Rust crates. The plugin:

- Looks up the call against the curated table first (`confidence:
  "curated"`).
- Never silently substitutes — always returns a suggestion for the host to
  mark at the call site, per the "never guess silently" core principle.
- The table is user-extensible (it's tool config, not conversion-rule
  config, so this is fine per the project's config policy) — a project can
  add its own house-preferred mappings without forking the plugin.

## Built-in plugin: docs conversion

Recognizes Sphinx-style, Google-style, and NumPy-style docstrings attached
to IR nodes, parses out structured fields (parameters, return value,
raised exceptions, examples), and emits them as a rustdoc-shaped `///`
comment block in stage 5 — including a `# Arguments` / `# Returns` /
`# Errors` layout that mirrors what a hand-written Rust doc comment would
look like, rather than a literal line-by-line translation of the
docstring's original formatting.

## Milestone 2 note: ownership is not a plugin hook

The `#!` ownership directive system (`directives/parser.py`,
`ownership/resolver.py`) is deliberately **core**, not a plugin hook,
even though it's a new "judgment call" category like the ones above.
Per `ROADMAP.md`: directive parsing and ownership resolution stay core
and are never pluggable, since they govern language-level correctness
(what compiles) rather than ecosystem taste (which crate to suggest).
Only the *stdlib/crate substitution* side of Milestone 2 (porting the
existing `crate_substitution` plugin, milestone 6) remains suggest-only
and plugin-based.

## Milestone 3 note: clippy-cleanliness is not a plugin hook either

Same reasoning as ownership: whether generated Rust triggers a clippy
lint (`needless_return`, unnecessary parens, `explicit_iter_loop`,
`assign_op_pattern`, etc.) is a property of the core renderer
(`codegen/rust_writer.py`), not ecosystem taste -- there's exactly one
correct, idiomatic rendering for e.g. "this is a function's tail
return", and it doesn't vary by project or house style the way a crate
substitution preference might.
