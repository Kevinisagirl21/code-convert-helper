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


def test_type_hole_renders_as_identifier_not_bare_comment():
    out = _rust("def f(x):\n    return x\n")
    # Must still be a syntactically plausible type position -- a bare
    # comment there would make the file fail to even parse.
    assert "x: TypeHole_hole_0001" in out
    assert "// TYPE HOLE hole_0001" in out


def test_accumulator_emits_let_mut_once_and_plain_reassignment_after():
    out = _rust("def f(n: int) -> int:\n    t = 0\n    for i in range(n):\n        t = t + i\n    return t\n")
    assert "let mut t: i64 = 0;" in out
    assert "t = (t + i);" in out
    # only the initial binding uses `let` -- the reassignment inside the
    # loop uses plain `t = ...;` with no `let`/`let mut` prefix.
    assert out.count("let mut t") == 1
    assert out.count("let t") == 0


def test_self_attr_mutation_has_no_let_and_no_type():
    src = (
        "class C:\n"
        "    def __init__(self, x: int):\n"
        "        self.x = x\n"
        "    def bump(self):\n"
        "        self.x = self.x + 1\n"
    )
    out = _rust(src)
    assert "self.x = (self.x + 1);" in out
    assert "let self.x" not in out


def test_class_renders_struct_and_impl_with_ambiguity_marker():
    src = "class C:\n    def __init__(self, x: int):\n        self.x = x\n"
    out = _rust(src)
    assert "// AMBIGUOUS[class-shape]" in out
    assert "pub struct C {" in out
    assert "impl C {" in out
    assert "pub fn new(x: i64) -> Self {" in out


def test_raise_unwraps_exception_message_into_panic():
    out = _rust("def f():\n    raise ValueError('bad')\n")
    assert 'panic!("{}", "bad".to_string());' in out
    assert "// AMBIGUOUS[error-handling]" in out


def test_for_over_range_uses_rust_range_syntax():
    out = _rust("def f(n: int):\n    for i in range(n):\n        print(i)\n")
    assert "for i in 0..n {" in out


def test_for_over_sequence_uses_iter_and_marks_ambiguity():
    out = _rust("def f(items):\n    for x in items:\n        print(x)\n")
    assert "for x in items.iter()" in out
    assert "// AMBIGUOUS[iteration-style]" in out


def test_list_and_dict_literals():
    out = _rust("def f():\n    a = [1, 2]\n    b = {'k': 1}\n")
    assert "vec![1, 2]" in out
    assert "HashMap::from([" in out
    assert "use std::collections::HashMap;" in out


def test_unsupported_construct_is_kept_verbatim_as_a_comment_block():
    out = _rust("def f():\n    with open('x') as fh:\n        pass\n")
    assert "UNSUPPORTED" in out
    assert "with open" in out


# ---------------------------------------------------------------------------
# Milestone 2: ownership-aware rendering
# ---------------------------------------------------------------------------


def test_directive_owner_param_renders_plain_type():
    src = (
        "def f(\n"
        "    name: str,  #! owner\n"
        ") -> str:\n"
        "    return name\n"
    )
    out = _rust(src)
    assert "fn f(name: String) -> String {" in out


def test_directive_refer_param_renders_reference_type():
    src = (
        "def f(\n"
        "    name: str,  #! refer\n"
        "):\n"
        "    print(name)\n"
    )
    out = _rust(src)
    assert "fn f(name: &String)" in out


def test_directive_refer_mut_param_renders_mut_reference_type():
    src = (
        "def f(\n"
        "    name: str,  #! refer_mut\n"
        "):\n"
        "    print(name)\n"
    )
    out = _rust(src)
    assert "fn f(name: &mut String)" in out


def test_inferred_ownership_emits_reference_comment():
    src = "def f(name: str):\n    print(name)\n"
    out = _rust(src)
    assert "// OWNERSHIP (inferred 'refer')" in out
    assert "fn f(name: &String)" in out


def test_ownership_conflict_emits_conflict_comment():
    src = (
        "def f(\n"
        "    name: str,  #! refer_mut\n"
        ") -> str:\n"
        "    return name\n"
    )
    out = _rust(src)
    assert "// OWNERSHIP CONFLICT" in out
    # directive still wins even though it conflicts with inference, and
    # the return type echoes the same reference-ness so the signature is
    # internally consistent (returning &mut String as a plain String
    # would be a real type mismatch).
    assert "fn f(name: &mut String) -> &mut String {" in out


def test_copy_primitive_param_never_gets_reference_prefix():
    out = _rust("def f(n: int):\n    print(n)\n")
    assert "fn f(n: i64)" in out
    assert "&i64" not in out


def test_directive_on_assignment_renders_reference_binding():
    src = (
        "def f():\n"
        "    x = 'hello'  #! refer\n"
        "    print(x)\n"
    )
    out = _rust(src)
    assert 'let x: &String = &"hello".to_string();' in out
