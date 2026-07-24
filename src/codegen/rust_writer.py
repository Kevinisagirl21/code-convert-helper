"""Stage 5: generate Rust source text from the IR.

Deliberately not built on ``syn``/``quote``-style AST-to-AST generation --
those normalize away comments and exact formatting. This is a small,
explicit string-building pretty-printer instead, so comment placement and
ambiguity markers land exactly where they should.

Milestone 2 added ownership-aware rendering (parameter/return/``let``
prefixes driven by resolved :class:`~ir.schema.OwnershipDecision`).

Milestone 3 adds clippy-clean-by-construction rendering:

* ``clippy::needless_return`` -- a function or method's final ``return
  expr;`` statement renders as a bare tail expression instead.
* Unnecessary parentheses -- binary/comparison/boolean/unary expressions
  are rendered with real operator-precedence awareness, so parens are
  emitted only where Rust's grammar actually requires them, not
  unconditionally around every sub-expression.
* ``clippy::explicit_iter_loop`` -- ``for x in seq.iter()`` becomes
  ``for x in &seq``.
* Unneeded ``.to_string()`` in ``panic!`` -- a literal string message
  panics directly on the literal; a bare name uses an inlined format
  capture; only a genuinely computed expression falls back to
  ``panic!("{}", expr)``.
* ``clippy::uninlined_format_args`` -- ``println!("{}", x)`` for a plain
  variable becomes ``println!("{x}")``.
* ``&mut self`` is only emitted for a method that actually mutates one of
  its own fields (a ``self.attr = ...`` assignment), not for a method
  that merely reassigns an unrelated local variable.
* ``clippy::assign_op_pattern`` -- ``x = x + y`` (an accumulator or
  ``self.attr`` mutation) renders as ``x += y`` wherever the shape is
  unambiguous, rather than a plain reassignment that duplicates the
  target on both sides.
"""

from __future__ import annotations

from ir import schema

_INDENT = "    "


def _indent(text: str, level: int) -> str:
    pad = _INDENT * level
    return "\n".join(pad + line if line else line for line in text.splitlines())


# ---------------------------------------------------------------------------
# Expression rendering with real operator-precedence awareness
# ---------------------------------------------------------------------------

#: Higher number binds tighter. Only categories that actually appear as
#: internal (non-atomic) expression nodes need an entry; anything else
#: (names, literals, calls, attribute/subscript access, list/dict
#: literals) is always atomic and never needs wrapping.
_PREC_OR = 1
_PREC_AND = 2
_PREC_COMPARE = 3
_PREC_ADD_SUB = 4
_PREC_MUL_DIV_MOD = 5
_PREC_UNARY = 6
_PREC_ATOM = 7

#: Operators for which ``a OP (b OP c))`` is *not* the same as
#: ``a OP b OP c`` -- i.e. non-associative on the right. A right-hand
#: child at the *same* precedence still needs parens for these; ``+``
#: and ``*`` don't (regrouping doesn't change the result).
_NON_ASSOC_OPS = {"-", "/", "%"}


def _expr_prec(expr: schema.Expr) -> int:
    if isinstance(expr, schema.BoolOpExpr):
        return _PREC_OR if expr.op == "or" else _PREC_AND
    if isinstance(expr, schema.CompareExpr):
        return _PREC_COMPARE
    if isinstance(expr, schema.BinOpExpr):
        return _PREC_ADD_SUB if expr.op in ("+", "-") else _PREC_MUL_DIV_MOD
    if isinstance(expr, schema.UnaryOpExpr):
        return _PREC_UNARY
    return _PREC_ATOM


def render_expr(expr: schema.Expr, parent_prec: int = 0, is_right_operand: bool = False) -> str:
    """Render an expression, wrapping in parens only where precedence demands it.

    ``parent_prec`` is the binding power of whatever is about to
    concatenate this expression's text (0 at the top of a statement,
    where nothing ever needs wrapping). ``is_right_operand`` additionally
    guards the one case where *equal* precedence still needs parens: the
    right-hand side of a non-associative operator (``-``, ``/``, ``%``).
    """

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
        prec = _expr_prec(expr)
        left = render_expr(expr.left, prec, False)
        right = render_expr(expr.right, prec, True)
        text = f"{left} {expr.op} {right}"
        needs_parens = prec < parent_prec or (
            prec == parent_prec and is_right_operand and expr.op in _NON_ASSOC_OPS
        )
        return f"({text})" if needs_parens else text
    if isinstance(expr, schema.CompareExpr):
        prec = _expr_prec(expr)
        left = render_expr(expr.left, prec, False)
        right = render_expr(expr.right, prec, True)
        text = f"{left} {expr.op} {right}"
        return f"({text})" if prec < parent_prec else text
    if isinstance(expr, schema.BoolOpExpr):
        prec = _expr_prec(expr)
        rust_op = "&&" if expr.op == "and" else "||"
        rendered = [render_expr(v, prec, i > 0) for i, v in enumerate(expr.values)]
        text = f" {rust_op} ".join(rendered)
        return f"({text})" if prec < parent_prec else text
    if isinstance(expr, schema.UnaryOpExpr):
        prec = _expr_prec(expr)
        rust_op = "!" if expr.op == "not" else "-"
        operand = render_expr(expr.operand, prec)
        text = f"{rust_op}{operand}"
        return f"({text})" if prec < parent_prec else text
    if isinstance(expr, schema.CallExpr):
        func_text = render_expr(expr.func)
        if func_text == "print":
            return _render_println(expr.args)
        args_text = ", ".join(render_expr(a) for a in expr.args)
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


