"""Local type inference for py2rust's v1 core subset.

This module never guesses a concrete type it isn't confident about. Where
inference can't resolve a type, it returns a :class:`~py2rust.ir.schema.TypeHole`
carrying whatever partial evidence was found -- that evidence is what shows
up as a reference comment above the hole in generated Rust.
"""

from __future__ import annotations

import itertools

import libcst as cst

from ir import schema

_hole_counter = itertools.count(1)


def _next_hole_id() -> str:
    return f"hole_{next(_hole_counter):04d}"


def reset_hole_counter() -> None:
    """Reset the hole ID counter. Mainly useful for deterministic tests."""

    global _hole_counter
    _hole_counter = itertools.count(1)


def new_hole(known_info: list[str] | None = None) -> schema.TypeHole:
    """Public constructor for a fresh :class:`~py2rust.ir.schema.TypeHole`.

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
    * Returns disagree, or any can't be resolved -> a :class:`~py2rust.ir.schema.TypeHole`
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
