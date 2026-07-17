# pyrite -- full source dump

Every source file in the prototype, concatenated for project-knowledge
searchability. See `HANDOFF.md` for current status and open items, and
`PROJECT_OVERVIEW.md` / `ARCHITECTURE.md` / `PLUGIN_API.md` for the design.

## `pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "pyrite"
version = "0.1.0"
description = "A Python-to-Rust conversion assistant that preserves comments and marks every judgment call it makes."
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
dependencies = [
    "libcst>=1.1.0",
    "typer>=0.12.0",
    "rich>=13.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "sphinx>=7.0.0",
    "sphinx-autodoc-typehints>=2.0.0",
    "furo>=2024.1.29",
]

[project.scripts]
pyrite = "pyrite.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["src/pyrite"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

## `README.md`

```toml
# pyrite

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
pyrite preflight my_module.py

# Full conversion: writes output/my_module.rs, output/ir/my_module.pyrir.json,
# and output/ambiguities.md
pyrite convert my_module.py --out output

# With the optional output-length split suggestion enabled
pyrite convert my_module.py --out output --split-check

# Inspect a previously generated (locked, read-only) IR file
pyrite inspect-ir output/ir/my_module.pyrir.json
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
src/pyrite/
    preflight/       stage 0: syntax, scope, out-of-scope-construct scan
    ir/               the IR schema, CST -> IR builder, and (de)serialization
    typing_inference/ literal- and hint-based type inference, type holes
    ambiguity/        ambiguity markers (collection types, class shape, ...)
    codegen/          IR -> Rust text rendering
    plugins/          the subprocess plugin protocol + built-in plugins
    report/           run summary, ambiguities.md, the split-length check
    pipeline.py       wires every stage together
    cli.py            the `pyrite` command-line interface
docs/                 Sphinx documentation (see below)
tests/                pytest test suite
examples/             a sample Python file to try the tool on
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

## Extending this prototype

The design docs (`PROJECT_OVERVIEW.md`, `ARCHITECTURE.md`, `PLUGIN_API.md`)
describe several features this prototype leaves room for but doesn't fully
implement yet:

- Multi-file projects and the `_imports/` IR directory layout for
  converted dependencies (this prototype handles one file at a time).
- The docstring-to-rustdoc plugin (`pyrite/plugins/docs_conversion.py` is
  a documented stub explaining what's needed).
- A daemon-mode plugin protocol (today's subprocess-per-call model in
  `pyrite/plugins/protocol.py` is deliberately the simple starting point).
- IR schema versioning/upgrade passes for a v2 that adds decorators,
  generators, or `async`.
```

## `src/pyrite/__init__.py`

```python
"""pyrite: a Python-to-Rust conversion assistant (v1 core-subset prototype)."""

__version__ = "0.1.0"
```

## `src/pyrite/ambiguity/__init__.py`

```python

```

## `src/pyrite/ambiguity/resolver.py`

```python
"""Stage 4: ambiguity marking.

Where more than one reasonable Rust translation exists for a Python
pattern, this module records the choice as an :class:`~pyrite.ir.schema.Ambiguity`
attached to the relevant IR node -- never a silent pick. In this
prototype only one option is actually implemented for each category, but
the ``alternatives`` list documents what a future revision would add, and
nothing about the shape of the marker needs to change when that happens.

No part of this module reads from an external config file: which
*translation* gets chosen is never configurable, by design (see
``PROJECT_OVERVIEW.md``, principle 2).
"""

from __future__ import annotations

from pyrite.ir import schema


def mark_collection_type(concrete: schema.ConcreteType) -> schema.Ambiguity | None:
    """Attach an ambiguity marker to a collection type, if applicable.

    Returns ``None`` for non-collection concrete types (nothing to mark).
    """

    if concrete.value.startswith("Vec<"):
        return schema.Ambiguity(
            category="collection-type",
            chosen=concrete.value,
            alternatives=["a fixed-size array", "a borrowed slice (&[T])"],
            rationale=(
                "Vec<T> is the safe default for a Python list; switch to an "
                "array or slice if the size is fixed or ownership isn't needed."
            ),
        )
    if concrete.value.startswith("HashMap<"):
        return schema.Ambiguity(
            category="collection-type",
            chosen=concrete.value,
            alternatives=["BTreeMap<K, V> (if insertion/sort order matters)"],
            rationale=(
                "HashMap is the safe default for a Python dict; switch to "
                "BTreeMap if you rely on key ordering."
            ),
        )
    return None


def mark_class_shape(class_name: str) -> schema.Ambiguity:
    """Ambiguity marker for the struct-vs-trait-object decision on a class.

    v1 always emits a plain struct + impl block; the trait-object
    alternative is recorded so a human (or a future revision that can see
    polymorphic usage across the file) can reconsider it.
    """

    return schema.Ambiguity(
        category="class-shape",
        chosen="struct + impl",
        alternatives=["a trait + trait object (dyn Trait), if used polymorphically"],
        rationale=(
            f"'{class_name}' was translated as a plain struct; reconsider a "
            "trait object if it's used polymorphically elsewhere in the project."
        ),
    )


def mark_raise(message_hint: str) -> schema.Ambiguity:
    """Ambiguity marker for a ``raise`` statement.

    Rust has no exceptions, so this is always marked -- ``panic!`` is a
    safe, obvious-to-grep default, never a silent stand-in for proper
    ``Result``-based error handling.
    """

    return schema.Ambiguity(
        category="error-handling",
        chosen="panic!(...)",
        alternatives=["returning Result<T, E> and propagating with '?'"],
        rationale=(
            f"Python 'raise {message_hint}' was translated as a panic; "
            "consider a Result-based rewrite for recoverable errors."
        ),
    )


def mark_for_loop(iter_kind: str) -> schema.Ambiguity | None:
    """Ambiguity marker for how a ``for`` loop's iterable was interpreted."""

    if iter_kind == "sequence":
        return schema.Ambiguity(
            category="iteration-style",
            chosen=".iter()",
            alternatives=[".into_iter() (if ownership of elements should move)"],
            rationale=(
                "Iterating by shared reference is the safe default; switch "
                "to into_iter() if the loop body needs to own each element."
            ),
        )
    return None
```

## `src/pyrite/cli.py`

```python
"""The ``pyrite`` command-line interface.

Built with `typer <https://typer.tiangolo.com/>`_ -- arguments and options
are driven by type hints, which fits a project whose whole subject is
type inference. Run ``pyrite --help`` for the full command list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from pyrite import pipeline
from pyrite.ir import storage
from pyrite.preflight import checks
from pyrite.report.split_check import SplitCheckConfig

app = typer.Typer(
    name="pyrite",
    help="Convert a Python file's core-subset code to Rust, preserving comments.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def preflight(file: Path = typer.Argument(..., exists=True, help="Python file to check.")) -> None:
    """Run stage-0 preflight checks only, and print the report."""

    source = file.read_text(encoding="utf-8")
    report = checks.run_preflight(source)

    if not report.passed:
        console.print(f"[bold red]FAILED[/bold red] -- {file} has a syntax error:")
        for issue in report.errors():
            console.print(f"  [red]error[/red] {issue.message}")
        raise typer.Exit(code=1)

    console.print(f"[bold green]PASSED[/bold green] -- {file} parses cleanly.")
    if report.issues:
        table = Table(title="Preflight findings")
        table.add_column("Severity")
        table.add_column("Message")
        for issue in report.issues:
            style = {"warning": "yellow", "info": "cyan"}.get(issue.severity, "white")
            table.add_row(f"[{style}]{issue.severity}[/{style}]", issue.message)
        console.print(table)
    else:
        console.print("No warnings or notable findings.")


@app.command()
def convert(
    file: Path = typer.Argument(..., exists=True, help="Python file to convert."),
    out: Path = typer.Option(Path("output"), "--out", "-o", help="Output directory."),
    emit_ir: bool = typer.Option(True, help="Write the locked IR artifact alongside the output."),
    split_check: bool = typer.Option(
        False, "--split-check/--no-split-check", help="Enable the output-length split suggestion."
    ),
    split_ratio: float = typer.Option(1.5, help="Split-check ratio threshold (output/input lines)."),
    split_lines: int = typer.Option(500, help="Split-check absolute line-count threshold."),
) -> None:
    """Convert FILE to Rust, writing output, IR, and an ambiguity report."""

    split_config = SplitCheckConfig(
        ratio_threshold=split_ratio, absolute_line_threshold=split_lines, enabled=split_check
    )
    result = pipeline.convert_file(file, out, emit_ir=emit_ir, split_config=split_config)

    if not result.preflight.passed:
        console.print(f"[bold red]Preflight failed for {file}:[/bold red]")
        for issue in result.preflight.errors():
            console.print(f"  [red]error[/red] {issue.message}")
        raise typer.Exit(code=1)

    rust_path = out / f"{file.stem}.rs"
    console.print(f"[bold green]Converted[/bold green] {file} -> {rust_path}")

    if result.run_summary is not None:
        s = result.run_summary
        console.print(
            f"  functions: {s.functions_converted}  classes: {s.classes_converted}  "
            f"type holes: {len(s.type_holes)}  ambiguities: {len(s.ambiguities)}  "
            f"unsupported: {len(s.unsupported)}"
        )
        console.print(f"  full report: {out / 'ambiguities.md'}")

    if result.split_result is not None and result.split_result.triggered:
        console.print(f"  [yellow]split suggestion:[/yellow] {result.split_result.reason}")

    for issue in result.preflight.warnings():
        console.print(f"  [yellow]warning[/yellow] {issue.message}")


@app.command("inspect-ir")
def inspect_ir(ir_file: Path = typer.Argument(..., exists=True, help="A .pyrir.json IR file.")) -> None:
    """Pretty-print a locked IR file for inspection (read-only, non-destructive)."""

    module = storage.load_module(ir_file)
    console.print(f"[bold]{module.source_file}[/bold]  (schema {module.schema_version})")
    for top in module.body:
        console.print(f"  {top.kind}: {getattr(top, 'name', getattr(top, 'module', ''))}")


@app.command()
def version() -> None:
    """Print the pyrite version."""

    from pyrite import __version__

    console.print(f"pyrite {__version__}")


if __name__ == "__main__":
    app()
```

## `src/pyrite/codegen/__init__.py`

```python

```

## `src/pyrite/codegen/rust_writer.py`

```python
"""Stage 5: generate Rust source text from the IR.

Deliberately not built on ``syn``/``quote``-style AST-to-AST generation --
those normalize away comments and exact formatting. This is a small,
explicit string-building pretty-printer instead, so comment placement and
ambiguity markers land exactly where they should.
"""

from __future__ import annotations

from pyrite.ir import schema

_INDENT = "    "


def _indent(text: str, level: int) -> str:
    pad = _INDENT * level
    return "\n".join(pad + line if line else line for line in text.splitlines())


def render_expr(expr: schema.Expr) -> str:
    if isinstance(expr, schema.ConstantExpr):
        if expr.py_type == "str":
            escaped = str(expr.value).replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}".to_string()'
        if expr.py_type == "None":
            return "None"
        if expr.py_type == "bool":
            return "true" if expr.value else "false"
        return str(expr.value)
    if isinstance(expr, schema.NameExpr):
        return expr.name
    if isinstance(expr, schema.BinOpExpr):
        return f"({render_expr(expr.left)} {expr.op} {render_expr(expr.right)})"
    if isinstance(expr, schema.CompareExpr):
        return f"({render_expr(expr.left)} {expr.op} {render_expr(expr.right)})"
    if isinstance(expr, schema.BoolOpExpr):
        rust_op = "&&" if expr.op == "and" else "||"
        return f" {rust_op} ".join(f"({render_expr(v)})" for v in expr.values)
    if isinstance(expr, schema.UnaryOpExpr):
        rust_op = "!" if expr.op == "not" else "-"
        return f"{rust_op}{render_expr(expr.operand)}"
    if isinstance(expr, schema.CallExpr):
        func_text = render_expr(expr.func)
        args_text = ", ".join(render_expr(a) for a in expr.args)
        if func_text == "print":
            return f'println!("{{}}", {args_text})' if args_text else 'println!()'
        return f"{func_text}({args_text})"
    if isinstance(expr, schema.AttributeExpr):
        return f"{render_expr(expr.value)}.{expr.attr}"
    if isinstance(expr, schema.SubscriptExpr):
        return f"{render_expr(expr.value)}[{render_expr(expr.index)}]"
    if isinstance(expr, schema.ListExpr):
        return "vec![" + ", ".join(render_expr(e) for e in expr.elements) + "]"
    if isinstance(expr, schema.DictExpr):
        pairs = ", ".join(
            f"({render_expr(k)}, {render_expr(v)})" for k, v in zip(expr.keys, expr.values)
        )
        return f"HashMap::from([{pairs}])"
    return f"/* unrenderable expr: {expr!r} */"


def _clean_comment_text(text: str) -> str:
    return text.lstrip("#").strip()


def _render_comments_leading(comments: schema.Comments, level: int) -> str:
    lines = [f"{_INDENT * level}// {_clean_comment_text(c.text)}" for c in comments.leading]
    return "\n".join(lines)


def _render_trailing(comments: schema.Comments) -> str:
    if comments.trailing:
        return "  // " + _clean_comment_text(comments.trailing[0].text)
    return ""


def _type_slot_to_rust(slot: schema.TypeSlot) -> str:
    """Render a type slot as Rust text.

    A hole becomes a made-up but syntactically valid type name (e.g.
    ``TypeHole_hole_0001``) rather than a bare comment -- the file should
    still *parse* as Rust (and fail to compile loudly on an unknown type),
    not fail to parse at all. The human-readable evidence goes in a
    reference comment above the line instead (see :func:`_hole_comment`).
    """

    if isinstance(slot, schema.ConcreteType):
        return slot.value
    return f"TypeHole_{slot.id}"


