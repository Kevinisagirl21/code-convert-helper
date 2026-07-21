"""Shared mandatory-hint resolution logic for v2.

Both :mod:`preflight.checks` (which only needs to *validate* and collect
every violation for one clean diagnostic report) and :mod:`ir.builder`
(which needs the *actual resolved type* to build the IR) walk the same
"does this assignment have a type, and if not, can one be legitimately
derived" logic. This module is the single place that logic lives, so the
two callers can't drift out of sync.

Rules encoded here (from the Milestone 1 design interview)
------------------------------------------------------------
* Type hints are mandatory and strict everywhere: params, return types,
  first assignments, and ``self.`` attributes.
* Re-assignment (``x = 10`` after an earlier hinted ``x: int = 5``) and
  augmented assignment (``x += 1``) are exempt -- the type is looked up
  from the name's first hinted appearance, not re-guessed.
* This lookup is a **flat, single table for the whole file** -- no
  per-function or per-class scoping. We're trusting that the input has
  already been checked by mypy/pyright/pylint, so a local name shadowing
  a module-level (or another function's) name with a *different* type is
  assumed not to happen. This is a deliberate scope reduction (see the
  Milestone 1 design conversation): py2rust's preflight is not
  re-implementing a real type checker.
* ``global x; x = ...`` needs no special-casing under the flat-table
  model above -- it just resolves via the same lookup as any other
  reassignment.
* A bare local/attribute assigned directly from an already-hinted name
  (most commonly a constructor parameter, e.g. ``self.x = x``) derives
  its type from that name rather than requiring its own redundant hint.
* Tuple/multiple-target assignment (``a, b = 1, 2`` or ``x = y = 5``) is
  hard-rejected for now -- Python has no hint syntax for it, and it's
  not in the ROADMAP's MVP subset list.
* ``for x in range(...)``: exempt, ``x`` resolves to the int type.
  ``for x in <sequence>``: exempt if the sequence's element type is
  already known (e.g. a hinted ``Vec<T>``-typed name); otherwise
  rejected, since there's nothing to legitimately derive from.
"""

from __future__ import annotations

import libcst as cst

from ir import schema
from typing_inference import infer


class MandatoryHintError(ValueError):
    """A hint was required but missing, and nothing could be derived."""


class TupleUnpackingNotSupportedError(ValueError):
    """Tuple/multi-target assignment isn't supported in v2's MVP yet."""


def _self_attr_key(attr: str) -> str:
    # Separate namespace from plain locals/params so `self.x` and a local
    # `x` in the same or another function don't collide in the flat table.
    return f"self.{attr}"


