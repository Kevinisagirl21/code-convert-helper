"""Ownership resolution for Milestone 2 (``ROADMAP.md`` #2).

Two independent jobs live here:

1. **Usage-based inference** (:func:`infer_param_ownership`,
   :func:`infer_return_ownership`, :func:`infer_assignment_ownership`) --
   a shallow, best-effort heuristic in the same spirit as
   ``typing_inference.infer.collect_usage_evidence``: good enough to give
   a human a head start, not a full borrow-checker. It deliberately never
   infers ``"refer_mut"`` -- see :func:`infer_param_ownership` for why --
   only an explicit ``#!`` directive is trusted for that value.
2. **Resolution** (:func:`resolve_ownership`) -- combines an optional
   explicit ``#!`` directive with the inferred guess. A directive always
   wins outright; if it disagrees with what inference would have picked,
   that disagreement is recorded on the decision as a ``conflict`` string
   (never silently swallowed) for the caller to turn into a warning, or a
   hard failure under ``--warnings-as-fatal``.
"""

from __future__ import annotations

import libcst as cst

from directives.parser import is_valid_ownership_value
from ir import schema

#: Types that are ``Copy`` in Rust. A reference to one of these is
#: essentially never what idiomatic Rust wants -- passing by value is
#: both correct and the readable default, so inference short-circuits to
#: ``"owner"`` for these regardless of how the parameter is used.
_COPY_PRIMITIVES = {"i64", "f64", "bool", "()"}


def infer_param_ownership(
    name: str,
    body: list[cst.BaseStatement],
    type_hint: "schema.TypeSlot | None" = None,
) -> tuple[str, list[str]]:
    """Best-effort ownership guess for a parameter with no ``#!`` directive.

    v1 deliberately never *infers* ``"refer_mut"``: Python's reassignment
    of a local parameter name carries none of the reference semantics a
    Rust ``&mut`` parameter implies (rebinding a Python name never
    mutates anything the caller can observe), so guessing "the caller
    needs to see this mutation" from a plain ``x = ...`` inside the
    function body would not be a sound inference -- only an explicit
    ``#! refer_mut`` directive is trusted for that value. What *is*
    inferred:

    * A parameter whose resolved type is a Copy primitive (``i64``,
      ``f64``, ``bool``, unit) -> ``"owner"``, regardless of usage.
    * A non-Copy parameter that's returned directly, or passed straight
      into a ``self.attr = `` assignment (the common constructor-
      passthrough shape) -> ``"move"`` (ownership transfers out).
    * Otherwise (only read, compared, or passed along) -> ``"refer"`` --
      avoids an unnecessary clone/move for a read-only use of a non-Copy
      value.
    """

    if isinstance(type_hint, schema.ConcreteType) and type_hint.value in _COPY_PRIMITIVES:
        return "owner", [f"'{type_hint.value}' is a Copy primitive; pass-by-value is idiomatic"]

    state = {"moved": False}
    evidence: list[str] = []

    class _Visitor(cst.CSTVisitor):
        def visit_Assign(self, node: cst.Assign) -> None:
            for t in node.targets:
                target = t.target
                if (
                    isinstance(target, cst.Attribute)
                    and isinstance(target.value, cst.Name)
                    and target.value.value == "self"
                    and isinstance(node.value, cst.Name)
                    and node.value.value == name
                ):
                    state["moved"] = True
                    evidence.append(f"stored into 'self.{target.attr.value}'")

        def visit_Return(self, node: cst.Return) -> None:
            if isinstance(node.value, cst.Name) and node.value.value == name:
                state["moved"] = True
                evidence.append("returned directly from the function")

    for stmt in body:
        stmt.visit(_Visitor())

    if state["moved"]:
        return "move", evidence
    evidence.append(f"'{name}' is only read or passed along; never stored or returned")
    return "refer", evidence


def infer_return_ownership(
    return_expr_texts: list[str], param_ownership: dict[str, str]
) -> tuple[str, list[str]]:
    """Best-effort ownership guess for a function's return type.

    v1 keeps this intentionally simple: returning a value out of a
    function body is translated as a real ownership transfer (``move``)
    by default, since a Rust function returning a borrowed reference to
    a value it just computed needs a lifetime it doesn't have. The one
    exception this heuristic recognizes is a function that returns one
    of its own parameters unchanged -- in that case the return type
    echoes *that parameter's own resolved ownership* rather than always
    defaulting to ``"owner"``: if the parameter itself is a ``"refer"``
    or ``"refer_mut"`` reference, returning it as a plain owned value
    would be a real type mismatch (``-> String`` can't be satisfied by
    handing back a ``&String``), whereas ``-> &String`` for a single
    reference parameter is valid Rust under lifetime elision.
    """

    if not return_expr_texts:
        return "move", ["function has no return value"]

    if len(set(return_expr_texts)) == 1 and return_expr_texts[0] in param_ownership:
        pname = return_expr_texts[0]
        pvalue = param_ownership[pname]
        if pvalue in ("refer", "refer_mut"):
            return pvalue, [
                f"returns parameter '{pname}' unchanged, itself resolved as '{pvalue}'"
            ]
        return "owner", [f"returns parameter '{pname}' unchanged"]

    return "move", ["return value is constructed or computed in the function body"]


def infer_assignment_ownership(target: str) -> tuple[str, list[str]]:
    """Best-effort ownership guess for a plain local assignment.

    A ``let`` binding in Rust owns its value by default, so ``"owner"``
    is the safe, conservative default here -- overriding it (e.g. to bind
    a reference instead) is exactly what an explicit ``#!`` directive is
    for.
    """

    return "owner", [f"'{target} = ...' binds a new owned local by default"]


def resolve_ownership(
    directive: "schema.Directive | None",
    inferred_value: str,
    inferred_evidence: list[str],
) -> schema.OwnershipDecision:
    """Combine an optional directive with an inferred guess.

    The directive's value always wins when present -- this function never
    substitutes the inferred value over an explicit directive, even when
    they disagree. Disagreement (or an unrecognized directive keyword) is
    recorded in ``conflict`` rather than silently dropped, so the caller
    can warn (or hard-fail under ``--warnings-as-fatal``) without losing
    the user's explicit choice.
    """

    if directive is not None:
        value = directive.value
        conflict: str | None = None
        if not is_valid_ownership_value(value):
            conflict = (
                f"directive value '{value}' is not a recognized ownership "
                f"keyword ({', '.join(schema.OWNERSHIP_VALUES)}); usage-based "
                f"inference would have chosen '{inferred_value}'"
            )
        elif value != inferred_value:
            reason = "; ".join(inferred_evidence) if inferred_evidence else "no clear usage evidence"
            conflict = (
                f"directive says '{value}' but usage-based inference would have "
                f"chosen '{inferred_value}' ({reason})"
            )
        return schema.OwnershipDecision(
            value=value,
            source="directive",
            directive=directive,
            conflict=conflict,
        )

    return schema.OwnershipDecision(
        value=inferred_value,
        source="inferred",
        evidence=inferred_evidence,
    )