def _hole_comment(slot: schema.TypeSlot, level: int) -> str:
    if isinstance(slot, schema.TypeHole) and slot.known_info:
        info = "; ".join(slot.known_info)
        return f"{_INDENT * level}// TYPE HOLE {slot.id}: {info}\n"
    if isinstance(slot, schema.TypeHole):
        return f"{_INDENT * level}// TYPE HOLE {slot.id}: no evidence gathered yet\n"
    return ""


def render_stmt(stmt: schema.Stmt, level: int) -> str:
    pad = _INDENT * level
    leading = _render_comments_leading(stmt.comments, level)
    leading = leading + "\n" if leading else ""

    if isinstance(stmt, schema.AssignStmt):
        if stmt.target_kind in ("self_attr", "reassign"):
            # The real type was already established elsewhere (the struct
            # field declaration, or this name's first `let` binding) -- so
            # this is a plain mutation, not a new binding: no `let`, no
            # type, and no hole comment (this statement's own re-inferred
            # type would just be a weaker, redundant guess).
            line = f"{pad}{stmt.target} = {render_expr(stmt.value)};{_render_trailing(stmt.comments)}"
            return f"{leading}{line}"
        hole_comment = _hole_comment(stmt.type, level)
        kw = "let mut" if stmt.mutable else "let"
        ty = _type_slot_to_rust(stmt.type)
        line = f"{pad}{kw} {stmt.target}: {ty} = {render_expr(stmt.value)};{_render_trailing(stmt.comments)}"
        return f"{leading}{hole_comment}{line}"

    if isinstance(stmt, schema.ReturnStmt):
        value_text = f" {render_expr(stmt.value)}" if stmt.value is not None else ""
        return f"{leading}{pad}return{value_text};{_render_trailing(stmt.comments)}"

    if isinstance(stmt, schema.ExprStmt):
        return f"{leading}{pad}{render_expr(stmt.value)};{_render_trailing(stmt.comments)}"

    if isinstance(stmt, schema.PassStmt):
        return f"{leading}{pad}// (pass){_render_trailing(stmt.comments)}"

    if isinstance(stmt, schema.IfStmt):
        body_text = "\n".join(render_stmt(s, level + 1) for s in stmt.body) or f"{_INDENT * (level + 1)}// (empty)"
        out = f"{leading}{pad}if {render_expr(stmt.test)} {{\n{body_text}\n{pad}}}"
        if stmt.orelse:
            else_text = "\n".join(render_stmt(s, level + 1) for s in stmt.orelse)
            out += f" else {{\n{else_text}\n{pad}}}"
        return out

    if isinstance(stmt, schema.WhileStmt):
        body_text = "\n".join(render_stmt(s, level + 1) for s in stmt.body) or f"{_INDENT * (level + 1)}// (empty)"
        return f"{leading}{pad}while {render_expr(stmt.test)} {{\n{body_text}\n{pad}}}"

    if isinstance(stmt, schema.ForStmt):
        body_text = "\n".join(render_stmt(s, level + 1) for s in stmt.body) or f"{_INDENT * (level + 1)}// (empty)"
        if stmt.iter_kind == "range":
            iter_text = _render_range(stmt.iter)
        else:
            iter_text = f"{render_expr(stmt.iter)}.iter()"
        return f"{leading}{pad}for {stmt.target} in {iter_text} {{\n{body_text}\n{pad}}}"

    if isinstance(stmt, schema.RaiseStmt):
        msg = _render_raise_message(stmt.message)
        return f"{leading}{pad}panic!(\"{{}}\", {msg});{_render_trailing(stmt.comments)}"

    if isinstance(stmt, schema.UnsupportedStmt):
        escaped = stmt.source_text.replace("*/", "* /")
        return (
            f"{leading}{pad}// UNSUPPORTED ({stmt.reason}), original Python kept for reference:\n"
            f"{pad}/*\n{pad}{escaped}\n{pad}*/"
        )

    return f"{leading}{pad}// unrenderable statement: {stmt!r}"


def _render_raise_message(message: schema.Expr | None) -> str:
    """Pick a sensible panic!() message from a ``raise`` expression.

    ``raise SomeError("text")`` is far more common than a bare re-raise, so
    unwrap the exception constructor's first argument rather than
    rendering the call itself (which would nest a made-up Rust function
    call inside the panic).
    """

    if message is None:
        return '"error".to_string()'
    if isinstance(message, schema.CallExpr) and message.args:
        return render_expr(message.args[0])
    if isinstance(message, schema.CallExpr):
        return f'"{message.func.name if isinstance(message.func, schema.NameExpr) else "error"}".to_string()'
    return render_expr(message)


def _render_range(iter_expr: schema.Expr) -> str:
    if isinstance(iter_expr, schema.CallExpr) and len(iter_expr.args) == 1:
        return f"0..{render_expr(iter_expr.args[0])}"
    if isinstance(iter_expr, schema.CallExpr) and len(iter_expr.args) == 2:
        return f"{render_expr(iter_expr.args[0])}..{render_expr(iter_expr.args[1])}"
    return f"{render_expr(iter_expr)}"


def render_function(func: schema.FunctionDefNode, level: int = 0, *, is_method: bool = False, self_kind: str = "&self") -> str:
    pad = _INDENT * level
    leading = _render_comments_leading(func.comments, level)
    leading = leading + "\n" if leading else ""

    param_parts = [self_kind] if is_method else []
    hole_comments = ""
    for p in func.params:
        hole_comments += _hole_comment(p.type, level)
        param_parts.append(f"{p.name}: {_type_slot_to_rust(p.type)}")
    params_text = ", ".join(param_parts)

    return_hole = _hole_comment(func.return_type, level)
    return_text = _type_slot_to_rust(func.return_type)
    arrow = f" -> {return_text}" if return_text != "()" else ""

    body_text = "\n".join(render_stmt(s, level + 1) for s in func.body) or f"{_INDENT * (level + 1)}// (empty)"

    ambiguity_comment = ""
    if func.ambiguity is not None:
        ambiguity_comment = f"{pad}// AMBIGUOUS[{func.ambiguity.category}]: {func.ambiguity.rationale}\n"

    return (
        f"{leading}{ambiguity_comment}{hole_comments}{return_hole}"
        f"{pad}fn {func.name}({params_text}){arrow} {{\n{body_text}\n{pad}}}"
    )


def _method_needs_mut_self(method: schema.FunctionDefNode) -> bool:
    def _assigns_self(stmts: list[schema.Stmt]) -> bool:
        for s in stmts:
            if isinstance(s, schema.AssignStmt) and s.target in _self_field_names:
                return True
            if isinstance(s, schema.IfStmt) and (_assigns_self(s.body) or _assigns_self(s.orelse)):
                return True
            if isinstance(s, (schema.WhileStmt, schema.ForStmt)) and _assigns_self(s.body):
                return True
        return False

    # Heuristic: any assignment inside the method body is treated as a
    # potential self-mutation. Precise self.attr detection would require
    # threading attribute-target information through AssignStmt -- a
    # reasonable follow-up once the IR grows an explicit attribute target.
    _self_field_names: set[str] = set()

    def _collect(stmts: list[schema.Stmt]) -> None:
        for s in stmts:
            if isinstance(s, schema.AssignStmt):
                _self_field_names.add(s.target)
            if isinstance(s, schema.IfStmt):
                _collect(s.body)
                _collect(s.orelse)
            if isinstance(s, (schema.WhileStmt, schema.ForStmt)):
                _collect(s.body)

    _collect(method.body)
    return bool(_self_field_names)


def render_class(cls: schema.ClassDefNode) -> str:
    leading = _render_comments_leading(cls.comments, 0)
    leading = leading + "\n" if leading else ""

    ambiguity_comment = ""
    if cls.ambiguity is not None:
        ambiguity_comment = f"// AMBIGUOUS[{cls.ambiguity.category}]: {cls.ambiguity.rationale}\n"

    bases_note = ""
    if cls.unsupported_bases:
        bases_note = (
            f"// NOTE: base class(es) {', '.join(cls.unsupported_bases)} were not "
            "converted (inheritance is out of v1 scope)\n"
        )

    field_lines = []
    field_holes = ""
    for f in cls.fields:
        field_holes += _hole_comment(f.type, 1)
        field_lines.append(f"{_INDENT}pub {f.name}: {_type_slot_to_rust(f.type)},")
    fields_text = "\n".join(field_lines) or f"{_INDENT}// (no fields)"

    struct_text = f"pub struct {cls.name} {{\n{field_holes}{fields_text}\n}}"

    new_params = ", ".join(f"{f.name}: {_type_slot_to_rust(f.type)}" for f in cls.fields)
    new_body = "\n".join(f"{_INDENT * 3}{f.name}," for f in cls.fields) or f"{_INDENT * 3}// (no fields)"
    new_fn = (
        f"{_INDENT}pub fn new({new_params}) -> Self {{\n"
        f"{_INDENT * 2}Self {{\n{new_body}\n{_INDENT * 2}}}\n"
        f"{_INDENT}}}"
    )

    method_texts = [new_fn]
    for m in cls.methods:
        self_kind = "&mut self" if _method_needs_mut_self(m) else "&self"
        method_texts.append(render_function(m, level=1, is_method=True, self_kind=self_kind))

    impl_text = f"impl {cls.name} {{\n" + "\n\n".join(method_texts) + f"\n}}"

    return f"{leading}{ambiguity_comment}{bases_note}{struct_text}\n\n{impl_text}"


def render_import_note(imp: schema.ImportNode) -> str:
    return f"// (Python import '{imp.module}' -- add the equivalent Rust crate manually)"


def render_module(module: schema.ModuleNode) -> str:
    """Render a full :class:`~pyrite.ir.schema.ModuleNode` to Rust source text."""

    parts: list[str] = [
        "// Generated by pyrite -- review all AMBIGUOUS/TYPE HOLE/UNSUPPORTED markers.",
        f"// Source: {module.source_file}",
        "",
    ]
    uses_hashmap = _module_uses_hashmap(module)
    if uses_hashmap:
        parts.append("use std::collections::HashMap;")
        parts.append("")

    for top in module.body:
        if isinstance(top, schema.ImportNode):
            parts.append(render_import_note(top))
        elif isinstance(top, schema.FunctionDefNode):
            parts.append(render_function(top))
        elif isinstance(top, schema.ClassDefNode):
            parts.append(render_class(top))
        elif isinstance(top, schema.UnsupportedStmt):
            parts.append(render_stmt(top, 0))
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _module_uses_hashmap(module: schema.ModuleNode) -> bool:
    text_hint = repr(module)
    return "HashMap" in text_hint
```

## `src/pyrite/ir/__init__.py`

```python

```

## `src/pyrite/ir/builder.py`

```python
"""Stages 1-4: parse Python source, build the IR, mark ambiguities.

This is the front end's core: it walks a ``libcst`` concrete syntax tree
(chosen specifically because it keeps every comment attached to the node
it belongs to -- no heuristic re-association needed) and produces the
:mod:`pyrite.ir.schema` data structures that get serialized to disk.

Only the v1 core subset is understood here. Anything else becomes an
:class:`~pyrite.ir.schema.UnsupportedStmt` carrying the exact original
source text, per the "capture, don't drop" principle in
``ARCHITECTURE.md``.
"""

from __future__ import annotations

import itertools

import libcst as cst
from libcst.metadata import CodeRange, PositionProvider

from pyrite.ambiguity import resolver as ambiguity
from pyrite.ir import schema
from pyrite.typing_inference import infer

_node_counter = itertools.count(1)


def reset_node_counter() -> None:
    """Reset the node ID counter. Mainly useful for deterministic tests."""

    global _node_counter
    _node_counter = itertools.count(1)


def _next_id(prefix: str) -> str:
    return f"{prefix}_{next(_node_counter):04d}"


_BIN_OPS = {
    cst.Add: "+",
    cst.Subtract: "-",
    cst.Multiply: "*",
    cst.Divide: "/",
    cst.Modulo: "%",
}

_COMPARE_OPS = {
    cst.Equal: "==",
    cst.NotEqual: "!=",
    cst.LessThan: "<",
    cst.LessThanEqual: "<=",
    cst.GreaterThan: ">",
    cst.GreaterThanEqual: ">=",
}

_BOOL_OPS = {
    cst.And: "and",
    cst.Or: "or",
}


