"""Tests for ir.builder -- v2's mandatory-hint IR construction.

v1's tests exercised type-hole fallback (unannotated params becoming
holes). v2 requires hints everywhere, so those cases now use explicit
hints throughout, plus new coverage for the v2-specific shapes:
AnnAssign locals/self-attrs, the self.attr-derived-from-hinted-param
passthrough, and augmented assignment.
"""

from pathlib import Path

from ir import builder, schema, storage


def _build(src: str, filename: str = "t.py") -> schema.ModuleNode:
    module = builder.build_module_ir(src, filename)
    builder.apply_collection_ambiguities(module)
    return module


def test_function_with_annotated_params():
    module = _build("def add(a: int, b: int) -> int:\n    return a + b\n")
    fn = module.body[0]
    assert isinstance(fn, schema.FunctionDefNode)
    assert fn.name == "add"
    assert [p.type for p in fn.params] == [
        schema.ConcreteType(value="i64"),
        schema.ConcreteType(value="i64"),
    ]
    assert fn.return_type == schema.ConcreteType(value="i64")


def test_param_ownership_defaults_to_none_placeholder():
    """Milestone 1: ownership is a bare placeholder, not yet resolved."""

    module = _build("def add(a: int, b: int) -> int:\n    return a + b\n")
    fn = module.body[0]
    assert all(p.ownership is None for p in fn.params)


def test_comment_attaches_to_the_statement_it_describes():
    src = "def f(x: int) -> int:\n    # add one\n    return x + 1\n"
    module = _build(src)
    fn = module.body[0]
    ret_stmt = fn.body[0]
    assert isinstance(ret_stmt, schema.ReturnStmt)
    assert ret_stmt.comments.leading[0].text == "# add one"


def test_trailing_comment_attaches_to_same_line():
    src = "def f() -> None:\n    x: int = 1  # start here\n"
    module = _build(src)
    stmt = module.body[0].body[0]
    assert stmt.comments.trailing[0].text == "# start here"


def test_annotated_local_assignment_resolves_type():
    src = "def f() -> int:\n    x: int = 5\n    return x\n"
    module = _build(src)
    fn = module.body[0]
    assign = fn.body[0]
    assert isinstance(assign, schema.AssignStmt)
    assert assign.type == schema.ConcreteType(value="i64")
    assert assign.target_kind == "name"


def test_reassignment_without_hint_reuses_first_hinted_type():
    src = "def f() -> int:\n    x: int = 5\n    x = 10\n    return x\n"
    module = _build(src)
    fn = module.body[0]
    second = fn.body[1]
    assert isinstance(second, schema.AssignStmt)
    assert second.type == schema.ConcreteType(value="i64")
    # apply_mutability marks the repeated name mutable and the *second*
    # occurrence as a plain reassignment (no redundant `let`).
    assert second.target_kind == "reassign"


def test_augmented_assignment_builds_as_accumulator_pattern():
    src = "def total(n: int) -> int:\n    t: int = 0\n    for i in range(n):\n        t += i\n    return t\n"
    module = _build(src)
    fn = module.body[0]
    first_assign = fn.body[0]
    loop = fn.body[1]
    second_assign = loop.body[0]
    assert isinstance(first_assign, schema.AssignStmt)
    assert first_assign.target_kind == "name"
    assert first_assign.mutable is True
    assert isinstance(second_assign, schema.AssignStmt)
    assert second_assign.target_kind == "reassign"
    assert isinstance(second_assign.value, schema.BinOpExpr)
    assert second_assign.value.op == "+"


def test_class_fields_from_init_passthrough():
    src = (
        "class Counter:\n"
        "    def __init__(self, start: int):\n"
        "        self.value = start\n"
    )
    module = _build(src)
    cls = module.body[0]
    assert isinstance(cls, schema.ClassDefNode)
    assert cls.fields == [schema.ClassFieldNode(name="value", type=schema.ConcreteType(value="i64"))]


def test_class_field_with_explicit_annotation():
    src = (
        "class Counter:\n"
        "    def __init__(self, start: int):\n"
        "        self.value: int = start\n"
    )
    module = _build(src)
    cls = module.body[0]
    assert cls.fields == [schema.ClassFieldNode(name="value", type=schema.ConcreteType(value="i64"))]


