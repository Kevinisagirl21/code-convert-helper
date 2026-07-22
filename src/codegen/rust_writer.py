"""Stage 5: generate Rust source text from the IR.

Deliberately not built on ``syn``/``quote``-style AST-to-AST generation --
those normalize away comments and exact formatting. This is a small,
explicit string-building pretty-printer instead, so comment placement and
ambiguity markers land exactly where they should.

Milestone 2 adds ownership-aware rendering: a parameter, return type, or
``let`` binding's resolved :class:`~ir.schema.OwnershipDecision` (from an
explicit ``#!`` directive, or from usage-based inference) now controls
whether it renders as ``&T``, ``&mut T``, or a plain owned ``T`` -- never
silently defaulted the way every parameter used to render as pass-by-
value regardless of how it was actually used.
"""

from __future__ import annotations

from ir import schema

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


# ---------------------------------------------------------------------------
# Ownership rendering (Milestone 2)
# ---------------------------------------------------------------------------


def _ownership_prefix(decision: "schema.OwnershipDecision | None") -> str:
    """The Rust ``&``/``&mut``/(nothing) prefix implied by an ownership decision.

    ``"owner"`` and ``"move"`` both render as a plain owned value -- the
    distinction between them is about *how* the value got here (freshly
    bound vs. transferred from elsewhere), not about the type syntax.
    An unrecognized directive value (see
    ``ownership.resolver.resolve_ownership``) falls back to owned rather
    than emitting a nonsense prefix; the conflict itself is still
    reported via :func:`_ownership_comment` and the ownership log, never
    silently dropped.
    """

    if decision is None:
        return ""
    if decision.value == "refer":
        return "&"
    if decision.value == "refer_mut":
        return "&mut "
    return ""


def _ownership_comment(decision: "schema.OwnershipDecision | None", level: int) -> str:
    """A reference comment for an ownership decision that wasn't a clean,
    agreeing directive -- i.e. it was inferred, or a directive conflicted
    with what inference would have chosen. A clean directive with no
    conflict needs no comment; it's exactly what the user asked for.
    """

    if decision is None:
        return ""
    pad = _INDENT * level
    if decision.conflict is not None:
        return f"{pad}// OWNERSHIP CONFLICT: {decision.conflict}\n"
    if decision.source == "inferred":
        reason = "; ".join(decision.evidence) if decision.evidence else "no directive present"
        return f"{pad}// OWNERSHIP (inferred '{decision.value}'): {reason}\n"
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
        ownership_comment = _ownership_comment(stmt.ownership, level)
        own_prefix = _ownership_prefix(stmt.ownership)
        kw = "let mut" if stmt.mutable else "let"
        ty = f"{own_prefix}{_type_slot_to_rust(stmt.type)}"
        value_text = render_expr(stmt.value)
        if own_prefix:
            value_text = f"{own_prefix}{value_text}"
        line = f"{pad}{kw} {stmt.target}: {ty} = {value_text};{_render_trailing(stmt.comments)}"
        return f"{leading}{ownership_comment}{hole_comment}{line}"

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
    ownership_comments = ""
    for p in func.params:
        hole_comments += _hole_comment(p.type, level)
        ownership_comments += _ownership_comment(p.ownership, level)
        own_prefix = _ownership_prefix(p.ownership)
        param_parts.append(f"{p.name}: {own_prefix}{_type_slot_to_rust(p.type)}")
    params_text = ", ".join(param_parts)

    return_hole = _hole_comment(func.return_type, level)
    return_ownership_comment = _ownership_comment(func.return_ownership, level)
    return_own_prefix = _ownership_prefix(func.return_ownership)
    return_text = f"{return_own_prefix}{_type_slot_to_rust(func.return_type)}"
    arrow = f" -> {return_text}" if _type_slot_to_rust(func.return_type) != "()" else ""

    body_text = "\n".join(render_stmt(s, level + 1) for s in func.body) or f"{_INDENT * (level + 1)}// (empty)"

    ambiguity_comment = ""
    if func.ambiguity is not None:
        ambiguity_comment = f"{pad}// AMBIGUOUS[{func.ambiguity.category}]: {func.ambiguity.rationale}\n"

    return (
        f"{leading}{ambiguity_comment}{ownership_comments}{hole_comments}"
        f"{return_ownership_comment}{return_hole}"
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
    """Render a full :class:`~ir.schema.ModuleNode` to Rust source text."""

    parts: list[str] = [
        "// Generated by code-convert-helper -- review all AMBIGUOUS/TYPE HOLE/OWNERSHIP/UNSUPPORTED markers.",
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