class IRBuilder:
    """Builds a :class:`~pyrite.ir.schema.ModuleNode` from Python source.

    One builder instance corresponds to one source file. Position lookups
    are resolved once up front via ``libcst``'s metadata system.
    """

    def __init__(self, source: str, source_file: str) -> None:
        self._source_file = source_file
        wrapper = cst.MetadataWrapper(cst.parse_module(source))
        self._positions: dict[cst.CSTNode, CodeRange] = wrapper.resolve(PositionProvider)
        self._module = wrapper.module
        # Populated while building a class's methods, so a `self.x = ...`
        # assignment can reuse the field type already established by the
        # constructor instead of re-deriving a fresh (usually emptier) hole.
        self._current_field_types: dict[str, schema.TypeSlot] = {}

    # -- helpers -----------------------------------------------------

    def _span(self, node: cst.CSTNode) -> schema.SourceSpan:
        pos = self._positions.get(node)
        if pos is None:
            return schema.SourceSpan(self._source_file, 0, 0)
        return schema.SourceSpan(self._source_file, pos.start.line, pos.end.line)

    def _source_text(self, node: cst.CSTNode) -> str:
        return cst.Module([]).code_for_node(node).strip()

    def _leading_comments(self, leading_lines: tuple[cst.EmptyLine, ...]) -> list[schema.Comment]:
        return [
            schema.Comment(text=line.comment.value)
            for line in leading_lines
            if line.comment is not None
        ]

    def _trailing_comment(
        self, trailing_whitespace: cst.TrailingWhitespace | None
    ) -> list[schema.Comment]:
        if trailing_whitespace is not None and trailing_whitespace.comment is not None:
            return [schema.Comment(text=trailing_whitespace.comment.value)]
        return []

    def _simple_stmt_comments(self, node: cst.SimpleStatementLine) -> schema.Comments:
        return schema.Comments(
            leading=self._leading_comments(node.leading_lines),
            trailing=self._trailing_comment(node.trailing_whitespace),
        )

    def _compound_comments(self, node: cst.CSTNode) -> schema.Comments:
        """Leading/header comments for If/While/For (no same-line trailing
        capture for compound statements in v1 -- see ARCHITECTURE.md open
        questions)."""

        leading_lines = getattr(node, "leading_lines", ())
        return schema.Comments(leading=self._leading_comments(leading_lines))

    # -- expressions ---------------------------------------------------

    def build_expr(self, node: cst.BaseExpression) -> schema.Expr:
        if isinstance(node, cst.Integer):
            return schema.ConstantExpr(value=int(node.value), py_type="int")
        if isinstance(node, cst.Float):
            return schema.ConstantExpr(value=float(node.value), py_type="float")
        if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
            return schema.ConstantExpr(value=node.evaluated_value, py_type="str")
        if isinstance(node, cst.Name):
            if node.value == "True":
                return schema.ConstantExpr(value=True, py_type="bool")
            if node.value == "False":
                return schema.ConstantExpr(value=False, py_type="bool")
            if node.value == "None":
                return schema.ConstantExpr(value=None, py_type="None")
            return schema.NameExpr(name=node.value)
        if isinstance(node, cst.BinaryOperation):
            op = _BIN_OPS.get(type(node.operator), "?")
            return schema.BinOpExpr(
                op=op, left=self.build_expr(node.left), right=self.build_expr(node.right)
            )
        if isinstance(node, cst.Comparison):
            comp = node.comparisons[0]
            op = _COMPARE_OPS.get(type(comp.operator), "?")
            return schema.CompareExpr(
                op=op, left=self.build_expr(node.left), right=self.build_expr(comp.comparator)
            )
        if isinstance(node, cst.BooleanOperation):
            op = _BOOL_OPS.get(type(node.operator), "?")
            return schema.BoolOpExpr(
                op=op, values=[self.build_expr(node.left), self.build_expr(node.right)]
            )
        if isinstance(node, cst.UnaryOperation):
            op = "-" if isinstance(node.operator, cst.Minus) else "not"
            return schema.UnaryOpExpr(op=op, operand=self.build_expr(node.expression))
        if isinstance(node, cst.Call):
            return schema.CallExpr(
                func=self.build_expr(node.func),
                args=[self.build_expr(a.value) for a in node.args],
            )
        if isinstance(node, cst.Attribute):
            return schema.AttributeExpr(value=self.build_expr(node.value), attr=node.attr.value)
        if isinstance(node, cst.Subscript):
            index_node = node.slice[0].slice
            index_expr = index_node.value if isinstance(index_node, cst.Index) else index_node
            return schema.SubscriptExpr(
                value=self.build_expr(node.value), index=self.build_expr(index_expr)
            )
        if isinstance(node, cst.List):
            return schema.ListExpr(elements=[self.build_expr(e.value) for e in node.elements])
        if isinstance(node, cst.Dict):
            keys, values = [], []
            for el in node.elements:
                if isinstance(el, cst.DictElement):
                    keys.append(self.build_expr(el.key))
                    values.append(self.build_expr(el.value))
            return schema.DictExpr(keys=keys, values=values)
        # Fall back to a name-like placeholder rather than raising, so one
        # unrecognized expression doesn't take down the whole statement.
        return schema.NameExpr(name=f"/* unrecognized: {self._source_text(node)} */")

    # -- statements ------------------------------------------------------

    def build_stmt(self, stmt: cst.BaseStatement, sibling_body: list[cst.BaseStatement]) -> schema.Stmt:
        if isinstance(stmt, cst.SimpleStatementLine):
            return self._build_simple_stmt_line(stmt, sibling_body)
        if isinstance(stmt, cst.If):
            return schema.IfStmt(
                test=self.build_expr(stmt.test),
                body=self.build_block(stmt.body),
                orelse=self._build_orelse(stmt.orelse),
                comments=self._compound_comments(stmt),
            )
        if isinstance(stmt, cst.While):
            return schema.WhileStmt(
                test=self.build_expr(stmt.test),
                body=self.build_block(stmt.body),
                comments=self._compound_comments(stmt),
            )
        if isinstance(stmt, cst.For):
            return self._build_for(stmt)
        return schema.UnsupportedStmt(
            source_text=self._source_text(stmt),
            reason=f"'{type(stmt).__name__}' is not part of the v1 core subset",
        )

    def _build_orelse(self, orelse: cst.Else | None) -> list[schema.Stmt]:
        if orelse is None:
            return []
        return self.build_block(orelse.body)

    def _build_for(self, stmt: cst.For) -> schema.ForStmt:
        target_name = stmt.target.value if isinstance(stmt.target, cst.Name) else "_"
        iter_expr = stmt.iter
        if (
            isinstance(iter_expr, cst.Call)
            and isinstance(iter_expr.func, cst.Name)
            and iter_expr.func.value == "range"
        ):
            iter_kind = "range"
        else:
            iter_kind = "sequence"
        return schema.ForStmt(
            target=target_name,
            iter=self.build_expr(iter_expr),
            iter_kind=iter_kind,
            body=self.build_block(stmt.body),
            comments=self._compound_comments(stmt),
        )

    def _build_simple_stmt_line(
        self, node: cst.SimpleStatementLine, sibling_body: list[cst.BaseStatement]
    ) -> schema.Stmt:
        comments = self._simple_stmt_comments(node)
        small = node.body[0]
        if isinstance(small, cst.Assign):
            target = small.targets[0].target
            if (
                isinstance(target, cst.Attribute)
                and isinstance(target.value, cst.Name)
                and target.value.value == "self"
            ):
                attr_name = target.attr.value
                inferred = self._current_field_types.get(attr_name) or infer.infer_assignment_type(
                    attr_name, small.value, sibling_body
                )
                return schema.AssignStmt(
                    target=f"self.{attr_name}",
                    value=self.build_expr(small.value),
                    type=inferred,
                    target_kind="self_attr",
                    comments=comments,
                )
            target_name = target.value if isinstance(target, cst.Name) else self._source_text(target)
            inferred = infer.infer_assignment_type(target_name, small.value, sibling_body)
            return schema.AssignStmt(
                target=target_name,
                value=self.build_expr(small.value),
                type=inferred,
                comments=comments,
            )
        if isinstance(small, cst.Return):
            value = self.build_expr(small.value) if small.value is not None else None
            return schema.ReturnStmt(value=value, comments=comments)
        if isinstance(small, cst.Expr):
            return schema.ExprStmt(value=self.build_expr(small.value), comments=comments)
        if isinstance(small, cst.Pass):
            return schema.PassStmt(comments=comments)
        if isinstance(small, cst.Raise):
            message = self.build_expr(small.exc) if small.exc is not None else None
            return schema.RaiseStmt(message=message, comments=comments)
        return schema.UnsupportedStmt(
            source_text=self._source_text(node),
            reason=f"'{type(small).__name__}' is not part of the v1 core subset",
            comments=comments,
        )

    def build_block(self, body: cst.BaseSuite) -> list[schema.Stmt]:
        if not isinstance(body, cst.IndentedBlock):
            return []
        statements = list(body.body)
        return [self.build_stmt(s, statements) for s in statements]

    # -- top level -------------------------------------------------------

    def build_function(self, node: cst.FunctionDef) -> schema.FunctionDefNode:
        params = []
        param_type_lookup: dict[str, schema.TypeSlot] = {}
        for i, p in enumerate(node.params.params):
            if i == 0 and p.name.value == "self":
                # Rust methods take &self / &mut self implicitly; not a
                # regular typed parameter. See codegen for the self-vs-
                # mut-self heuristic.
                continue
            annotated = infer.type_from_annotation(p.annotation)
            if annotated is not None:
                p_type = annotated
            else:
                p_type = infer.new_hole(["no type hint; not yet inferred from call sites"])
            params.append(schema.Param(name=p.name.value, type=p_type))
            param_type_lookup[p.name.value] = p_type

        explicit_return = infer.type_from_annotation(node.returns)
        if explicit_return is not None:
            return_type = explicit_return
        else:
            # No annotation -- infer from the body's `return` statements
            # rather than silently defaulting to `()`, which would produce
            # a signature that doesn't match a body that actually returns
            # a value (a real compile error, not a conservative default).
            return_type = infer.infer_return_type(
                list(node.body.body) if isinstance(node.body, cst.IndentedBlock) else [],
                param_type_lookup,
                self._current_field_types,
            )

        body = self.build_block(node.body)
        apply_mutability(body)

        return schema.FunctionDefNode(
            node_id=_next_id("fn"),
            name=node.name.value,
            params=params,
            return_type=return_type,
            body=body,
            source_span=self._span(node),
            comments=self._compound_comments(node),
        )

    def build_class(self, node: cst.ClassDef) -> schema.ClassDefNode:
        unsupported_bases = [self._source_text(b.value) for b in node.bases]
        fields: list[schema.ClassFieldNode] = []
        methods: list[schema.FunctionDefNode] = []

        body = node.body.body if isinstance(node.body, cst.IndentedBlock) else []

        # First pass: find __init__ (if any) and derive field types from it,
        # so the second pass can let other methods' `self.x = ...` reuse
        # those types instead of re-deriving weaker evidence.
        for member in body:
            if isinstance(member, cst.FunctionDef) and member.name.value == "__init__":
                fields = self._fields_from_init(member)
                break

        self._current_field_types = {f.name: f.type for f in fields}
        try:
            for member in body:
                if isinstance(member, cst.FunctionDef) and member.name.value != "__init__":
                    methods.append(self.build_function(member))
        finally:
            self._current_field_types = {}

        class_ir = schema.ClassDefNode(
            node_id=_next_id("class"),
            name=node.name.value,
            fields=fields,
            methods=methods,
            source_span=self._span(node),
            comments=self._compound_comments(node),
            unsupported_bases=unsupported_bases,
            ambiguity=ambiguity.mark_class_shape(node.name.value),
        )
        return class_ir

    def _fields_from_init(self, init: cst.FunctionDef) -> list[schema.ClassFieldNode]:
        fields: list[schema.ClassFieldNode] = []
        if not isinstance(init.body, cst.IndentedBlock):
            return fields

        # Build a lookup of __init__ parameter -> inferred type, so the very
        # common `self.x = x` passthrough can reuse the parameter's type
        # instead of re-deriving (usually empty) evidence from scratch.
        param_types: dict[str, schema.TypeSlot] = {}
        for p in init.params.params:
            if p.name.value == "self":
                continue
            annotated = infer.type_from_annotation(p.annotation)
            param_types[p.name.value] = annotated or infer.new_hole(
                ["no type hint on constructor parameter"]
            )

        statements = list(init.body.body)
        for stmt in statements:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue
            for small in stmt.body:
                if not isinstance(small, cst.Assign):
                    continue
                target = small.targets[0].target
                if (
                    isinstance(target, cst.Attribute)
                    and isinstance(target.value, cst.Name)
                    and target.value.value == "self"
                ):
                    field_type = self._field_type_with_param_lookup(
                        small.value, param_types, target.attr.value, statements
                    )
                    fields.append(schema.ClassFieldNode(name=target.attr.value, type=field_type))
        return fields

    def _field_type_with_param_lookup(
        self,
        value: cst.BaseExpression,
        param_types: dict[str, schema.TypeSlot],
        field_name: str,
        sibling_body: list[cst.BaseStatement],
    ) -> schema.TypeSlot:
        """Resolve a ``self.x = <value>`` field type, preferring a known
        constructor-parameter type over generic inference where possible.

        Handles the direct passthrough (``self.x = x``) and the common
        "wrap a parameter in a collection literal" case (``self.x = [x]``),
        since both are extremely common in ``__init__`` and a bare literal
        inference pass alone can't see the parameter's type.
        """

        if isinstance(value, cst.Name) and value.value in param_types:
            return param_types[value.value]

        if isinstance(value, cst.List) and value.elements:
            first = value.elements[0].value
            if isinstance(first, cst.Name) and first.value in param_types:
                elem_type = param_types[first.value]
                if isinstance(elem_type, schema.ConcreteType):
                    return schema.ConcreteType(value=f"Vec<{elem_type.value}>")

        return infer.infer_assignment_type(field_name, value, sibling_body)

    def build_module(self) -> schema.ModuleNode:
        body: list[schema.TopLevel] = []
        for stmt in self._module.body:
            if isinstance(stmt, cst.FunctionDef):
                body.append(self.build_function(stmt))
            elif isinstance(stmt, cst.ClassDef):
                body.append(self.build_class(stmt))
            elif isinstance(stmt, cst.SimpleStatementLine):
                body.extend(self._build_top_level_simple(stmt))
            else:
                body.append(
                    schema.UnsupportedStmt(
                        source_text=self._source_text(stmt),
                        reason=f"top-level '{type(stmt).__name__}' is not part of the v1 core subset",
                    )
                )
        return schema.ModuleNode(
            schema_version=schema.SCHEMA_VERSION,
            source_file=self._source_file,
            body=body,
        )

    def _build_top_level_simple(self, node: cst.SimpleStatementLine) -> list[schema.TopLevel]:
        results: list[schema.TopLevel] = []
        for small in node.body:
            if isinstance(small, cst.Import):
                for alias in small.names:
                    module_name = self._source_text(alias.name)
                    as_name = alias.asname.name.value if alias.asname else None
                    results.append(
                        schema.ImportNode(module=module_name, alias=as_name, source_span=self._span(node))
                    )
            else:
                results.append(
                    schema.UnsupportedStmt(
                        source_text=self._source_text(node),
                        reason=f"top-level '{type(small).__name__}' is not part of the v1 core subset",
                    )
                )
        return results


