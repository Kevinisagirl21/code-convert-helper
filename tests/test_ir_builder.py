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


def test_unannotated_param_is_a_hole():
    module = _build("def f(x):\n    return x\n")
    fn = module.body[0]
    assert isinstance(fn.params[0].type, schema.TypeHole)


def test_comment_attaches_to_the_statement_it_describes():
    src = "def f(x: int) -> int:\n    # add one\n    return x + 1\n"
    module = _build(src)
    fn = module.body[0]
    ret_stmt = fn.body[0]
    assert isinstance(ret_stmt, schema.ReturnStmt)
    assert ret_stmt.comments.leading[0].text == "# add one"


def test_trailing_comment_attaches_to_same_line():
    src = "def f():\n    x = 1  # start here\n"
    module = _build(src)
    stmt = module.body[0].body[0]
    assert stmt.comments.trailing[0].text == "# start here"


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
    src = "class C:\n    def __init__(self, x: int):\n        self.x = x\n\n    def get(self):\n        return self.x\n"
    module = _build(src)
    cls = module.body[0]
    get_method = [m for m in cls.methods if m.name == "get"][0]
    assert get_method.params == []


def test_accumulator_pattern_marks_mutability_and_reassignment():
    src = "def total(n: int) -> int:\n    t = 0\n    for i in range(n):\n        t = t + i\n    return t\n"
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
        "    def bump(self):\n"
        "        self.x = self.x + 1\n"
    )
    module = _build(src)
    cls = module.body[0]
    bump = [m for m in cls.methods if m.name == "bump"][0]
    assign = bump.body[0]
    assert assign.target_kind == "self_attr"
    assert assign.target == "self.x"


def test_unsupported_construct_captures_original_source():
    src = "def f():\n    with open('x') as fh:\n        pass\n"
    module = _build(src)
    fn = module.body[0]
    stmt = fn.body[0]
    assert isinstance(stmt, schema.UnsupportedStmt)
    assert "with open" in stmt.source_text


def test_ir_round_trips_through_disk(tmp_path: Path):
    module = _build(
        "class C:\n    def __init__(self, x: int):\n        self.x = x\n"
        "    def bump(self):\n        self.x = self.x + 1\n"
    )
    path = tmp_path / "ir" / "t.pyrir.json"
    storage.save_module(module, path)
    loaded = storage.load_module(path)
    assert loaded == module
    assert not (path.stat().st_mode & 0o200)


def test_param_directive_is_recognized_via_trailing_comma_comment():
    src = (
        "def f(\n"
        "    name: str,  #! owner\n"
        ") -> str:\n"
        "    return name\n"
    )
    module = _build(src)
    fn = module.body[0]
    param = fn.params[0]
    assert param.ownership is not None
    assert param.ownership.source == "directive"
    assert param.ownership.value == "owner"
    assert param.ownership.directive.raw_text == "#! owner"


def test_param_without_directive_falls_back_to_inference():
    src = "def f(name: str):\n    print(name)\n"
    module = _build(src)
    fn = module.body[0]
    param = fn.params[0]
    assert param.ownership is not None
    assert param.ownership.source == "inferred"
    assert param.ownership.value == "refer"
    assert param.ownership.evidence


def test_copy_primitive_param_infers_owner_regardless_of_usage():
    src = "def f(n: int):\n    print(n)\n"
    module = _build(src)
    fn = module.body[0]
    assert fn.params[0].ownership.value == "owner"
    assert fn.params[0].ownership.source == "inferred"


def test_param_returned_directly_infers_move():
    src = "def f(name: str):\n    return name\n"
    module = _build(src)
    fn = module.body[0]
    assert fn.params[0].ownership.value == "move"


def test_param_stored_into_self_attr_infers_move():
    src = (
        "class C:\n"
        "    def __init__(self):\n"
        "        self.name = ''\n"
        "    def set_name(self, name: str):\n"
        "        self.name = name\n"
    )
    module = _build(src)
    cls = module.body[0]
    set_name = [m for m in cls.methods if m.name == "set_name"][0]
    assert set_name.params[0].ownership.value == "move"


def test_directive_conflicting_with_inference_is_recorded_not_dropped():
    src = (
        "def f(\n"
        "    name: str,  #! refer_mut\n"
        ") -> int:\n"
        "    return 1\n"
    )
    module = _build(src)
    fn = module.body[0]
    param = fn.params[0]
    assert param.ownership.source == "directive"
    assert param.ownership.value == "refer_mut"
    assert param.ownership.conflict is not None
    assert "refer_mut" in param.ownership.conflict


def test_unrecognized_directive_keyword_is_a_conflict():
    src = (
        "def f(\n"
        "    name: str,  #! own\n"
        ") -> str:\n"
        "    return name\n"
    )
    module = _build(src)
    param = module.body[0].params[0]
    assert param.ownership.source == "directive"
    assert param.ownership.value == "own"
    assert param.ownership.conflict is not None


def test_return_type_directive_recognized_from_header_comment():
    src = "def f(n: int) -> int:  #! owner\n    return n\n"
    module = _build(src)
    fn = module.body[0]
    assert fn.return_ownership.source == "directive"
    assert fn.return_ownership.value == "owner"


def test_assignment_directive_recognized_and_stripped_from_comments():
    src = "def f():\n    x = compute()  #! move\n"
    module = _build(src)
    fn = module.body[0]
    assign = fn.body[0]
    assert assign.ownership.source == "directive"
    assert assign.ownership.value == "move"
    assert assign.comments.trailing == []


def test_plain_comment_is_not_mistaken_for_a_directive():
    src = "def f():\n    x = 1  # just a note\n"
    module = _build(src)
    assign = module.body[0].body[0]
    assert assign.ownership.source == "inferred"
    assert assign.comments.trailing[0].text == "# just a note"


def test_ownership_round_trips_through_disk(tmp_path: Path):
    src = (
        "def f(\n"
        "    name: str,  #! owner\n"
        ") -> str:\n"
        "    return name\n"
    )
    module = _build(src)
    path = tmp_path / "ir" / "t.pyrir.json"
    storage.save_module(module, path)
    loaded = storage.load_module(path)
    assert loaded == module
    assert loaded.body[0].params[0].ownership.value == "owner"
