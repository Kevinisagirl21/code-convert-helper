"""Tests for typing_inference.infer -- v2's mandatory-hint resolution.

v1's test_infer.py exercised literal-/usage-based *inference* (guessing a
type from `x = 5`, tracking a hole's evidence, etc.). None of that exists
in v2: every type comes from an explicit hint. These tests are redesigned
around that -- resolving a hint's text to a Rust type, handling the
list[T]/dict[K, V] MVP generics, and hard-erroring (never guessing) on
anything unsupported or missing.
"""

import libcst as cst
import pytest

from ir import schema
from typing_inference import infer


def _annotation(src: str) -> cst.Annotation:
    return cst.Annotation(annotation=cst.parse_expression(src))


def test_int_annotation():
    assert infer.type_from_annotation(_annotation("int")) == schema.ConcreteType(value="i64")


def test_float_annotation():
    assert infer.type_from_annotation(_annotation("float")) == schema.ConcreteType(value="f64")


def test_str_annotation():
    assert infer.type_from_annotation(_annotation("str")) == schema.ConcreteType(value="String")


def test_bool_annotation():
    assert infer.type_from_annotation(_annotation("bool")) == schema.ConcreteType(value="bool")


def test_none_annotation():
    assert infer.type_from_annotation(_annotation("None")) == schema.ConcreteType(value="()")


def test_list_of_int_annotation():
    result = infer.type_from_annotation(_annotation("list[int]"))
    assert result == schema.ConcreteType(value="Vec<i64>")


def test_dict_str_to_int_annotation():
    result = infer.type_from_annotation(_annotation("dict[str, int]"))
    assert result == schema.ConcreteType(value="HashMap<String, i64>")


def test_nested_list_of_list_annotation():
    result = infer.type_from_annotation(_annotation("list[list[int]]"))
    assert result == schema.ConcreteType(value="Vec<Vec<i64>>")


def test_missing_annotation_is_a_hard_error_not_a_hole():
    """v1 returned `None` here so a caller could fall back to inference.
    v2 has no fallback -- a missing hint reaching this function at all is
    a preflight/builder invariant violation, so it raises."""

    with pytest.raises(ValueError):
        infer.type_from_annotation(None)


def test_unrecognized_annotation_is_a_hard_error_not_an_informed_hole():
    """v1 turned an unrecognized hint (e.g. a user-defined class) into a
    TypeHole carrying the hint text as evidence. v2 has no hole state, so
    this is a hard UnsupportedTypeHintError instead."""

    with pytest.raises(infer.UnsupportedTypeHintError, match="SomeCustomType"):
        infer.type_from_annotation(_annotation("SomeCustomType"))


def test_unsupported_generic_base_is_a_hard_error():
    with pytest.raises(infer.UnsupportedTypeHintError):
        infer.type_from_annotation(_annotation("set[int]"))