def apply_mutability(body: list[schema.Stmt]) -> None:
    """Turn a name assigned more than once into ``let mut`` + reassignment.

    Without this pass, a loop accumulator like ``total = total + i``
    inside a ``for`` loop would re-emit ``let total: ... = ...`` on every
    textual occurrence, which shadows rather than mutates and silently
    produces the wrong Rust semantics for the classic accumulator pattern.

    This is a flat, order-based heuristic, not real scope analysis: it
    assumes a name reassigned anywhere in the function refers to the same
    binding. That's true for the common cases this prototype targets
    (accumulators, running totals) but could misfire for two
    independently-scoped variables in different ``if``/``else`` branches
    that happen to share a name. A real per-block scope resolver is a
    natural next step (see ``ARCHITECTURE.md``).
    """

    counts: dict[str, int] = {}

    def _count(stmts: list[schema.Stmt]) -> None:
        for s in stmts:
            if isinstance(s, schema.AssignStmt) and s.target_kind == "name":
                counts[s.target] = counts.get(s.target, 0) + 1
            if isinstance(s, schema.IfStmt):
                _count(s.body)
                _count(s.orelse)
            elif isinstance(s, (schema.WhileStmt, schema.ForStmt)):
                _count(s.body)

    _count(body)

    seen: set[str] = set()

    def _mark(stmts: list[schema.Stmt]) -> None:
        for s in stmts:
            if isinstance(s, schema.AssignStmt) and s.target_kind == "name":
                if counts.get(s.target, 0) > 1:
                    s.mutable = True
                if s.target in seen:
                    s.target_kind = "reassign"
                else:
                    seen.add(s.target)
            if isinstance(s, schema.IfStmt):
                _mark(s.body)
                _mark(s.orelse)
            elif isinstance(s, (schema.WhileStmt, schema.ForStmt)):
                _mark(s.body)

    _mark(body)


def build_module_ir(source: str, source_file: str) -> schema.ModuleNode:
    """Convenience entry point: parse ``source`` and build its IR in one call."""

    return IRBuilder(source, source_file).build_module()


def apply_collection_ambiguities(module: schema.ModuleNode) -> None:
    """Walk a built module and attach collection-type ambiguity markers.

    Separate from :meth:`IRBuilder.build_module` so the "mark ambiguities"
    stage stays a distinct, independently testable pipeline step (stage 4
    in ``ARCHITECTURE.md``), even though in this prototype it's cheap
    enough to run as a follow-up walk rather than a full IR-to-IR pass.
    """

    def _mark_type_slot(slot: schema.TypeSlot) -> schema.TypeSlot:
        return slot

    def _walk_stmt(stmt: schema.Stmt) -> None:
        if isinstance(stmt, schema.AssignStmt) and isinstance(stmt.type, schema.ConcreteType):
            marker = ambiguity.mark_collection_type(stmt.type)
            if marker is not None and stmt.comments.trailing == []:
                stmt.comments.trailing.append(
                    schema.Comment(text=f"AMBIGUOUS[{marker.category}]: {marker.rationale}")
                )
        elif isinstance(stmt, schema.RaiseStmt):
            hint = "..."
            marker = ambiguity.mark_raise(hint)
            stmt.comments.leading.append(
                schema.Comment(text=f"AMBIGUOUS[{marker.category}]: {marker.rationale}")
            )
        elif isinstance(stmt, schema.ForStmt):
            marker = ambiguity.mark_for_loop(stmt.iter_kind)
            if marker is not None:
                stmt.comments.leading.append(
                    schema.Comment(text=f"AMBIGUOUS[{marker.category}]: {marker.rationale}")
                )
            for s in stmt.body:
                _walk_stmt(s)
        elif isinstance(stmt, schema.IfStmt):
            for s in stmt.body:
                _walk_stmt(s)
            for s in stmt.orelse:
                _walk_stmt(s)
        elif isinstance(stmt, schema.WhileStmt):
            for s in stmt.body:
                _walk_stmt(s)

    for top in module.body:
        if isinstance(top, schema.FunctionDefNode):
            for s in top.body:
                _walk_stmt(s)
        elif isinstance(top, schema.ClassDefNode):
            for m in top.methods:
                for s in m.body:
                    _walk_stmt(s)
