from codegen import rust_writer
from ir import builder


def _rust(src: str) -> str:
    module = builder.build_module_ir(src, "t.py")
    builder.apply_collection_ambiguities(module)
    return rust_writer.render_module(module)


def test_simple_function_renders_valid_shape():
    out = _rust("def add(a: int, b: int) -> int:\n    return a + b\n")
    assert "fn add(a: i64, b: i64) -> i64 {" in out
    # needless_return: tail position -> bare expression, no `return`, no
    # parens (`+` doesn't need any at top level).
    assert "\n    a + b\n" in out
    assert "return a + b" not in out


def test_type_hole_renders_as_identifier_not_bare_comment():
    out = _rust("def f(x):\n    return x\n")
    assert "x: TypeHole_hole_0001" in out
    assert "// TYPE HOLE hole_0001" in out


def test_accumulator_emits_let_mut_once_and_compound_assign_after():
    out = _rust("def f(n: int) -> int:\n    t = 0\n    for i in range(n):\n        t = t + i\n    return t\n")
    assert "let mut t: i64 = 0;" in out
    # clippy::assign_op_pattern: `t = t + i` renders as `t += i`.
    assert "t += i;" in out
    assert "t = t + i;" not in out
    assert out.count("let mut t") == 1
    assert out.count("let t") == 0
    # tail return -> bare `t`, not `return t;`
    assert "\n    t\n}" in out
    assert "return t;" not in out


def test_self_attr_mutation_has_no_let_and_no_type():
    src = (
        "class C:\n"
        "    def __init__(self, x: int):\n"
        "        self.x = x\n"
        "    def bump(self):\n"
        "        self.x = self.x + 1\n"
    )
    out = _rust(src)
    # clippy::assign_op_pattern: `self.x = self.x + 1` renders as `self.x += 1`.
    assert "self.x += 1;" in out
    assert "self.x = self.x + 1;" not in out
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
    # No needless `.to_string()` -- a literal message panics directly.
    assert 'panic!("bad");' in out
    assert ".to_string()" not in out
    assert "// AMBIGUOUS[error-handling]" in out


def test_raise_with_name_message_uses_inlined_format_capture():
    out = _rust("def f(msg: str):\n    raise ValueError(msg)\n")
    assert 'panic!("{msg}");' in out


def test_for_over_range_uses_rust_range_syntax():
    out = _rust("def f(n: int):\n    for i in range(n):\n        print(i)\n")
    assert "for i in 0..n {" in out


def test_for_over_sequence_borrows_instead_of_using_iter():
    out = _rust("def f(items):\n    for x in items:\n        print(x)\n")
    assert "for x in &items {" in out
    assert ".iter()" not in out
    assert "// AMBIGUOUS[iteration-style]" in out


def test_print_inlines_a_plain_name_argument():
    out = _rust("def f(items):\n    for x in items:\n        print(x)\n")
    assert 'println!("{x}")' in out
    assert 'println!("{}", x)' not in out


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


# ---------------------------------------------------------------------------
# Milestone 3: clippy-clean-by-construction rendering
# ---------------------------------------------------------------------------


def test_needless_return_removed_from_function_tail():
    out = _rust("def f(n: int) -> int:\n    return n\n")
    assert "return n;" not in out
    assert "fn f(n: i64) -> i64 {\n    n\n}" in out


def test_needless_return_removed_but_early_returns_kept():
    src = (
        "def clamp(value: int, lo: int, hi: int) -> int:\n"
        "    if value < lo:\n"
        "        return lo\n"
        "    if value > hi:\n"
        "        return hi\n"
        "    return value\n"
    )
    out = _rust(src)
    # Early returns inside the `if` bodies are genuine control flow, not
    # the tail return -- they stay explicit `return ...;` statements.
    assert "return lo;" in out
    assert "return hi;" in out
    # Only the function's own final statement drops the `return` keyword.
    assert out.rstrip().endswith("value\n}") or "\n    value\n}" in out
    assert "return value;" not in out


def test_unit_returning_function_keeps_explicit_return_semantics():
    # No non-unit return type -> nothing to use as a tail expression;
    # the (rare) explicit `return;`/no-value form is left untouched.
    out = _rust("def f():\n    x = 1\n    print(x)\n")
    assert "fn f() {" in out


def test_no_unnecessary_parens_around_simple_binop():
    out = _rust("def add(a: int, b: int) -> int:\n    return a + b\n")
    assert "(a + b)" not in out
    assert "a + b" in out


def test_no_unnecessary_parens_around_comparison_in_if():
    out = _rust("def f(value: int, lo: int) -> int:\n    if value < lo:\n        return lo\n    return value\n")
    assert "if value < lo {" in out
    assert "if (value < lo)" not in out


def test_parens_kept_where_precedence_actually_requires_them():
    # a - (b - c) != a - b - c, so the regrouping parens must survive.
    out = _rust("def f(a: int, b: int, c: int) -> int:\n    return a - (b - c)\n")
    assert "a - (b - c)" in out


def test_parens_dropped_when_regrouping_is_a_no_op():
    # (a - b) - c == a - b - c, so these parens are redundant and should
    # not be emitted.
    out = _rust("def f(a: int, b: int, c: int) -> int:\n    return (a - b) - c\n")
    assert "(a - b) - c" not in out
    assert "a - b - c" in out


def test_mixed_bool_ops_keep_precedence_correct_parens():
    out = _rust("def f(a: bool, b: bool, c: bool) -> bool:\n    return a and (b or c)\n")
    assert "a && (b || c)" in out


def test_higher_precedence_multiply_needs_no_parens_next_to_add():
    out = _rust("def f(a: int, b: int) -> int:\n    return a + b * 2\n")
    assert "a + b * 2" in out


def test_compound_assign_falls_back_when_shape_does_not_match():
    # `x = y + 1` is not `x = x + ...` -- must stay a plain reassignment,
    # never misread as a compound assignment on the wrong variable.
    src = (
        "def f(n: int) -> int:\n"
        "    x = 0\n"
        "    y = 0\n"
        "    for i in range(n):\n"
        "        x = y + i\n"
        "        y = i\n"
        "    return x\n"
    )
    out = _rust(src)
    assert "x = y + i;" in out
    assert "x += " not in out


def test_mut_self_only_emitted_when_method_mutates_a_field():
    src = (
        "class C:\n"
        "    def __init__(self, x: int):\n"
        "        self.x = x\n"
        "    def bump(self):\n"
        "        self.x = self.x + 1\n"
        "    def compute_only(self):\n"
        "        total = 0\n"
        "        for i in range(self.x):\n"
        "            total = total + i\n"
        "        return total\n"
    )
    out = _rust(src)
    assert "fn bump(&mut self)" in out
    # `compute_only` only reassigns a local accumulator -- it never
    # mutates a field of `self`, so it must not get `&mut self`.
    assert "fn compute_only(&self)" in out
    assert "fn compute_only(&mut self)" not in out