def _render_println(args: list[schema.Expr]) -> str:
    """Render a ``print(...)`` call as an inlined-format-args ``println!``.

    A single plain-name argument (the common case) inlines directly as
    ``println!("{name}")`` (``clippy::uninlined_format_args``). Anything
    else -- no args, or a genuinely computed expression that can't be
    spelled as a bare identifier inside a format string -- falls back to
    the positional ``println!("{}", expr)`` form.
    """

    if not args:
        return "println!()"
    if len(args) == 1 and isinstance(args[0], schema.NameExpr):
        return f'println!("{{{args[0].name}}}")'
    args_text = ", ".join(render_expr(a) for a in args)
    return f'println!("{{}}", {args_text})'


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
    """The Rust ``&``/``&mut``/(nothing) prefix implied by an ownership decision."""

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
    with what inference would have chosen."""

    if decision is None:
        return ""
    pad = _INDENT * level
    if decision.conflict is not None:
        return f"{pad}// OWNERSHIP CONFLICT: {decision.conflict}\n"
    if decision.source == "inferred":
        reason = "; ".join(decision.evidence) if decision.evidence else "no directive present"
        return f"{pad}// OWNERSHIP (inferred '{decision.value}'): {reason}\n"
    return ""


# ---------------------------------------------------------------------------
# Statement rendering
# ---------------------------------------------------------------------------


_COMPOUND_ASSIGNABLE_OPS = {"+", "-", "*", "/", "%"}


def _try_render_compound_assign(target: str, value: schema.Expr) -> str | None:
    """Render ``target = target OP rhs;`` as ``target OP= rhs;`` where sound.

    Without this, the very common accumulator/mutation shape (``t = t +
    i``, ``self.value = self.value + amount``) round-trips into Rust as
    a plain reassignment, which trips ``clippy::assign_op_pattern`` (a
    default-on style lint) even though the value is semantically
    identical. Only applies when the binary op's *left* operand renders
    to exactly the same text as the assignment target -- anything else
    (a different variable, a more complex expression) falls back to a
    plain assignment rather than risk misreading the shape.
    """

    if (
        isinstance(value, schema.BinOpExpr)
        and value.op in _COMPOUND_ASSIGNABLE_OPS
        and render_expr(value.left) == target
    ):
        return f"{target} {value.op}= {render_expr(value.right)};"
    return None


def render_stmt(stmt: schema.Stmt, level: int, *, tail: bool = False) -> str:
    """Render one statement.

    ``tail`` marks this statement as being in tail position within its
    enclosing function body -- i.e. it is that body's very last
    top-level statement, and its value (if any) is the function's
    result. A ``tail`` ``ReturnStmt`` renders as a bare expression with
    no ``return`` keyword and no trailing semicolon
    (``clippy::needless_return``), rather than an explicit return.
    """

    pad = _INDENT * level
    leading = _render_comments_leading(stmt.comments, level)
    leading = leading + "\n" if leading else ""

    if isinstance(stmt, schema.AssignStmt):
        if stmt.target_kind in ("self_attr", "reassign"):
            compound = _try_render_compound_assign(stmt.target, stmt.value)
            rhs_line = compound if compound is not None else f"{stmt.target} = {render_expr(stmt.value)};"
            line = f"{pad}{rhs_line}{_render_trailing(stmt.comments)}"
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
        if tail and stmt.value is not None:
            # needless_return: the function's final return is just its
            # tail expression -- no `return` keyword, no semicolon.
            value_text = render_expr(stmt.value)
            return f"{leading}{pad}{value_text}{_render_trailing(stmt.comments)}"
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
            # explicit_iter_loop: borrow the sequence directly (`&seq`)
            # instead of the more verbose, lint-flagged `seq.iter()`.
            iter_text = f"&{render_expr(stmt.iter)}"
        return f"{leading}{pad}for {stmt.target} in {iter_text} {{\n{body_text}\n{pad}}}"

    if isinstance(stmt, schema.RaiseStmt):
        return f"{leading}{pad}{_render_panic_call(stmt.message)};{_render_trailing(stmt.comments)}"

    if isinstance(stmt, schema.UnsupportedStmt):
        escaped = stmt.source_text.replace("*/", "* /")
        return (
            f"{leading}{pad}// UNSUPPORTED ({stmt.reason}), original Python kept for reference:\n"
            f"{pad}/*\n{pad}{escaped}\n{pad}*/"
        )

    return f"{leading}{pad}// unrenderable statement: {stmt!r}"


def _render_panic_call(message: schema.Expr | None) -> str:
    """Render a ``raise`` as a clippy-clean ``panic!(...)`` call.

    Avoids the needless ``.to_string()`` that :func:`render_expr` adds for
    an ordinary string *value* (which does need to own/allocate) -- a
    ``panic!`` message is a format-string literal, not a ``String``
    value, so it never needs that call:

    * ``raise SomeError("literal text")`` -> ``panic!("literal text")``
      (message text substituted directly into the format string, with any
      literal ``{``/``}`` escaped so it isn't mistaken for a format
      capture).
    * ``raise SomeError(some_name)`` -> ``panic!("{some_name}")``
      (inlined format capture, ``clippy::uninlined_format_args``).
    * Anything else (a genuinely computed expression) -> the general
      ``panic!("{}", expr)`` fallback, which still needs the positional
      form since an arbitrary expression can't be inlined into a format
      string.
    * No message at all -> ``panic!("error")``.
    """

    if message is None:
        return 'panic!("error")'

    if isinstance(message, schema.CallExpr):
        inner = message.args[0] if message.args else None
    else:
        inner = message

    if inner is None:
        name = message.func.name if isinstance(message.func, schema.NameExpr) else "error"
        return f'panic!("{name}")'

    if isinstance(inner, schema.ConstantExpr) and inner.py_type == "str":
        escaped = (
            str(inner.value)
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("{", "{{")
            .replace("}", "}}")
        )
        return f'panic!("{escaped}")'

    if isinstance(inner, schema.NameExpr):
        return f'panic!("{{{inner.name}}}")'

    return f'panic!("{{}}", {render_expr(inner)})'


def _render_range(iter_expr: schema.Expr) -> str:
    if isinstance(iter_expr, schema.CallExpr) and len(iter_expr.args) == 1:
        return f"0..{render_expr(iter_expr.args[0])}"
    if isinstance(iter_expr, schema.CallExpr) and len(iter_expr.args) == 2:
        return f"{render_expr(iter_expr.args[0])}..{render_expr(iter_expr.args[1])}"
    return f"{render_expr(iter_expr)}"


def _render_body(body: list[schema.Stmt], level: int, *, tail: bool = False) -> str:
    """Render a function/method body, marking only its final statement
    (if any) as being in tail position."""

    if not body:
        return f"{_INDENT * level}// (empty)"
    lines = [
        render_stmt(s, level, tail=(tail and i == len(body) - 1)) for i, s in enumerate(body)
    ]
    return "\n".join(lines)


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

    # Tail-position return -> bare expression is only sound when the
    # function actually has a non-unit return type; a unit-returning
    # function's final `return;`/no-value case is left as-is.
    body_text = _render_body(func.body, level + 1, tail=(arrow != ""))

    ambiguity_comment = ""
    if func.ambiguity is not None:
        ambiguity_comment = f"{pad}// AMBIGUOUS[{func.ambiguity.category}]: {func.ambiguity.rationale}\n"

    return (
        f"{leading}{ambiguity_comment}{ownership_comments}{hole_comments}"
        f"{return_ownership_comment}{return_hole}"
        f"{pad}fn {func.name}({params_text}){arrow} {{\n{body_text}\n{pad}}}"
    )


def _method_needs_mut_self(method: schema.FunctionDefNode) -> bool:
    """Whether a method needs ``&mut self`` -- i.e. actually mutates one
    of the struct's own fields (a ``self.attr = ...`` assignment).

    Reassigning a plain local variable inside the method body (a loop
    accumulator, a temporary, etc.) is not a mutation of ``self`` and
    must not trigger ``&mut self`` -- only an ``AssignStmt`` whose
    ``target_kind`` is ``"self_attr"`` (see
    :func:`ir.builder.IRBuilder._build_simple_stmt_line`) counts.
    """

    def _assigns_self(stmts: list[schema.Stmt]) -> bool:
        for s in stmts:
            if isinstance(s, schema.AssignStmt) and s.target_kind == "self_attr":
                return True
            if isinstance(s, schema.IfStmt) and (_assigns_self(s.body) or _assigns_self(s.orelse)):
                return True
            if isinstance(s, (schema.WhileStmt, schema.ForStmt)) and _assigns_self(s.body):
                return True
        return False

    return _assigns_self(method.body)


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