```

## `src/pyrite/ir/schema.py`

```python
"""Intermediate representation (IR) schema.

This module defines the versioned, serializable data structures that make
up pyrite's intermediate representation. The IR sits between the Python
front end (:mod:`pyrite.ir.builder`) and the Rust back end
(:mod:`pyrite.codegen.rust_writer`): it is the artifact that gets written
to disk, locked read-only, and inspected for debugging.

Design notes
------------
* Every node kind is a plain :func:`dataclasses.dataclass` with no
  inheritance between node kinds. This keeps serialization simple
  (:func:`dataclasses.asdict` handles any nested dataclass regardless of
  static type) and keeps each class easy to read in isolation.
* A ``kind`` field on every node acts as a tag so a dict loaded back from
  JSON can be dispatched to the right dataclass constructor
  (see :mod:`pyrite.ir.storage`).
* Type information is never a bare guess. A type slot is either a
  :class:`ConcreteType` (resolved) or a :class:`TypeHole` (explicitly
  unresolved, carrying whatever partial evidence inference collected).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

#: Schema version for this IR shape. A future revision that adds support
#: for e.g. decorators or generators bumps this and writes IR under a new
#: version rather than mutating files written under this one.
SCHEMA_VERSION = "v1_core"


@dataclass
class SourceSpan:
    """A location in the original Python source file."""

    file: str
    start_line: int
    end_line: int


@dataclass
class ConcreteType:
    """A fully resolved type, e.g. produced from a type hint or inference."""

    value: str
    kind: str = "concrete"


@dataclass
class TypeHole:
    """An explicitly unresolved type slot.

    Attributes
    ----------
    id:
        Stable identifier so the same hole can be referenced from more
        than one place (e.g. a parameter and a later usage).
    known_info:
        Human-readable fragments of evidence gathered during inference,
        e.g. ``"compared with '>' against param 'value' (int)"``. These
        are carried all the way to codegen and rendered as a reference
        comment above the hole, instead of being discarded.
    """

    id: str
    known_info: list[str] = field(default_factory=list)
    kind: str = "hole"


#: A type slot is always one or the other -- never a silent default.
TypeSlot = Union[ConcreteType, TypeHole]


@dataclass
class Comment:
    """A single comment, with a confidence score for its attachment."""

    text: str
    confidence: float = 1.0


@dataclass
class Comments:
    """Comments attached to a node: those above it, and same-line trailing."""

    leading: list[Comment] = field(default_factory=list)
    trailing: list[Comment] = field(default_factory=list)


@dataclass
class Ambiguity:
    """Records a judgment call the tool made, so it can be marked visibly.

    Attributes
    ----------
    category:
        A short machine-readable label, e.g. ``"collection-type"``.
    chosen:
        What the tool actually emitted.
    alternatives:
        Other reasonable choices that were not picked -- this list is
        allowed to have a single entry today ("no other option
        implemented yet") without changing the shape of the field, so a
        future revision can add real alternatives without touching the
        schema.
    rationale:
        Short human-readable explanation shown in the generated comment.
    """

    category: str
    chosen: str
    alternatives: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class Param:
    """A function or method parameter."""

    name: str
    type: TypeSlot


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


@dataclass
class ConstantExpr:
    """A literal: int, float, str, bool, or None."""

    value: object
    py_type: str  # "int" | "float" | "str" | "bool" | "None"
    kind: str = "constant"


@dataclass
class NameExpr:
    """A bare name reference, e.g. ``x``."""

    name: str
    kind: str = "name"


@dataclass
class BinOpExpr:
    """A binary operator expression, e.g. ``a + b``."""

    op: str
    left: "Expr"
    right: "Expr"
    kind: str = "binop"


@dataclass
class CompareExpr:
    """A comparison, e.g. ``a < b``. Only single comparisons in v1."""

    op: str
    left: "Expr"
    right: "Expr"
    kind: str = "compare"


@dataclass
class BoolOpExpr:
    """A boolean combination, e.g. ``a and b``."""

    op: str  # "and" | "or"
    values: list["Expr"]
    kind: str = "boolop"


@dataclass
class UnaryOpExpr:
    """A unary operator, e.g. ``not x`` or ``-x``."""

    op: str
    operand: "Expr"
    kind: str = "unaryop"


@dataclass
class CallExpr:
    """A function or method call."""

    func: "Expr"
    args: list["Expr"] = field(default_factory=list)
    kind: str = "call"


@dataclass
class AttributeExpr:
    """Attribute access, e.g. ``self.x`` or ``requests.get``."""

    value: "Expr"
    attr: str
    kind: str = "attribute"


@dataclass
class SubscriptExpr:
    """Indexing, e.g. ``items[0]``."""

    value: "Expr"
    index: "Expr"
    kind: str = "subscript"


@dataclass
class ListExpr:
    """A list literal."""

    elements: list["Expr"] = field(default_factory=list)
    kind: str = "list"


@dataclass
class DictExpr:
    """A dict literal."""

    keys: list["Expr"] = field(default_factory=list)
    values: list["Expr"] = field(default_factory=list)
    kind: str = "dict"


Expr = Union[
    ConstantExpr,
    NameExpr,
    BinOpExpr,
    CompareExpr,
    BoolOpExpr,
    UnaryOpExpr,
    CallExpr,
    AttributeExpr,
    SubscriptExpr,
    ListExpr,
    DictExpr,
]


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass
class AssignStmt:
    """An assignment.

    ``target_kind`` distinguishes:

    * ``"name"`` -- a fresh local binding -> Rust ``let`` (``let mut`` if
      ``mutable`` is set because the name is reassigned later).
    * ``"self_attr"`` -- a mutation of an existing struct field -> plain
      ``self.x = ...;``, no ``let``/type, since the field's type was
      already declared on the struct.
    * ``"reassign"`` -- a later assignment to a name already bound earlier
      in the same function (e.g. a loop accumulator) -> plain
      ``x = ...;``, no ``let``/type, since re-declaring would shadow
      rather than mutate. See
      :func:`pyrite.ir.builder.apply_mutability` for how this gets set.
    """

    target: str
    value: "Expr"
    type: TypeSlot
    mutable: bool = False
    target_kind: str = "name"  # "name" | "self_attr" | "reassign"
    comments: Comments = field(default_factory=Comments)
    kind: str = "assign"


@dataclass
class ReturnStmt:
    value: "Expr | None"
    comments: Comments = field(default_factory=Comments)
    kind: str = "return"


@dataclass
class ExprStmt:
    """A bare expression used as a statement, e.g. a call for side effects."""

    value: "Expr"
    comments: Comments = field(default_factory=Comments)
    kind: str = "expr_stmt"


@dataclass
class PassStmt:
    comments: Comments = field(default_factory=Comments)
    kind: str = "pass"


@dataclass
class IfStmt:
    test: "Expr"
    body: list["Stmt"]
    orelse: list["Stmt"] = field(default_factory=list)
    comments: Comments = field(default_factory=Comments)
    kind: str = "if"


@dataclass
class WhileStmt:
    test: "Expr"
    body: list["Stmt"]
    comments: Comments = field(default_factory=Comments)
    kind: str = "while"


@dataclass
class ForStmt:
    """A ``for target in iter:`` loop.

    ``iter_kind`` records whether the iterable was recognized as a
    ``range(...)`` call (translated to a Rust range expression) or a
    generic sequence (translated to ``.iter()``) -- this itself is an
    ambiguity-adjacent decision worth keeping explicit rather than
    silently picking one.
    """

    target: str
    iter: "Expr"
    iter_kind: str  # "range" | "sequence"
    body: list["Stmt"]
    comments: Comments = field(default_factory=Comments)
    kind: str = "for"


@dataclass
class RaiseStmt:
    """A ``raise`` statement.

    Rust has no exceptions, so this is always ambiguity-marked in codegen
    (see :mod:`pyrite.codegen.rust_writer`) -- the default translation is
    a ``panic!``, clearly marked as a placeholder for a ``Result``-based
    rewrite.
    """

    message: "Expr | None"
    comments: Comments = field(default_factory=Comments)
    kind: str = "raise"


@dataclass
class UnsupportedStmt:
    """An opaque placeholder for a construct v1 doesn't understand yet.

    Carries the exact original source text and location so a future
    revision can find and re-parse just this fragment without touching
    the rest of the already-converted IR.
    """

    source_text: str
    reason: str
    comments: Comments = field(default_factory=Comments)
    kind: str = "unsupported"


Stmt = Union[
    AssignStmt,
    ReturnStmt,
    ExprStmt,
    PassStmt,
    IfStmt,
    WhileStmt,
    ForStmt,
    RaiseStmt,
    UnsupportedStmt,
]


# ---------------------------------------------------------------------------
# Top-level nodes
# ---------------------------------------------------------------------------


@dataclass
class FunctionDefNode:
    node_id: str
    name: str
    params: list[Param]
    return_type: TypeSlot
    body: list[Stmt]
    source_span: SourceSpan
    comments: Comments = field(default_factory=Comments)
    ambiguity: "Ambiguity | None" = None
    kind: str = "function_def"


@dataclass
class ClassFieldNode:
    """A struct field inferred from a ``self.x = ...`` assignment in ``__init__``."""

    name: str
    type: TypeSlot


@dataclass
class ClassDefNode:
    node_id: str
    name: str
    fields: list[ClassFieldNode]
    methods: list[FunctionDefNode]
    source_span: SourceSpan
    comments: Comments = field(default_factory=Comments)
    ambiguity: "Ambiguity | None" = None
    unsupported_bases: list[str] = field(default_factory=list)
    kind: str = "class_def"


@dataclass
class ImportNode:
    """A recorded ``import`` statement.

    Not translated to Rust directly in v1 -- kept so plugins (e.g. the
    crate-substitution plugin) can recognize which module a later
    ``module.call(...)`` belongs to.
    """

    module: str
    alias: "str | None"
    source_span: SourceSpan
    kind: str = "import"


TopLevel = Union[FunctionDefNode, ClassDefNode, ImportNode, UnsupportedStmt]


@dataclass
class ModuleNode:
    """The root IR node for one source file."""

    schema_version: str
    source_file: str
    body: list[TopLevel] = field(default_factory=list)
```

## `src/pyrite/ir/storage.py`

```python
"""Serializing IR to disk, and loading it back.

The IR file is a real artifact, not a throwaway cache: :func:`save_module`
writes it as formatted JSON and then marks it read-only, so nothing --
including this tool's own later stages -- can hand-edit it and break the
invariants the schema depends on. See ``ARCHITECTURE.md`` for the full
rationale and the planned upgrade path for future schema versions.
"""

from __future__ import annotations

import dataclasses
import json
import os
import stat
from pathlib import Path
from typing import Any

from pyrite.ir import schema

# Maps a node's "kind" tag to the dataclass that should be reconstructed
# from it. Every dataclass in schema.py that can appear inside a Stmt/Expr
# union or as a top-level body entry must be registered here.
_EXPR_REGISTRY: dict[str, type] = {
    "constant": schema.ConstantExpr,
    "name": schema.NameExpr,
    "binop": schema.BinOpExpr,
    "compare": schema.CompareExpr,
    "boolop": schema.BoolOpExpr,
    "unaryop": schema.UnaryOpExpr,
    "call": schema.CallExpr,
    "attribute": schema.AttributeExpr,
    "subscript": schema.SubscriptExpr,
    "list": schema.ListExpr,
    "dict": schema.DictExpr,
}

_STMT_REGISTRY: dict[str, type] = {
    "assign": schema.AssignStmt,
    "return": schema.ReturnStmt,
    "expr_stmt": schema.ExprStmt,
    "pass": schema.PassStmt,
    "if": schema.IfStmt,
    "while": schema.WhileStmt,
    "for": schema.ForStmt,
    "raise": schema.RaiseStmt,
    "unsupported": schema.UnsupportedStmt,
}

_TOP_LEVEL_REGISTRY: dict[str, type] = {
    **_STMT_REGISTRY,
    "function_def": schema.FunctionDefNode,
    "class_def": schema.ClassDefNode,
    "import": schema.ImportNode,
}

# Field names whose values are themselves expressions (single or list) or
# statement lists, and therefore need tagged-union reconstruction rather
# than being treated as plain data.
_EXPR_FIELDS = {"value", "left", "right", "operand", "func", "test", "index", "iter", "message"}
_EXPR_LIST_FIELDS = {"values", "args", "elements", "keys"}
_STMT_LIST_FIELDS = {"body", "orelse"}


def module_to_dict(module: schema.ModuleNode) -> dict[str, Any]:
    """Convert a :class:`~pyrite.ir.schema.ModuleNode` to a plain JSON-safe dict.

    ``dataclasses.asdict`` recurses into nested dataclasses based on their
    *actual* runtime type, so this works uniformly across the tagged
    unions defined in :mod:`pyrite.ir.schema` without needing a per-class
    ``to_dict`` method.
    """

    return dataclasses.asdict(module)


def _reconstruct_expr(data: dict[str, Any] | None) -> Any:
    if data is None:
        return None
    kind = data["kind"]
    cls = _EXPR_REGISTRY.get(kind)
    if cls is None:
        raise ValueError(f"unknown expression kind: {kind!r}")
    return _reconstruct_node(cls, data)


def _reconstruct_stmt(data: dict[str, Any]) -> Any:
    kind = data["kind"]
    cls = _STMT_REGISTRY.get(kind)
    if cls is None:
        raise ValueError(f"unknown statement kind: {kind!r}")
    return _reconstruct_node(cls, data)


def _reconstruct_type_slot(data: dict[str, Any]) -> schema.TypeSlot:
    if data["kind"] == "concrete":
        return schema.ConcreteType(value=data["value"])
    return schema.TypeHole(id=data["id"], known_info=list(data.get("known_info", [])))


def _reconstruct_node(cls: type, data: dict[str, Any]) -> Any:
    """Rebuild one dataclass instance, recursing into known nested shapes."""

    kwargs: dict[str, Any] = {}
    field_names = {f.name for f in dataclasses.fields(cls)}
    for key, value in data.items():
        if key not in field_names:
            continue
        if key == "type" and isinstance(value, dict):
            kwargs[key] = _reconstruct_type_slot(value)
        elif key == "return_type" and isinstance(value, dict):
            kwargs[key] = _reconstruct_type_slot(value)
        elif key in _EXPR_FIELDS and isinstance(value, dict):
            kwargs[key] = _reconstruct_expr(value)
        elif key in _EXPR_LIST_FIELDS and isinstance(value, list):
            kwargs[key] = [_reconstruct_expr(v) for v in value]
        elif key in _STMT_LIST_FIELDS and isinstance(value, list):
            kwargs[key] = [_reconstruct_stmt(v) for v in value]
        elif key == "body" and cls in (schema.FunctionDefNode,) and isinstance(value, list):
            kwargs[key] = [_reconstruct_stmt(v) for v in value]
        elif key == "comments" and isinstance(value, dict):
            kwargs[key] = schema.Comments(
                leading=[schema.Comment(**c) for c in value.get("leading", [])],
                trailing=[schema.Comment(**c) for c in value.get("trailing", [])],
            )
        elif key == "source_span" and isinstance(value, dict):
            kwargs[key] = schema.SourceSpan(**value)
        elif key == "ambiguity" and isinstance(value, dict):
            kwargs[key] = schema.Ambiguity(**value)
        elif key == "params" and isinstance(value, list):
            kwargs[key] = [
                schema.Param(name=p["name"], type=_reconstruct_type_slot(p["type"]))
                for p in value
            ]
        elif key == "fields" and isinstance(value, list) and cls is schema.ClassDefNode:
            kwargs[key] = [
                schema.ClassFieldNode(name=f["name"], type=_reconstruct_type_slot(f["type"]))
                for f in value
            ]
        elif key == "methods" and isinstance(value, list):
            kwargs[key] = [_reconstruct_node(schema.FunctionDefNode, m) for m in value]
        else:
            kwargs[key] = value
    return cls(**kwargs)


def module_from_dict(data: dict[str, Any]) -> schema.ModuleNode:
    """Reconstruct a :class:`~pyrite.ir.schema.ModuleNode` from its dict form."""

    body = []
    for entry in data.get("body", []):
        cls = _TOP_LEVEL_REGISTRY.get(entry["kind"])
        if cls is None:
            raise ValueError(f"unknown top-level kind: {entry['kind']!r}")
        body.append(_reconstruct_node(cls, entry))
    return schema.ModuleNode(
        schema_version=data["schema_version"],
        source_file=data["source_file"],
        body=body,
    )


def save_module(module: schema.ModuleNode, path: Path, *, read_only: bool = True) -> None:
    """Write ``module`` to ``path`` as formatted JSON, then lock it read-only.

    Parameters
    ----------
    read_only:
        Set to ``False`` only for the tool's own internal upgrade passes
        (see ``ARCHITECTURE.md``). User-facing runs should always leave
        this ``True``.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # A prior locked file: unlock briefly so we can overwrite it, this
        # run's output is authoritative for this invocation.
        os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
    path.write_text(json.dumps(module_to_dict(module), indent=2), encoding="utf-8")
    if read_only:
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def load_module(path: Path) -> schema.ModuleNode:
    """Read an IR file back into a :class:`~pyrite.ir.schema.ModuleNode`.

    Read-only permissions on the file are not touched by this function --
    loading an IR file for inspection should never require unlocking it.
    """

    data = json.loads(path.read_text(encoding="utf-8"))
    return module_from_dict(data)
```

## `src/pyrite/pipeline.py`

```python
"""Orchestrates the full pipeline described in ``ARCHITECTURE.md``.

::

    ingest & preflight -> parse & build IR -> mark ambiguities
        -> crate-substitution annotation -> generate Rust
        -> optional split check -> write output + reports

Each stage is independently testable in its own module; this module's
only job is calling them in the right order and packaging up the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyrite.ambiguity import resolver as ambiguity  # noqa: F401  (re-exported for callers)
from pyrite.codegen import rust_writer
from pyrite.ir import builder, storage
from pyrite.ir.schema import ModuleNode
from pyrite.plugins import crate_substitution
from pyrite.preflight import checks
from pyrite.report import split_check, summary


@dataclass
class ConversionResult:
    preflight: checks.PreflightReport
    module: ModuleNode | None
    rust_source: str | None
    run_summary: summary.RunSummary | None
    split_result: split_check.SplitCheckResult | None


def convert_source(
    source: str,
    source_file: str,
    *,
    split_config: split_check.SplitCheckConfig | None = None,
) -> ConversionResult:
    """Run the full pipeline over Python ``source`` already in memory.

    Does not touch the filesystem -- see :func:`convert_file` for the
    CLI-facing version that reads/writes real files. Kept separate so the
    pipeline itself stays trivially unit-testable with plain strings.
    """

    report = checks.run_preflight(source)
    if not report.passed:
        return ConversionResult(report, None, None, None, None)

    module = builder.build_module_ir(source, source_file)
    builder.apply_collection_ambiguities(module)
    crate_substitution.annotate_crate_suggestions(module)

    rust_source = rust_writer.render_module(module)

    result_summary = summary.build_summary(module)

    split_result = None
    if split_config is not None:
        split_result = split_check.check_output_length(source, rust_source, split_config)
        rust_source = split_check.prepend_split_notice(rust_source, split_result)

    return ConversionResult(report, module, rust_source, result_summary, split_result)


def convert_file(
    input_path: Path,
    output_dir: Path,
    *,
    emit_ir: bool = True,
    split_config: split_check.SplitCheckConfig | None = None,
) -> ConversionResult:
    """Run the full pipeline against a file on disk and write all outputs.

    Writes, under ``output_dir``:

    * ``<stem>.rs`` -- the generated Rust.
    * ``ir/<stem>.pyrir.json`` -- the locked IR artifact (if ``emit_ir``).
    * ``ambiguities.md`` -- the run's flagged-item report.
    """

    source = input_path.read_text(encoding="utf-8")
    result = convert_source(source, input_path.name, split_config=split_config)

    if not result.preflight.passed:
        return result

    output_dir.mkdir(parents=True, exist_ok=True)
    assert result.module is not None and result.rust_source is not None

    if emit_ir:
        ir_path = output_dir / "ir" / f"{input_path.stem}.pyrir.json"
        storage.save_module(result.module, ir_path)

    rust_path = output_dir / f"{input_path.stem}.rs"
    rust_path.write_text(result.rust_source, encoding="utf-8")

    if result.run_summary is not None:
        summary.write_ambiguities_report(result.run_summary, output_dir / "ambiguities.md")

    return result
```

## `src/pyrite/plugins/__init__.py`

```python

```

## `src/pyrite/plugins/crate_substitution.py`

```python
"""Built-in plugin: suggest a Rust crate in place of a Python import.

Runs in-process (unlike a third-party plugin, which goes through the
subprocess protocol in :mod:`pyrite.plugins.protocol`) since it ships with
the tool itself. The curated table below is ordinary, editable data --
not a conversion rule -- and every suggestion is surfaced as a marked
comment, never silently substituted, per ``PLUGIN_API.md``.
"""

from __future__ import annotations

from pyrite.ir import schema
from pyrite.plugins.protocol import PluginSuggestion

#: module.attribute -> curated suggestion. Deliberately small; growing
#: this table is safe (it only ever produces a marked suggestion), unlike
#: growing the core conversion logic.
_CURATED_TABLE: dict[str, PluginSuggestion] = {
    "requests.get": PluginSuggestion(
        summary="consider `reqwest` (blocking client)",
        detail='add `reqwest = { version = "*", features = ["blocking"] }`',
        confidence="curated",
    ),
    "requests.post": PluginSuggestion(
        summary="consider `reqwest` (blocking client)",
        detail='add `reqwest = { version = "*", features = ["blocking"] }`',
        confidence="curated",
    ),
    "json.dumps": PluginSuggestion(
        summary="consider `serde_json::to_string`",
        detail='add `serde_json = "*"` and a `#[derive(Serialize)]` type',
        confidence="curated",
    ),
    "json.loads": PluginSuggestion(
        summary="consider `serde_json::from_str`",
        detail='add `serde_json = "*"` and a `#[derive(Deserialize)]` type',
        confidence="curated",
    ),
}


def suggest_crate(module: str, attr: str) -> PluginSuggestion | None:
    """Look up a curated suggestion for ``module.attr``, if one exists."""

    return _CURATED_TABLE.get(f"{module}.{attr}")


def annotate_crate_suggestions(module: schema.ModuleNode) -> None:
    """Walk a built module IR and attach crate-substitution comments.

    Looks for calls shaped like ``imported_module.attr(...)`` at the top
    level of an expression statement or assignment, and -- if the curated
    table has a match -- appends a marked ``SUGGESTED CRATE`` comment. This
    never rewrites the call itself; it only ever adds a visible marker,
    consistent with every other ambiguity-adjacent decision in the tool.
    """

    imported_modules = {
        top.alias or top.module: top.module for top in module.body if isinstance(top, schema.ImportNode)
    }
    if not imported_modules:
        return

    def _check_expr(expr: schema.Expr) -> PluginSuggestion | None:
        if (
            isinstance(expr, schema.CallExpr)
            and isinstance(expr.func, schema.AttributeExpr)
            and isinstance(expr.func.value, schema.NameExpr)
        ):
            local_name = expr.func.value.name
            real_module = imported_modules.get(local_name)
            if real_module is not None:
                return suggest_crate(real_module, expr.func.attr)
        return None

    def _annotate(stmt: schema.Stmt) -> None:
        suggestion = None
        if isinstance(stmt, schema.ExprStmt):
            suggestion = _check_expr(stmt.value)
        elif isinstance(stmt, schema.AssignStmt):
            suggestion = _check_expr(stmt.value)
        if suggestion is not None:
            stmt.comments.leading.append(
                schema.Comment(
                    text=f"SUGGESTED CRATE: {suggestion.summary} ({suggestion.detail})"
                )
            )
        if isinstance(stmt, schema.IfStmt):
            for s in stmt.body + stmt.orelse:
                _annotate(s)
        elif isinstance(stmt, (schema.WhileStmt, schema.ForStmt)):
            for s in stmt.body:
                _annotate(s)

    for top in module.body:
        if isinstance(top, schema.FunctionDefNode):
            for s in top.body:
                _annotate(s)
        elif isinstance(top, schema.ClassDefNode):
            for m in top.methods:
                for s in m.body:
                    _annotate(s)
```

## `src/pyrite/plugins/docs_conversion.py`

```python
"""Built-in plugin: docstring -> rustdoc conversion. **Not yet implemented.**

This is deliberately left as a documented stub rather than a half-working
guess. ``PLUGIN_API.md`` specifies the target behavior: recognize Sphinx,
Google-style, and NumPy-style docstrings attached to IR nodes and emit an
idiomatic rustdoc (``///``) comment block with ``# Arguments`` /
``# Returns`` / ``# Errors`` sections.

What's needed before this can be implemented for real:

1. The IR schema needs a dedicated docstring node (today a docstring is
   just the first statement of a function body, an ordinary
   ``ExprStmt`` wrapping a string constant -- it isn't structurally
   distinguished from any other expression statement).
2. A small parser per docstring style (Sphinx's ``:param:``/``:returns:``,
   Google's ``Args:``/``Returns:``, NumPy's underlined section headers).

Left unimplemented here rather than shipping a partial parser that would
silently mishandle two of the three styles.
"""

from __future__ import annotations


def convert_docstring(_docstring_text: str, _style: str = "auto") -> str:
    raise NotImplementedError(
        "docstring-to-rustdoc conversion is planned (see PLUGIN_API.md) "
        "but not implemented in this prototype"
    )
```

## `src/pyrite/plugins/protocol.py`

```python
"""The plugin subprocess protocol described in ``PLUGIN_API.md``.

A plugin is any executable that reads one JSON request object from stdin
and writes one JSON response object to stdout before exiting. This module
is the host side of that contract: it never trusts a plugin to behave --
a crash, a timeout, or malformed JSON just means "no suggestion" for that
call, and the overall conversion continues unaffected.

Python is the primary plugin-authoring path (see
:mod:`pyrite.plugins.python_sdk`), but this protocol module itself has no
opinion about what language wrote the plugin -- a compiled executable
implementing the same stdin/stdout contract is just as valid.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any

PROTOCOL_VERSION = "1"

#: How long to wait for a plugin subprocess before giving up on it. A slow
#: or hung plugin should never be able to stall an entire conversion run.
DEFAULT_TIMEOUT_SECONDS = 5.0


@dataclass
class PluginRequest:
    hook: str
    context: dict[str, Any] = field(default_factory=dict)
    protocol_version: str = PROTOCOL_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class PluginSuggestion:
    summary: str
    detail: str = ""
    confidence: str = "heuristic"  # "curated" | "heuristic"


def run_external_plugin(
    executable: str, request: PluginRequest, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> PluginSuggestion | None:
    """Invoke an external plugin executable and return its suggestion, if any.

    Never raises on plugin misbehavior -- a failing plugin simply
    contributes nothing to this call, logged for visibility rather than
    surfaced as a hard error.
    """

    try:
        result = subprocess.run(
            [executable, request.hook],
            input=request.to_json(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[pyrite] plugin '{executable}' failed to run: {exc}")
        return None

    if result.returncode != 0:
        print(f"[pyrite] plugin '{executable}' exited with code {result.returncode}; skipping")
        return None

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"[pyrite] plugin '{executable}' returned malformed JSON; skipping")
        return None

    suggestion = payload.get("suggestion")
    if suggestion is None:
        return None
    try:
        return PluginSuggestion(**suggestion)
    except TypeError:
        print(f"[pyrite] plugin '{executable}' returned an unrecognized suggestion shape; skipping")
        return None
```

## `src/pyrite/preflight/__init__.py`

```python

```

## `src/pyrite/preflight/checks.py`

```python
"""Stage 0: preflight checks.

Before any translation happens, verify the input file is sound enough to
translate at all. A hard failure here means the tool refuses to proceed --
feeding a broken or unsound file into a translator just produces
confidently wrong Rust.

This is intentionally a lightweight, v1-scoped checker rather than a
reimplementation of ``mypy`` or ``pyflakes``: it catches syntax errors,
flags obviously undefined names within a function's own scope, and
records which out-of-scope constructs (generators, decorators, async,
``eval``, etc.) appear, without failing the run over those -- they become
:class:`~pyrite.ir.schema.UnsupportedStmt` nodes later instead of being
silently mistranslated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import libcst as cst

#: Names always available without an explicit assignment or parameter.
_BUILTINS = {
    "True",
    "False",
    "None",
    "print",
    "len",
    "range",
    "str",
    "int",
    "float",
    "bool",
    "list",
    "dict",
    "self",
}

#: CST node types that mark a construct as out of scope for v1. Recording
#: these here (rather than failing) is what lets stage 3 turn them into
#: UnsupportedStmt nodes instead of dropping them.
_OUT_OF_SCOPE_NODE_TYPES: dict[type, str] = {
    cst.FunctionDef: "async_or_decorated",  # only flagged if async/decorated, see below
}


@dataclass
class PreflightIssue:
    """One problem or notable finding from the preflight pass."""

    severity: str  # "error" | "warning" | "info"
    message: str
    line: int | None = None


@dataclass
class PreflightReport:
    passed: bool
    issues: list[PreflightIssue] = field(default_factory=list)

    def errors(self) -> list[PreflightIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def warnings(self) -> list[PreflightIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def _check_syntax(source: str) -> tuple[cst.Module | None, list[PreflightIssue]]:
    try:
        return cst.parse_module(source), []
    except cst.ParserSyntaxError as exc:
        return None, [
            PreflightIssue(
                severity="error",
                message=f"syntax error: {exc.message}",
                line=getattr(exc, "raw_line", None),
            )
        ]


class _OutOfScopeScanner(cst.CSTVisitor):
    """Records constructs v1 doesn't support, without failing the run."""

    def __init__(self) -> None:
        self.findings: list[PreflightIssue] = []

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        if node.asynchronous is not None:
            self.findings.append(
                PreflightIssue("info", f"async function '{node.name.value}' is out of v1 scope")
            )
        if node.decorators:
            self.findings.append(
                PreflightIssue("info", f"decorated function '{node.name.value}' is out of v1 scope")
            )
        for stmt in _walk_statements(node.body):
            if isinstance(stmt, cst.SimpleStatementLine):
                for small in stmt.body:
                    if isinstance(small, (cst.Yield,)):
                        pass  # visited separately below

    def visit_Yield(self, node: cst.Yield) -> None:
        self.findings.append(PreflightIssue("info", "generator ('yield') is out of v1 scope"))

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        bases = [a.value for a in node.bases]
        if bases:
            names = ", ".join(cst.Module([]).code_for_node(b) for b in bases)
            self.findings.append(
                PreflightIssue(
                    "info",
                    f"class '{node.name.value}' has base(s) ({names}); "
                    "inheritance is out of v1 scope, fields/methods still convert",
                )
            )

    def visit_With(self, node: cst.With) -> None:
        self.findings.append(PreflightIssue("info", "'with' statement is out of v1 scope"))


def _walk_statements(body: cst.BaseSuite):
    if isinstance(body, cst.IndentedBlock):
        return body.body
    return []


class _UndefinedNameScanner(cst.CSTVisitor):
    """A shallow, best-effort undefined-name check, scoped per function.

    This is not a full scope resolver (see ``ARCHITECTURE.md`` for why a
    fuller checker is future work) -- it flags names that are read inside
    a function but never assigned, passed as a parameter, defined as a
    module-level function/class, or a recognized builtin.
    """

    def __init__(self, module_level_names: set[str]) -> None:
        self.module_level_names = module_level_names
        self.findings: list[PreflightIssue] = []

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        known = set(self.module_level_names) | _BUILTINS
        for param in node.params.params:
            known.add(param.name.value)

        class _Assigns(cst.CSTVisitor):
            def visit_Assign(self, n: cst.Assign) -> None:
                for t in n.targets:
                    if isinstance(t.target, cst.Name):
                        known.add(t.target.value)

            def visit_For(self, n: cst.For) -> None:
                if isinstance(n.target, cst.Name):
                    known.add(n.target.value)

        node.visit(_Assigns())

        class _Reads(cst.CSTVisitor):
            def visit_Name(self2, n: cst.Name) -> None:
                pass

        # Second pass: find reads not covered by `known`.
        def _scan(n: cst.CSTNode) -> None:
            if isinstance(n, cst.Name) and n.value not in known:
                # Heuristic: skip names that are attribute targets (`self.x`
                # already excluded since `self` itself is known) or keyword
                # argument labels, which aren't real reads.
                self.findings.append(
                    PreflightIssue(
                        "warning",
                        f"'{n.value}' used in '{node.name.value}' but never assigned, "
                        "parameterized, or imported (best-effort check)",
                    )
                )

        class _NameFinder(cst.CSTVisitor):
            def visit_Name(self2, n: cst.Name) -> None:
                _scan(n)

            def visit_Arg(self2, n: cst.Arg) -> bool:
                # Don't descend into keyword= labels as if they were reads.
                if n.keyword is not None:
                    n.value.visit(self2)
                    return False
                return True

            def visit_Attribute(self2, n: cst.Attribute) -> bool:
                # Only the base of an attribute chain is a real name read.
                n.value.visit(self2)
                return False

        node.body.visit(_NameFinder())
        return False  # don't recurse further; nested defs handled independently


def run_preflight(source: str) -> PreflightReport:
    """Run the full stage-0 preflight pass over Python ``source``.

    Returns a :class:`PreflightReport`. ``passed`` is ``False`` only on a
    hard syntax error -- undefined-name warnings and out-of-scope-construct
    findings never fail the run by themselves.
    """

    tree, issues = _check_syntax(source)
    if tree is None:
        return PreflightReport(passed=False, issues=issues)

    module_level_names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, cst.FunctionDef):
            module_level_names.add(stmt.name.value)
        elif isinstance(stmt, cst.ClassDef):
            module_level_names.add(stmt.name.value)
        elif isinstance(stmt, cst.SimpleStatementLine):
            for small in stmt.body:
                if isinstance(small, cst.Import):
                    for name in small.names:
                        module_level_names.add((name.asname or name).name.value)
                elif isinstance(small, cst.ImportFrom):
                    if small.names != cst.ImportStar():
                        for name in small.names:
                            module_level_names.add((name.asname or name).name.value)

    scope_scanner = _UndefinedNameScanner(module_level_names)
    tree.visit(scope_scanner)
    issues.extend(scope_scanner.findings)

    scope_finder = _OutOfScopeScanner()
    tree.visit(scope_finder)
    issues.extend(scope_finder.findings)

    return PreflightReport(passed=True, issues=issues)
```

## `src/pyrite/report/__init__.py`

```python

```

## `src/pyrite/report/split_check.py`

```python
"""Optional stage-6 feature: suggest splitting a file that grew too much.

This is a judgment about file organization, not about how any given line
of code gets translated -- so unlike conversion-ambiguity logic, its
thresholds are ordinary, editable configuration (see
``PROJECT_OVERVIEW.md`` for the distinction).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SplitCheckConfig:
    """Thresholds for the split-suggestion check. Both are checked;
    whichever triggers first wins."""

    ratio_threshold: float = 1.5
    absolute_line_threshold: int = 500
    enabled: bool = True


@dataclass
class SplitCheckResult:
    triggered: bool
    reason: str = ""
    input_lines: int = 0
    output_lines: int = 0


def check_output_length(
    input_source: str, output_source: str, config: SplitCheckConfig
) -> SplitCheckResult:
    """Compare line counts and decide whether to suggest a split."""

    input_lines = input_source.count("\n") + 1
    output_lines = output_source.count("\n") + 1

    if not config.enabled:
        return SplitCheckResult(False, input_lines=input_lines, output_lines=output_lines)

    ratio = output_lines / max(input_lines, 1)
    if ratio > config.ratio_threshold:
        return SplitCheckResult(
            True,
            reason=(
                f"output is {ratio:.1f}x the input's line count "
                f"(threshold {config.ratio_threshold}x)"
            ),
            input_lines=input_lines,
            output_lines=output_lines,
        )
    if output_lines > config.absolute_line_threshold:
        return SplitCheckResult(
            True,
            reason=(
                f"output has {output_lines} lines "
                f"(threshold {config.absolute_line_threshold})"
            ),
            input_lines=input_lines,
            output_lines=output_lines,
        )
    return SplitCheckResult(False, input_lines=input_lines, output_lines=output_lines)


def prepend_split_notice(output_source: str, result: SplitCheckResult) -> str:
    """Add a leading comment suggesting a split, if the check triggered."""

    if not result.triggered:
        return output_source
    notice = (
        "// SPLIT SUGGESTION: this file grew significantly during conversion "
        f"({result.reason}). Consider splitting it into smaller modules.\n"
    )
    return notice + output_source
```

## `src/pyrite/report/summary.py`

```python
"""Stage 6 (part 1): collect a run summary and write ``ambiguities.md``.

Walks the finished IR one more time to gather every marker that ended up
in the generated Rust -- type holes, ambiguities, and unsupported
fragments -- into one scannable report, instead of requiring a grep
through the output file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pyrite.ir import schema


@dataclass
class RunSummary:
    functions_converted: int = 0
    classes_converted: int = 0
    type_holes: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = ["# Conversion summary", ""]
        lines.append(f"- Functions converted: {self.functions_converted}")
        lines.append(f"- Classes converted: {self.classes_converted}")
        lines.append(f"- Type holes remaining: {len(self.type_holes)}")
        lines.append(f"- Ambiguities flagged: {len(self.ambiguities)}")
        lines.append(f"- Unsupported constructs preserved: {len(self.unsupported)}")
        lines.append("")

        if self.type_holes:
            lines.append("## Type holes")
            lines.extend(f"- {h}" for h in self.type_holes)
            lines.append("")
        if self.ambiguities:
            lines.append("## Ambiguities")
            lines.extend(f"- {a}" for a in self.ambiguities)
            lines.append("")
        if self.unsupported:
            lines.append("## Unsupported constructs (captured, not lost)")
            lines.extend(f"- {u}" for u in self.unsupported)
            lines.append("")

        return "\n".join(lines)


def _walk_type_slot(slot: schema.TypeSlot, context: str, summary: RunSummary) -> None:
    if isinstance(slot, schema.TypeHole):
        info = "; ".join(slot.known_info) if slot.known_info else "no evidence gathered"
        summary.type_holes.append(f"{slot.id} ({context}): {info}")


def _walk_stmt(stmt: schema.Stmt, summary: RunSummary) -> None:
    if isinstance(stmt, schema.AssignStmt):
        if stmt.target_kind == "name":
            _walk_type_slot(stmt.type, f"assignment to '{stmt.target}'", summary)
        for c in stmt.comments.leading + stmt.comments.trailing:
            if c.text.startswith("AMBIGUOUS"):
                summary.ambiguities.append(c.text)
    elif isinstance(stmt, schema.ForStmt):
        for c in stmt.comments.leading:
            if c.text.startswith("AMBIGUOUS"):
                summary.ambiguities.append(c.text)
        for s in stmt.body:
            _walk_stmt(s, summary)
    elif isinstance(stmt, schema.RaiseStmt):
        for c in stmt.comments.leading:
            if c.text.startswith("AMBIGUOUS"):
                summary.ambiguities.append(c.text)
    elif isinstance(stmt, schema.IfStmt):
        for s in stmt.body:
            _walk_stmt(s, summary)
        for s in stmt.orelse:
            _walk_stmt(s, summary)
    elif isinstance(stmt, schema.WhileStmt):
        for s in stmt.body:
            _walk_stmt(s, summary)
    elif isinstance(stmt, schema.UnsupportedStmt):
        summary.unsupported.append(f"{stmt.reason}: {stmt.source_text[:60]!r}")


def _walk_function(fn: schema.FunctionDefNode, summary: RunSummary) -> None:
    summary.functions_converted += 1
    for p in fn.params:
        _walk_type_slot(p.type, f"param '{p.name}' of '{fn.name}'", summary)
    _walk_type_slot(fn.return_type, f"return type of '{fn.name}'", summary)
    if fn.ambiguity is not None:
        summary.ambiguities.append(f"{fn.name}: {fn.ambiguity.rationale}")
    for s in fn.body:
        _walk_stmt(s, summary)


def build_summary(module: schema.ModuleNode) -> RunSummary:
    """Walk a finished module IR and produce a :class:`RunSummary`."""

    summary = RunSummary()
    for top in module.body:
        if isinstance(top, schema.FunctionDefNode):
            _walk_function(top, summary)
        elif isinstance(top, schema.ClassDefNode):
            summary.classes_converted += 1
            if top.ambiguity is not None:
                summary.ambiguities.append(f"{top.name}: {top.ambiguity.rationale}")
            for f in top.fields:
                _walk_type_slot(f.type, f"field '{f.name}' of '{top.name}'", summary)
            for m in top.methods:
                _walk_function(m, summary)
        elif isinstance(top, schema.UnsupportedStmt):
            summary.unsupported.append(f"{top.reason}: {top.source_text[:60]!r}")
    return summary


def write_ambiguities_report(summary: RunSummary, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(summary.to_markdown(), encoding="utf-8")
```

## `src/pyrite/typing_inference/__init__.py`

```python

```

## `src/pyrite/typing_inference/infer.py`

```python
"""Local type inference for pyrite's v1 core subset.

This module never guesses a concrete type it isn't confident about. Where
inference can't resolve a type, it returns a :class:`~pyrite.ir.schema.TypeHole`
carrying whatever partial evidence was found -- that evidence is what shows
up as a reference comment above the hole in generated Rust.
"""

from __future__ import annotations

import itertools

import libcst as cst

from pyrite.ir import schema

_hole_counter = itertools.count(1)


def _next_hole_id() -> str:
    return f"hole_{next(_hole_counter):04d}"


def reset_hole_counter() -> None:
    """Reset the hole ID counter. Mainly useful for deterministic tests."""

    global _hole_counter
    _hole_counter = itertools.count(1)


def new_hole(known_info: list[str] | None = None) -> schema.TypeHole:
    """Public constructor for a fresh :class:`~pyrite.ir.schema.TypeHole`.

    Prefer this over reaching for the module-private ID counter directly.
    """

    return schema.TypeHole(id=_next_hole_id(), known_info=list(known_info or []))


#: Maps a Python annotation's source text to a concrete Rust type name.
#: Deliberately small and explicit -- growing this table is safe, silently
#: mis-mapping an annotation is not.
_ANNOTATION_MAP = {
    "int": "i64",
    "float": "f64",
    "str": "String",
    "bool": "bool",
    "None": "()",
}


def type_from_annotation(annotation: cst.Annotation | None) -> schema.TypeSlot | None:
    """Resolve an explicit type hint, if present and recognized.

    Returns ``None`` (not a hole) when there is no annotation at all --
    callers should fall back to literal-based inference before deciding
    a hole is warranted.
    """

    if annotation is None:
        return None
    text = cst.Module([]).code_for_node(annotation.annotation).strip()
    mapped = _ANNOTATION_MAP.get(text)
    if mapped is not None:
        return schema.ConcreteType(value=mapped)
    # An annotation exists but isn't one v1 understands (e.g. a generic or
    # a user-defined class) -- still a hole, but a well-informed one.
    return schema.TypeHole(
        id=_next_hole_id(),
        known_info=[f"had an unsupported type hint: {text!r}"],
    )


def type_from_literal(value: cst.BaseExpression) -> schema.TypeSlot | None:
    """Infer a type directly from a literal assignment's right-hand side.

    Returns ``None`` when the expression isn't a literal v1 recognizes
    (e.g. a call result or a name), so callers can fall back to
    usage-based evidence instead.
    """

    if isinstance(value, cst.Integer):
        return schema.ConcreteType(value="i64")
    if isinstance(value, cst.Float):
        return schema.ConcreteType(value="f64")
    if isinstance(value, cst.SimpleString) or isinstance(value, cst.ConcatenatedString):
        return schema.ConcreteType(value="String")
    if isinstance(value, (cst.Name,)) and value.value in ("True", "False"):
        return schema.ConcreteType(value="bool")
    if isinstance(value, cst.List):
        # Best-effort: infer the element type from the first element only.
        if value.elements:
            first = value.elements[0].value
            elem = type_from_literal(first)
            if isinstance(elem, schema.ConcreteType):
                return schema.ConcreteType(value=f"Vec<{elem.value}>")
            return schema.TypeHole(
                id=_next_hole_id(),
                known_info=["list literal's element isn't a plain literal; element type unknown"],
            )
        return schema.TypeHole(
            id=_next_hole_id(), known_info=["empty list literal; element type unknown"]
        )
    if isinstance(value, cst.Dict):
        if value.elements:
            first = value.elements[0]
            if isinstance(first, cst.DictElement):
                k = type_from_literal(first.key)
                v = type_from_literal(first.value)
                if isinstance(k, schema.ConcreteType) and isinstance(v, schema.ConcreteType):
                    return schema.ConcreteType(value=f"HashMap<{k.value}, {v.value}>")
            return schema.TypeHole(
                id=_next_hole_id(),
                known_info=["dict literal's key/value aren't plain literals; types unknown"],
            )
        return schema.TypeHole(
            id=_next_hole_id(), known_info=["empty dict literal; key/value types unknown"]
        )
    return None


def collect_usage_evidence(name: str, body: list[cst.BaseStatement]) -> list[str]:
    """Scan a function body for how ``name`` is used, as evidence for a hole.

    This is deliberately shallow (a single pass looking at comparisons,
    binary operators, and call arguments) -- good enough to give a human
    a head start, not a full data-flow analysis.
    """

    evidence: list[str] = []

    class _Visitor(cst.CSTVisitor):
        def visit_Comparison(self, node: cst.Comparison) -> None:
            left_is_name = isinstance(node.left, cst.Name) and node.left.value == name
            if left_is_name and node.comparisons:
                op = node.comparisons[0].operator
                op_text = type(op).__name__
                evidence.append(f"compared ({op_text}) against another value")

        def visit_BinaryOperation(self, node: cst.BinaryOperation) -> None:
            for side, other in ((node.left, node.right), (node.right, node.left)):
                if isinstance(side, cst.Name) and side.value == name:
                    other_type = type_from_literal(other)
                    if isinstance(other_type, schema.ConcreteType):
                        evidence.append(
                            f"used with '{type(node.operator).__name__}' "
                            f"against a {other_type.value}"
                        )

        def visit_Call(self, node: cst.Call) -> None:
            for i, arg in enumerate(node.args):
                if isinstance(arg.value, cst.Name) and arg.value.value == name:
                    func_text = cst.Module([]).code_for_node(node.func)
                    evidence.append(f"passed as argument {i} to '{func_text}(...)'")

    for stmt in body:
        stmt.visit(_Visitor())
    return evidence


def infer_assignment_type(
    target_name: str, value: cst.BaseExpression, sibling_body: list[cst.BaseStatement]
) -> schema.TypeSlot:
    """Infer the type of ``target_name = value``.

    Tries a literal-based inference first; if that fails, falls back to a
    type hole enriched with usage evidence gathered from ``sibling_body``
    (the rest of the enclosing function, so later uses can still inform
    an earlier assignment's hole).
    """

    literal_type = type_from_literal(value)
    if literal_type is not None:
        return literal_type
    evidence = collect_usage_evidence(target_name, sibling_body)
    return schema.TypeHole(id=_next_hole_id(), known_info=evidence)


def infer_return_type(
    body: list[cst.BaseStatement],
    param_types: dict[str, schema.TypeSlot],
    field_types: dict[str, schema.TypeSlot] | None = None,
) -> schema.TypeSlot:
    """Infer a function's return type from its ``return`` statements.

    Used only when there's no explicit ``-> T`` annotation. Defaulting a
    function with no annotation to ``()`` regardless of what it actually
    returns is a real bug, not a conservative default -- it produces a
    Rust signature that doesn't match the body and fails to compile
    (``expected (), found i64``). So this walks every ``return`` in the
    body (through ``if``/``while``/``for``) and tries to resolve each
    one's type from a literal, a parameter, or (for methods) a known
    ``self.attr`` field type.

    * No ``return <value>`` anywhere -> ``()`` (a real, intentional unit
      return, not a guess).
    * Every resolvable return agrees on one concrete type -> that type.
    * Returns disagree, or any can't be resolved -> a :class:`~pyrite.ir.schema.TypeHole`
      carrying each return's evidence, so it's marked rather than silently
      wrong in either direction.
    """

    field_types = field_types or {}
    return_exprs: list[cst.BaseExpression | None] = []

    class _ReturnFinder(cst.CSTVisitor):
        def visit_Return(self, node: cst.Return) -> None:
            return_exprs.append(node.value)

        def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
            return False  # don't descend into nested function defs

    for stmt in body:
        stmt.visit(_ReturnFinder())

    value_returns = [e for e in return_exprs if e is not None]
    if not value_returns:
        return schema.ConcreteType(value="()")

    resolved: list[schema.ConcreteType | None] = []
    evidence: list[str] = []
    for expr in value_returns:
        text = cst.Module([]).code_for_node(expr).strip()
        literal = type_from_literal(expr)
        if isinstance(literal, schema.ConcreteType):
            resolved.append(literal)
            continue
        if isinstance(expr, cst.Name) and expr.value in param_types:
            candidate = param_types[expr.value]
            if isinstance(candidate, schema.ConcreteType):
                resolved.append(candidate)
                continue
        if (
            isinstance(expr, cst.Attribute)
            and isinstance(expr.value, cst.Name)
            and expr.value.value == "self"
            and expr.attr.value in field_types
        ):
            candidate = field_types[expr.attr.value]
            if isinstance(candidate, schema.ConcreteType):
                resolved.append(candidate)
                continue
        resolved.append(None)
        evidence.append(f"returns '{text}', type not resolved")

    concrete_values = {r.value for r in resolved if r is not None}
    if len(concrete_values) == 1 and len(evidence) == 0:
        return schema.ConcreteType(value=next(iter(concrete_values)))

    if len(concrete_values) > 1:
        evidence.insert(0, f"disagreeing return types found: {', '.join(sorted(concrete_values))}")
    return new_hole(evidence)
```

## `tests/conftest.py`

```python
"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from pyrite.ir import builder
from pyrite.typing_inference import infer


@pytest.fixture(autouse=True)
def _reset_id_counters():
    """Type-hole and node IDs are module-level counters; reset them before
    every test so assertions on exact IDs (e.g. ``hole_0001``) are
    deterministic regardless of test execution order."""

    infer.reset_hole_counter()
    builder.reset_node_counter()
    yield
```

## `tests/test_codegen.py`

```python
from pyrite.codegen import rust_writer
from pyrite.ir import builder


def _rust(src: str) -> str:
    module = builder.build_module_ir(src, "t.py")
    builder.apply_collection_ambiguities(module)
    return rust_writer.render_module(module)


def test_simple_function_renders_valid_shape():
    out = _rust("def add(a: int, b: int) -> int:\n    return a + b\n")
    assert "fn add(a: i64, b: i64) -> i64 {" in out
    assert "return (a + b);" in out


def test_type_hole_renders_as_identifier_not_bare_comment():
    out = _rust("def f(x):\n    return x\n")
    # Must still be a syntactically plausible type position -- a bare
    # comment there would make the file fail to even parse.
    assert "x: TypeHole_hole_0001" in out
    assert "// TYPE HOLE hole_0001" in out


def test_accumulator_emits_let_mut_once_and_plain_reassignment_after():
    out = _rust("def f(n: int) -> int:\n    t = 0\n    for i in range(n):\n        t = t + i\n    return t\n")
    assert "let mut t: i64 = 0;" in out
    assert "t = (t + i);" in out
    assert out.count("let") == 1  # only the initial binding uses `let`


def test_self_attr_mutation_has_no_let_and_no_type():
    src = (
        "class C:\n"
        "    def __init__(self, x: int):\n"
        "        self.x = x\n"
        "    def bump(self):\n"
        "        self.x = self.x + 1\n"
    )
    out = _rust(src)
    assert "self.x = (self.x + 1);" in out
    assert "let self.x" not in out


def test_class_renders_struct_and_impl_with_ambiguity_marker():
    src = "class C:\n    def __init__(self, x: int):\n        self.x = x\n"
    out = _rust(src)
    assert "// AMBIGUOUS[class-shape]" in out
    assert "pub struct C {" in out
    assert "impl C {" in out
    assert "pub fn new(x: i64) -> Self {" in out


def test_raise_unwraps_exception_message_into_panic():
    out = _rust("def f():\n    raise ValueError('bad')\n")
    assert 'panic!("{}", "bad".to_string());' in out
    assert "// AMBIGUOUS[error-handling]" in out


def test_for_over_range_uses_rust_range_syntax():
    out = _rust("def f(n: int):\n    for i in range(n):\n        print(i)\n")
    assert "for i in 0..n {" in out


def test_for_over_sequence_uses_iter_and_marks_ambiguity():
    out = _rust("def f(items):\n    for x in items:\n        print(x)\n")
    assert "for x in items.iter()" in out
    assert "// AMBIGUOUS[iteration-style]" in out


def test_list_and_dict_literals():
    out = _rust("def f():\n    a = [1, 2]\n    b = {'k': 1}\n")
    assert "vec![1, 2]" in out
    assert "HashMap::from([" in out
    assert "use std::collections::HashMap;" in out


def test_unsupported_construct_is_kept_verbatim_as_a_comment_block():
    out = _rust("def f():\n    with open('x') as fh:\n        pass\n")
    assert "UNSUPPORTED" in out
    assert "with open" in out
```

## `tests/test_infer.py`

```python
import libcst as cst

from pyrite.ir import schema
from pyrite.typing_inference import infer


def _literal(src: str) -> cst.BaseExpression:
    return cst.parse_expression(src)


def test_int_literal():
    assert infer.type_from_literal(_literal("5")) == schema.ConcreteType(value="i64")


def test_float_literal():
    assert infer.type_from_literal(_literal("5.0")) == schema.ConcreteType(value="f64")


def test_str_literal():
    assert infer.type_from_literal(_literal('"hi"')) == schema.ConcreteType(value="String")


def test_bool_literal():
    assert infer.type_from_literal(_literal("True")) == schema.ConcreteType(value="bool")


def test_list_of_ints():
    result = infer.type_from_literal(_literal("[1, 2, 3]"))
    assert result == schema.ConcreteType(value="Vec<i64>")


def test_dict_str_to_int():
    result = infer.type_from_literal(_literal('{"a": 1}'))
    assert result == schema.ConcreteType(value="HashMap<String, i64>")


def test_call_result_is_not_a_literal():
    assert infer.type_from_literal(_literal("foo()")) is None


def test_empty_list_is_a_hole_not_a_guess():
    result = infer.type_from_literal(_literal("[]"))
    assert isinstance(result, schema.TypeHole)
    assert result.known_info


def test_annotation_maps_known_type():
    annotation = cst.Annotation(annotation=cst.Name("int"))
    result = infer.type_from_annotation(annotation)
    assert result == schema.ConcreteType(value="i64")


def test_annotation_missing_is_none_not_a_hole():
    assert infer.type_from_annotation(None) is None


def test_unrecognized_annotation_is_an_informed_hole():
    annotation = cst.Annotation(annotation=cst.Name("SomeCustomType"))
    result = infer.type_from_annotation(annotation)
    assert isinstance(result, schema.TypeHole)
    assert "SomeCustomType" in result.known_info[0]


def test_usage_evidence_collects_binop_context():
    body = cst.parse_module("x = x + 1\n").body
    evidence = infer.collect_usage_evidence("x", body)
    assert any("i64" in e for e in evidence)
```

## `tests/test_ir_builder.py`

```python
from pathlib import Path

from pyrite.ir import builder, schema, storage


def _build(src: str, filename: str = "t.py") -> schema.ModuleNode:
    module = builder.build_module_ir(src, filename)
    builder.apply_collection_ambiguities(module)
    return module


def test_function_with_annotated_params():
    module = _build("def add(a: int, b: int) -> int:\n    return a + b\n")
    fn = module.body[0]
    assert isinstance(fn, schema.FunctionDefNode)
    assert fn.name == "add"
    assert [p.type for p in fn.params] == [
        schema.ConcreteType(value="i64"),
        schema.ConcreteType(value="i64"),
    ]
    assert fn.return_type == schema.ConcreteType(value="i64")


def test_unannotated_param_is_a_hole():
    module = _build("def f(x):\n    return x\n")
    fn = module.body[0]
    assert isinstance(fn.params[0].type, schema.TypeHole)


def test_comment_attaches_to_the_statement_it_describes():
    src = "def f(x: int) -> int:\n    # add one\n    return x + 1\n"
    module = _build(src)
    fn = module.body[0]
    ret_stmt = fn.body[0]
    assert isinstance(ret_stmt, schema.ReturnStmt)
    assert ret_stmt.comments.leading[0].text == "# add one"


def test_trailing_comment_attaches_to_same_line():
    src = "def f():\n    x = 1  # start here\n"
    module = _build(src)
    stmt = module.body[0].body[0]
    assert stmt.comments.trailing[0].text == "# start here"


def test_class_fields_from_init_passthrough():
    src = (
        "class Counter:\n"
        "    def __init__(self, start: int):\n"
        "        self.value = start\n"
    )
    module = _build(src)
    cls = module.body[0]
    assert isinstance(cls, schema.ClassDefNode)
    assert cls.fields == [schema.ClassFieldNode(name="value", type=schema.ConcreteType(value="i64"))]


def test_class_list_field_resolves_param_element_type():
    src = (
        "class Counter:\n"
        "    def __init__(self, start: int):\n"
        "        self.history = [start]\n"
    )
    module = _build(src)
    cls = module.body[0]
    assert cls.fields[0].type == schema.ConcreteType(value="Vec<i64>")


def test_self_is_not_a_regular_parameter():
    src = "class C:\n    def __init__(self, x: int):\n        self.x = x\n\n    def get(self):\n        return self.x\n"
    module = _build(src)
    cls = module.body[0]
    get_method = [m for m in cls.methods if m.name == "get"][0]
    assert get_method.params == []


def test_accumulator_pattern_marks_mutability_and_reassignment():
    src = "def total(n: int) -> int:\n    t = 0\n    for i in range(n):\n        t = t + i\n    return t\n"
    module = _build(src)
    fn = module.body[0]
    first_assign = fn.body[0]
    loop = fn.body[1]
    second_assign = loop.body[0]
    assert isinstance(first_assign, schema.AssignStmt)
    assert first_assign.target_kind == "name"
    assert first_assign.mutable is True
    assert isinstance(second_assign, schema.AssignStmt)
    assert second_assign.target_kind == "reassign"


def test_self_attr_mutation_is_marked_distinctly():
    src = (
        "class C:\n"
        "    def __init__(self, x: int):\n"
        "        self.x = x\n"
        "    def bump(self):\n"
        "        self.x = self.x + 1\n"
    )
    module = _build(src)
    cls = module.body[0]
    bump = [m for m in cls.methods if m.name == "bump"][0]
    assign = bump.body[0]
    assert assign.target_kind == "self_attr"
    assert assign.target == "self.x"


def test_unsupported_construct_captures_original_source():
    src = "def f():\n    with open('x') as fh:\n        pass\n"
    module = _build(src)
    fn = module.body[0]
    stmt = fn.body[0]
    assert isinstance(stmt, schema.UnsupportedStmt)
    assert "with open" in stmt.source_text


def test_ir_round_trips_through_disk(tmp_path: Path):
    module = _build(
        "class C:\n    def __init__(self, x: int):\n        self.x = x\n"
        "    def bump(self):\n        self.x = self.x + 1\n"
    )
    path = tmp_path / "ir" / "t.pyrir.json"
    storage.save_module(module, path)
    loaded = storage.load_module(path)
    assert loaded == module
    # locked read-only, per ARCHITECTURE.md
    assert not (path.stat().st_mode & 0o200)
```

## `tests/test_pipeline.py`

```python
from pathlib import Path

from pyrite import pipeline
from pyrite.report.split_check import SplitCheckConfig, check_output_length


def test_pipeline_end_to_end_writes_all_outputs(tmp_path: Path):
    src_file = tmp_path / "sample.py"
    src_file.write_text("def add(a: int, b: int) -> int:\n    return a + b\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir)

    assert result.preflight.passed
    assert (out_dir / "sample.rs").exists()
    assert (out_dir / "ir" / "sample.pyrir.json").exists()
    assert (out_dir / "ambiguities.md").exists()
    assert "fn add" in (out_dir / "sample.rs").read_text()


def test_pipeline_stops_on_syntax_error(tmp_path: Path):
    src_file = tmp_path / "broken.py"
    src_file.write_text("def f(:\n    pass\n")

    out_dir = tmp_path / "out"
    result = pipeline.convert_file(src_file, out_dir)

    assert not result.preflight.passed
    assert result.rust_source is None
    assert not (out_dir / "broken.rs").exists()


def test_split_check_ratio_trigger():
    result = check_output_length("a\nb\n", "\n".join(["x"] * 10), SplitCheckConfig())
    assert result.triggered
    assert "1.5x" in result.reason or "x" in result.reason


def test_split_check_disabled_never_triggers():
    result = check_output_length("a\n", "\n".join(["x"] * 1000), SplitCheckConfig(enabled=False))
    assert not result.triggered


def test_split_check_absolute_threshold():
    input_src = "\n".join(["a"] * 400)
    output_src = "\n".join(["b"] * 401)  # ratio ~1.0, under absolute default of 500 -> shouldn't trigger yet
    result = check_output_length(input_src, output_src, SplitCheckConfig(absolute_line_threshold=400))
    assert result.triggered
```

## `tests/test_plugins.py`

```python
import json

from pyrite.ir import builder
from pyrite.plugins import crate_substitution
from pyrite.plugins.protocol import PluginRequest, PluginSuggestion, run_external_plugin


def test_curated_suggestion_lookup():
    suggestion = crate_substitution.suggest_crate("requests", "get")
    assert suggestion is not None
    assert suggestion.confidence == "curated"
    assert "reqwest" in suggestion.summary


def test_unknown_call_has_no_suggestion():
    assert crate_substitution.suggest_crate("os", "some_unmapped_call") is None


def test_annotate_crate_suggestions_marks_but_does_not_rewrite():
    src = "import requests\n\ndef f(url):\n    response = requests.get(url)\n    return response\n"
    module = builder.build_module_ir(src, "t.py")
    crate_substitution.annotate_crate_suggestions(module)
    fn = [n for n in module.body if n.kind == "function_def"][0]
    assign = fn.body[0]
    texts = [c.text for c in assign.comments.leading]
    assert any("SUGGESTED CRATE" in t for t in texts)
    # never rewritten -- the call itself is untouched
    assert assign.value.func.attr == "get"


def test_external_plugin_protocol_round_trip(tmp_path):
    plugin_path = tmp_path / "echo_plugin.py"
    plugin_path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "json.load(sys.stdin)  # request is read but this stub ignores its content\n"
        "print(json.dumps({'suggestion': {'summary': 'ok', 'detail': '', 'confidence': 'heuristic'}}))\n"
    )
    plugin_path.chmod(0o755)

    request = PluginRequest(hook="library_substitution", context={"call": "foo.bar"})
    result = run_external_plugin(str(plugin_path), request)

    assert result is not None
    assert result.summary == "ok"
    assert result.confidence == "heuristic"


def test_external_plugin_failure_is_swallowed_not_raised():
    request = PluginRequest(hook="library_substitution", context={})
    result = run_external_plugin("/no/such/executable", request)
    assert result is None
```

## `tests/test_preflight.py`

```python
from pyrite.preflight import checks


def test_valid_source_passes():
    report = checks.run_preflight("def f(x):\n    return x + 1\n")
    assert report.passed
    assert not report.errors()


def test_syntax_error_fails():
    report = checks.run_preflight("def f(:\n    pass\n")
    assert not report.passed
    assert report.errors()


def test_no_false_positive_on_params_and_locals():
    src = (
        "def add(a: int, b: int) -> int:\n"
        "    total = a + b\n"
        "    return total\n"
    )
    report = checks.run_preflight(src)
    assert report.passed
    assert not report.warnings()


def test_flags_genuinely_undefined_name():
    src = "def broken(x):\n    return y + x\n"
    report = checks.run_preflight(src)
    assert report.passed  # a warning, not a hard failure
    messages = [w.message for w in report.warnings()]
    assert any("'y'" in m for m in messages)


def test_flags_out_of_scope_constructs_without_failing():
    src = (
        "async def fetch():\n"
        "    pass\n"
        "\n"
        "class Dog(Animal):\n"
        "    def speak(self):\n"
        "        yield 'woof'\n"
    )
    report = checks.run_preflight(src)
    assert report.passed
    infos = [i.message for i in report.issues if i.severity == "info"]
    assert any("async" in m for m in infos)
    assert any("yield" in m or "generator" in m for m in infos)
    assert any("base" in m for m in infos)


def test_for_loop_target_recognized_as_assignment():
    src = "def f(items):\n    for x in items:\n        print(x)\n"
    report = checks.run_preflight(src)
    assert not report.warnings()
```

## `examples/sample.py`

```python
import requests


def clamp(value: int, lo: int, hi: int) -> int:
    # keep value within [lo, hi]
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def sum_up_to(n: int) -> int:
    total = 0
    for i in range(n):
        total = total + i
    return total


class Counter:
    """A simple counter with a running total."""

    def __init__(self, start: int):
        self.value = start
        self.history = [start]

    def increment(self, amount: int):
        self.value = self.value + amount
        # keep a record of every value we've held
        for h in self.history:
            print(h)

    def report(self):
        if self.value > 100:
            raise ValueError("counter overflowed")
        return self.value


def fetch_data(url):
    response = requests.get(url)
    return response
```