class TypeResolver:
    """Flat, whole-file, first-hint-wins type table.

    One instance is meant to be shared across an entire source file (not
    per-function/per-class), per the "trust already-checked input, don't
    reimplement scope resolution" decision.
    """

    def __init__(self) -> None:
        self._hints: dict[str, schema.ConcreteType] = {}

    # -- registration --------------------------------------------------

    def register_annotation(self, name: str, annotation: cst.Annotation) -> schema.ConcreteType:
        """Register `name`'s type from an explicit annotation node."""

        resolved = infer.type_from_annotation(annotation)
        self._hints[name] = resolved
        return resolved

    def register_type(self, name: str, resolved: schema.ConcreteType) -> None:
        self._hints[name] = resolved

    def lookup(self, name: str) -> schema.ConcreteType | None:
        return self._hints.get(name)

    # -- parameters ------------------------------------------------------

    def resolve_param(self, name: str, annotation: cst.Annotation | None) -> schema.ConcreteType:
        """Resolve a function/method parameter's type. Always mandatory."""

        if annotation is None:
            raise MandatoryHintError(
                f"parameter '{name}' is missing a type hint (e.g. '{name}: int')"
            )
        return self.register_annotation(name, annotation)

    # -- return types ------------------------------------------------------

    def resolve_return(self, function_name: str, annotation: cst.Annotation | None) -> schema.ConcreteType:
        """Resolve a function's return type. Always mandatory -- no more
        inferring it from `return` statements like v1 did."""

        if annotation is None:
            raise MandatoryHintError(
                f"function '{function_name}' is missing a return type hint "
                f"(e.g. '-> int'); v2 requires explicit return annotations"
            )
        return infer.type_from_annotation(annotation)

    # -- assignments -------------------------------------------------------

    def resolve_assignment(
        self,
        target_name: str,
        annotation: cst.Annotation | None,
        value: cst.BaseExpression,
        *,
        is_self_attr: bool = False,
        is_aug_assign: bool = False,
    ) -> schema.ConcreteType:
        """Resolve the type for one assignment target.

        ``target_name`` is the bare name (``x``) or attribute name
        (``x`` for ``self.x``, with ``is_self_attr=True``) -- callers are
        responsible for building any dotted display text themselves.
        """

        key = _self_attr_key(target_name) if is_self_attr else target_name
        display = f"self.{target_name}" if is_self_attr else target_name

        if annotation is not None:
            resolved = infer.type_from_annotation(annotation)
            self._hints[key] = resolved
            return resolved

        existing = self._hints.get(key)
        if existing is not None:
            # Reassignment / augmented-assignment: exempt, reuse the type
            # recorded at this name's first, hinted appearance.
            return existing

        if is_aug_assign:
            # `x += 1` can't syntactically carry a hint, and there's no
            # prior binding to look up -- `x` was never hinted at all.
            raise MandatoryHintError(
                f"'{display}' is used with an augmented assignment but was "
                f"never hinted at an earlier assignment"
            )

        # No annotation, no prior binding: allow deriving from an
        # already-hinted name on the right-hand side (most commonly a
        # hinted constructor parameter, e.g. `self.x = x`).
        derived = self._derive_from_expr(value)
        if derived is not None:
            self._hints[key] = derived
            return derived

        raise MandatoryHintError(
            f"first assignment to '{display}' is missing a type hint "
            f"(e.g. '{display}: int = ...')"
        )

    def _derive_from_expr(self, value: cst.BaseExpression) -> schema.ConcreteType | None:
        if isinstance(value, cst.Name):
            return self._hints.get(value.value)
        if isinstance(value, cst.List) and value.elements:
            first = value.elements[0].value
            if isinstance(first, cst.Name):
                elem = self._hints.get(first.value)
                if elem is not None:
                    return schema.ConcreteType(value=f"Vec<{elem.value}>")
        return None

    # -- for loops -----------------------------------------------------

    def resolve_for_target(
        self, target_name: str, iter_kind: str, iter_expr: cst.BaseExpression
    ) -> schema.ConcreteType:
        """Resolve a ``for`` loop's target type.

        No hint syntax exists for loop targets, so this is always
        derived from the iterable: ``range(...)`` -> the int type;
        a sequence -> that sequence's known element type, if any.
        """

        if iter_kind == "range":
            resolved = schema.ConcreteType(value="i64")
            self._hints[target_name] = resolved
            return resolved

        seq_type: schema.ConcreteType | None = None
        if isinstance(iter_expr, cst.Name):
            seq_type = self._hints.get(iter_expr.value)
        elif (
            isinstance(iter_expr, cst.Attribute)
            and isinstance(iter_expr.value, cst.Name)
            and iter_expr.value.value == "self"
        ):
            seq_type = self._hints.get(_self_attr_key(iter_expr.attr.value))

        if seq_type is not None and seq_type.value.startswith("Vec<") and seq_type.value.endswith(">"):
            elem_value = seq_type.value[len("Vec<"):-1]
            resolved = schema.ConcreteType(value=elem_value)
            self._hints[target_name] = resolved
            return resolved

        raise MandatoryHintError(
            f"cannot determine a type for loop variable '{target_name}': "
            f"its iterable's element type isn't known (only 'range(...)' "
            f"or a name/'self.' attribute already hinted as a list are supported)"
        )

    # -- tuple/multi-target rejection ------------------------------------

    @staticmethod
    def reject_tuple_unpacking(display: str) -> None:
        raise TupleUnpackingNotSupportedError(
            f"tuple-unpacking not yet supported ('{display}'); use separate "
            f"hinted assignments instead"
        )
