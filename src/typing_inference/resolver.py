"""Shared mandatory-hint resolution logic for v2."""

from __future__ import annotations

import libcst as cst

from ir import schema
from typing_inference import infer


class MandatoryHintError(ValueError):
    """A hint was required but missing, and nothing could be derived."""


class TupleUnpackingNotSupportedError(ValueError):
    """Tuple/multi-target assignment isn't supported in v2's MVP yet."""


def _self_attr_key(attr: str) -> str:
    return f"self.{attr}"


class TypeResolver:
    """Flat, whole-file, first-hint-wins type table."""

    def __init__(self) -> None:
        self._hints: dict[str, schema.ConcreteType] = {}

    def register_annotation(self, name: str, annotation: cst.Annotation) -> schema.ConcreteType:
        resolved = infer.type_from_annotation(annotation)
        self._hints[name] = resolved
        return resolved

    def register_type(self, name: str, resolved: schema.ConcreteType) -> None:
        self._hints[name] = resolved

    def lookup(self, name: str) -> schema.ConcreteType | None:
        return self._hints.get(name)

    def resolve_param(self, name: str, annotation: cst.Annotation | None) -> schema.ConcreteType:
        if annotation is None:
            raise MandatoryHintError(
                f"parameter '{name}' is missing a type hint (e.g. '{name}: int')"
            )
        return self.register_annotation(name, annotation)

    def resolve_return(self, function_name: str, annotation: cst.Annotation | None) -> schema.ConcreteType:
        if annotation is None:
            raise MandatoryHintError(
                f"function '{function_name}' is missing a return type hint "
                f"(e.g. '-> int'); v2 requires explicit return annotations"
            )
        return infer.type_from_annotation(annotation)

    def resolve_assignment(
        self,
        target_name: str,
        annotation: cst.Annotation | None,
        value: cst.BaseExpression,
        *,
        is_self_attr: bool = False,
        is_aug_assign: bool = False,
    ) -> schema.ConcreteType:
        key = _self_attr_key(target_name) if is_self_attr else target_name
        display = f"self.{target_name}" if is_self_attr else target_name

        if annotation is not None:
            resolved = infer.type_from_annotation(annotation)
            self._hints[key] = resolved
            return resolved

        existing = self._hints.get(key)
        if existing is not None:
            return existing

        if is_aug_assign:
            raise MandatoryHintError(
                f"'{display}' is used with an augmented assignment but was "
                f"never hinted at an earlier assignment"
            )

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

    def resolve_for_target(
        self, target_name: str, iter_kind: str, iter_expr: cst.BaseExpression
    ) -> schema.ConcreteType:
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

    @staticmethod
    def reject_tuple_unpacking(display: str) -> None:
        raise TupleUnpackingNotSupportedError(
            f"tuple-unpacking not yet supported ('{display}'); use separate "
            f"hinted assignments instead"
        )
