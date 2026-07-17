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
