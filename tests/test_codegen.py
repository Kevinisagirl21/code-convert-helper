"""Tests for codegen.rust_writer -- v2's hole-free rendering.

v1's test_codegen.py had a dedicated "type hole renders as an identifier"
test; v2 has no hole state at all (see ir/schema.py's Milestone 1
notes), so that test is gone rather than adapted. Everything else is
re-run against fully-hinted v2 source, plus new coverage for
AnnAssign/AugAssign shapes that didn't exist as builder inputs in v1.
"""

from codegen import rust_writer
from ir import builder


def _rust(src: str) -> str:
    module = builder.build_module_ir(src, "t.py")
    builder.apply_collection_ambiguities(module)
    return rust_writer.render_module(module)


def test_simple_function_renders_valid_shape():
    out = _rust("def add(a: int, b: int) -> int:\n    return a + b\n")
    assert "fn add(a: i64, b: i64) -> i64 {" in out
    assert "return (a + b);" in out


def test_generated_header_no_longer_mentions_type_hole():
    out = _rust("def f() -> None:\n    pass\n")
    assert "TYPE HOLE" not in out
    assert "AMBIGUOUS/UNSUPPORTED" in out


def test_accumulator_emits_let_mut_once_and_plain_reassignment_after():
    out = _rust("def f(n: int) -> int:\n    t: int = 0\n    for i in range(n):\n        t = t + i\n    return t\n")
    assert "let mut t: i64 = 0;" in out
    assert "t = (t + i);" in out
    assert out.count("let") == 1  # only the initial binding uses `let`


def test_augmented_assignment_accumulator_emits_let_mut_once():
    out = _rust("def f(n: int) -> int:\n    t: int = 0\n    for i in range(n):\n        t += i\n    return t\n")
    assert "let mut t: i64 = 0;" in out
    assert "t = (t + i);" in out
    assert out.count("let") == 1


def test_self_attr_mutation_has_no_let_and_no_type():
    src = (
        "class C:\n"
        "    def __init__(self, x: int):\n"
        "        self.x = x\n"
        "    def bump(self) -> None:\n"
        "        self.x = self.x + 1\n"
    )
    out = _rust(src)
    assert "self.x = (self.x + 1);" in out
    assert "let self.x" not in out


def test_self_attr_augmented_assignment_has_no_let():
    src = (
        "class C:\n"
        "    def __init__(self, x: int):\n"
        "        self.x = x\n"
        "    def bump(self) -> None:\n"
        "        self.x += 1\n"
    )
    out = _rust(src)
    assert "self.x = (self.x + 1);" in out


def test_class_renders_struct_and_impl_with_ambiguity_marker():
    src = "class C:\n    def __init__(self, x: int):\n        self.x = x\n"
    out = _rust(src)
    assert "// AMBIGUOUS[class-shape]" in out
    assert "pub struct C {" in out
    assert "impl C {" in out
    assert "pub fn new(x: i64) -> Self {" in out


def test_raise_unwraps_exception_message_into_panic():
    out = _rust("def f() -> None:\n    raise ValueError('bad')\n")
    assert 'panic!("{}", "bad".to_string());' in out
    assert "// AMBIGUOUS[error-handling]" in out


def test_for_over_range_uses_rust_range_syntax():
    out = _rust("def f(n: int) -> None:\n    for i in range(n):\n        print(i)\n")
    assert "for i in 0..n {" in out


def test_for_over_sequence_uses_iter_and_marks_ambiguity():
    out = _rust("def f(items: list[int]) -> None:\n    for x in items:\n        print(x)\n")
    assert "for x in items.iter()" in out
    assert "// AMBIGUOUS[iteration-style]" in out


def test_list_and_dict_literals():
    out = _rust("def f() -> None:\n    a: list[int] = [1, 2]\n    b: dict[str, int] = {'k': 1}\n")
    assert "vec![1, 2]" in out
    assert "HashMap::from([" in out
    assert "use std::collections::HashMap;" in out


def test_unsupported_construct_is_kept_verbatim_as_a_comment_block():
    out = _rust("def f() -> None:\n    with open('x') as fh:\n        pass\n")
    assert "UNSUPPORTED" in out
    assert "with open" in out
