import libcst as cst

from directives.parser import parse_directive_text
from ir import schema
from ownership import resolver as ownership


def _body(src: str) -> list[cst.BaseStatement]:
    return list(cst.parse_module(src).body)


def test_copy_primitive_param_always_owner():
    body = _body("print(n)\n")
    value, evidence = ownership.infer_param_ownership("n", body, schema.ConcreteType(value="i64"))
    assert value == "owner"
    assert evidence


def test_non_copy_param_read_only_infers_refer():
    body = _body("print(name)\n")
    value, evidence = ownership.infer_param_ownership("name", body, schema.ConcreteType(value="String"))
    assert value == "refer"


def test_non_copy_param_returned_infers_move():
    body = _body("return name\n")
    value, _ = ownership.infer_param_ownership("name", body, schema.ConcreteType(value="String"))
    assert value == "move"


def test_non_copy_param_stored_in_self_attr_infers_move():
    body = _body("self.name = name\n")
    value, evidence = ownership.infer_param_ownership("name", body, schema.ConcreteType(value="String"))
    assert value == "move"
    assert any("self.name" in e for e in evidence)


def test_reassignment_does_not_force_refer_mut():
    # Reassigning a Python parameter name has no observable-by-caller
    # mutation semantics, so inference must never produce "refer_mut" --
    # only a directive is trusted for that value.
    body = _body("name = name + '!'\nreturn name\n")
    value, _ = ownership.infer_param_ownership("name", body, schema.ConcreteType(value="String"))
    assert value != "refer_mut"


def test_return_ownership_no_return_value():
    value, _ = ownership.infer_return_ownership([], {"x": "owner"})
    assert value == "move"


def test_return_ownership_echoes_single_param():
    value, _ = ownership.infer_return_ownership(["x"], {"x": "owner"})
    assert value == "owner"


def test_return_ownership_echoes_reference_param_as_reference():
    # Returning a '&String' parameter as a plain owned 'String' would be a
    # real type mismatch -- the return type must echo the param's own
    # reference-ness, not always default to "owner".
    value, _ = ownership.infer_return_ownership(["x"], {"x": "refer"})
    assert value == "refer"


def test_return_ownership_computed_value_is_move():
    value, _ = ownership.infer_return_ownership(["x + 1"], {"x": "owner"})
    assert value == "move"


def test_assignment_ownership_always_owner():
    value, _ = ownership.infer_assignment_ownership("total")
    assert value == "owner"


def test_resolve_ownership_no_directive_uses_inference():
    decision = ownership.resolve_ownership(None, "refer", ["read only"])
    assert decision.source == "inferred"
    assert decision.value == "refer"
    assert decision.conflict is None


def test_resolve_ownership_directive_wins_even_on_conflict():
    directive = parse_directive_text("#! owner")
    decision = ownership.resolve_ownership(directive, "refer", ["read only"])
    assert decision.source == "directive"
    assert decision.value == "owner"
    assert decision.conflict is not None
    assert "refer" in decision.conflict


def test_resolve_ownership_directive_agreeing_has_no_conflict():
    directive = parse_directive_text("#! refer")
    decision = ownership.resolve_ownership(directive, "refer", ["read only"])
    assert decision.source == "directive"
    assert decision.conflict is None


def test_resolve_ownership_invalid_keyword_is_a_conflict():
    directive = parse_directive_text("#! own")
    decision = ownership.resolve_ownership(directive, "refer", ["read only"])
    assert decision.value == "own"
    assert decision.conflict is not None
