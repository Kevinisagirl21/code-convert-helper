"""Tests for typing_inference.resolver.TypeResolver."""

import libcst as cst
import pytest

from ir import schema
from typing_inference.resolver import (
    MandatoryHintError,
    TupleUnpackingNotSupportedError,
    TypeResolver,
)


def _annotation(src: str) -> cst.Annotation:
    return cst.Annotation(annotation=cst.parse_expression(src))


def _expr(src: str) -> cst.BaseExpression:
    return cst.parse_expression(src)


def test_param_with_hint_resolves():
    r = TypeResolver()
    assert r.resolve_param("x", _annotation("int")) == schema.ConcreteType(value="i64")


def test_param_missing_hint_is_mandatory_hint_error():
    r = TypeResolver()
    with pytest.raises(MandatoryHintError, match="'x'"):
        r.resolve_param("x", None)


def test_return_missing_hint_is_mandatory_hint_error():
    r = TypeResolver()
    with pytest.raises(MandatoryHintError, match="f"):
        r.resolve_return("f", None)


def test_first_assignment_requires_a_hint():
    r = TypeResolver()
    with pytest.raises(MandatoryHintError):
        r.resolve_assignment("x", None, _expr("5"))


def test_first_assignment_with_hint_registers_it():
    r = TypeResolver()
    resolved = r.resolve_assignment("x", _annotation("int"), _expr("5"))
    assert resolved == schema.ConcreteType(value="i64")
    assert r.lookup("x") == schema.ConcreteType(value="i64")


def test_reassignment_exempt_reuses_first_hint():
    r = TypeResolver()
    r.resolve_assignment("x", _annotation("int"), _expr("5"))
    resolved = r.resolve_assignment("x", None, _expr("10"))
    assert resolved == schema.ConcreteType(value="i64")


def test_augmented_assignment_exempt_after_prior_hint():
    r = TypeResolver()
    r.resolve_assignment("x", _annotation("int"), _expr("5"))
    resolved = r.resolve_assignment("x", None, _expr("1"), is_aug_assign=True)
    assert resolved == schema.ConcreteType(value="i64")


def test_augmented_assignment_without_prior_hint_is_an_error():
    r = TypeResolver()
    with pytest.raises(MandatoryHintError):
        r.resolve_assignment("x", None, _expr("1"), is_aug_assign=True)


def test_derive_local_from_already_hinted_name():
    r = TypeResolver()
    r.resolve_param("start", _annotation("int"))
    resolved = r.resolve_assignment("value", None, _expr("start"))
    assert resolved == schema.ConcreteType(value="i64")


def test_derive_local_list_wrap_from_hinted_param():
    r = TypeResolver()
    r.resolve_param("start", _annotation("int"))
    resolved = r.resolve_assignment("history", None, _expr("[start]"))
    assert resolved == schema.ConcreteType(value="Vec<i64>")


def test_cannot_derive_from_unhinted_name_is_an_error():
    r = TypeResolver()
    with pytest.raises(MandatoryHintError):
        r.resolve_assignment("value", None, _expr("unknown_name"))


def test_self_attr_first_assignment_requires_a_hint():
    r = TypeResolver()
    with pytest.raises(MandatoryHintError, match="self.x"):
        r.resolve_assignment("x", None, _expr("5"), is_self_attr=True)


def test_self_attr_derives_from_hinted_param_passthrough():
    r = TypeResolver()
    r.resolve_param("x", _annotation("int"))
    resolved = r.resolve_assignment("x", None, _expr("x"), is_self_attr=True)
    assert resolved == schema.ConcreteType(value="i64")


def test_self_attr_and_local_of_same_name_do_not_collide():
    r = TypeResolver()
    r.resolve_assignment("x", _annotation("str"), _expr('"hi"'))
    with pytest.raises(MandatoryHintError):
        r.resolve_assignment("x", None, _expr("5"), is_self_attr=True)


def test_self_attr_reassignment_exempt():
    r = TypeResolver()
    r.resolve_assignment("x", _annotation("int"), _expr("5"), is_self_attr=True)
    resolved = r.resolve_assignment("x", None, _expr("10"), is_self_attr=True)
    assert resolved == schema.ConcreteType(value="i64")


def test_for_range_resolves_to_int():
    r = TypeResolver()
    resolved = r.resolve_for_target("i", "range", _expr("range(n)"))
    assert resolved == schema.ConcreteType(value="i64")


def test_for_sequence_derives_element_type_from_hinted_list_name():
    r = TypeResolver()
    r.resolve_assignment("items", _annotation("list[int]"), _expr("[1, 2]"))
    resolved = r.resolve_for_target("x", "sequence", _expr("items"))
    assert resolved == schema.ConcreteType(value="i64")


def test_for_sequence_derives_from_self_attr_list():
    r = TypeResolver()
    r.resolve_assignment("history", _annotation("list[int]"), _expr("[1]"), is_self_attr=True)
    resolved = r.resolve_for_target("h", "sequence", _expr("self.history"))
    assert resolved == schema.ConcreteType(value="i64")


def test_for_sequence_over_unknown_iterable_is_an_error():
    r = TypeResolver()
    with pytest.raises(MandatoryHintError):
        r.resolve_for_target("x", "sequence", _expr("unknown_list"))


def test_reject_tuple_unpacking_raises():
    with pytest.raises(TupleUnpackingNotSupportedError):
        TypeResolver.reject_tuple_unpacking("a, b = 1, 2")