def test_class_list_field_resolves_param_element_type():
    src = (
        "class Counter:\n"
        "    def __init__(self, start: int):\n"
        "        self.history = [start]\n"
    )
    module = _build(src)
    cls = module.body[0]
    assert cls.fields[0].type == schema.ConcreteType(value="Vec<i64>")


def test_self_is_not_a_regular_parameter():
    src = "class C:\n    def __init__(self, x: int):\n        self.x = x\n\n    def get(self) -> int:\n        return self.x\n"
    module = _build(src)
    cls = module.body[0]
    get_method = [m for m in cls.methods if m.name == "get"][0]
    assert get_method.params == []


def test_init_does_not_require_a_return_hint():
    """__init__ never contributes a return-type hint to codegen (Rust's
    `new()` always returns `Self`), so it's exempt from the mandatory
    return-hint rule that applies to every other function."""

    src = "class C:\n    def __init__(self, x: int):\n        self.x = x\n"
    module = _build(src)  # must not raise
    cls = module.body[0]
    assert isinstance(cls, schema.ClassDefNode)


def test_accumulator_pattern_marks_mutability_and_reassignment():
    src = "def total(n: int) -> int:\n    t: int = 0\n    for i in range(n):\n        t = t + i\n    return t\n"
    module = _build(src)
    fn = module.body[0]
    first_assign = fn.body[0]
    loop = fn.body[1]
    second_assign = loop.body[0]
    assert isinstance(first_assign, schema.AssignStmt)
    assert first_assign.target_kind == "name"
    assert first_assign.mutable is True
    assert isinstance(second_assign, schema.AssignStmt)
    assert second_assign.target_kind == "reassign"


def test_self_attr_mutation_is_marked_distinctly():
    src = (
        "class C:\n"
        "    def __init__(self, x: int):\n"
        "        self.x = x\n"
        "    def bump(self) -> None:\n"
        "        self.x = self.x + 1\n"
    )
    module = _build(src)
    cls = module.body[0]
    bump = [m for m in cls.methods if m.name == "bump"][0]
    assign = bump.body[0]
    assert assign.target_kind == "self_attr"
    assert assign.target == "self.x"


def test_self_attr_augmented_assignment():
    src = (
        "class C:\n"
        "    def __init__(self, x: int):\n"
        "        self.x = x\n"
        "    def bump(self) -> None:\n"
        "        self.x += 1\n"
    )
    module = _build(src)
    cls = module.body[0]
    bump = [m for m in cls.methods if m.name == "bump"][0]
    assign = bump.body[0]
    assert assign.target_kind == "self_attr"
    assert assign.target == "self.x"
    assert isinstance(assign.value, schema.BinOpExpr)
    assert assign.value.op == "+"


def test_for_over_range_derives_int_type():
    src = "def f(n: int) -> None:\n    for i in range(n):\n        print(i)\n"
    module = _build(src)
    fn = module.body[0]
    loop = fn.body[0]
    assert isinstance(loop, schema.ForStmt)
    assert loop.iter_kind == "range"


def test_for_over_hinted_list_derives_element_type():
    src = "def f(items: list[int]) -> None:\n    for x in items:\n        print(x)\n"
    module = _build(src)  # must not raise -- x derives 'i64' from items' Vec<i64>
    fn = module.body[0]
    loop = fn.body[0]
    assert isinstance(loop, schema.ForStmt)
    assert loop.iter_kind == "sequence"


def test_unsupported_construct_captures_original_source():
    src = "def f() -> None:\n    with open('x') as fh:\n        pass\n"
    module = _build(src)
    fn = module.body[0]
    stmt = fn.body[0]
    assert isinstance(stmt, schema.UnsupportedStmt)
    assert "with open" in stmt.source_text


def test_ir_round_trips_through_disk(tmp_path: Path):
    module = _build(
        "class C:\n    def __init__(self, x: int):\n        self.x = x\n"
        "    def bump(self) -> None:\n        self.x = self.x + 1\n"
    )
    path = tmp_path / "ir" / "t.pyrir.json"
    storage.save_module(module, path)
    loaded = storage.load_module(path)
    assert loaded == module
    # locked read-only, per ARCHITECTURE.md
    assert not (path.stat().st_mode & 0o200)


def test_module_schema_version_is_v2():
    module = _build("def f() -> None:\n    pass\n")
    assert module.schema_version == "v2_ownership"
