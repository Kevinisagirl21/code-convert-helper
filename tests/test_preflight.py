"""Tests for preflight.checks -- v2's mandatory-hint hard-rejection.

v1's tests exercised a checker where a missing hint was never fatal.
v2's central Milestone 1 change is that it now is: these tests cover the
strict-everywhere rule, every documented exemption (reassignment,
augmented assignment, for-loops, self.attr passthrough), and the
tuple-unpacking hard-rejection -- alongside the unchanged v1 checks
(syntax errors, undefined names, out-of-scope constructs).
"""

from preflight import checks


def test_syntax_error_fails():
    report = checks.run_preflight("def f(:\n    pass\n")
    assert not report.passed
    assert report.errors()


def test_fully_hinted_source_passes():
    src = (
        "def add(a: int, b: int) -> int:\n"
        "    total: int = a + b\n"
        "    return total\n"
    )
    report = checks.run_preflight(src)
    assert report.passed
    assert not report.errors()


def test_missing_param_hint_hard_rejects():
    report = checks.run_preflight("def f(x) -> int:\n    return x\n")
    assert not report.passed
    messages = [e.message for e in report.errors()]
    assert any("'x'" in m and "type hint" in m for m in messages)


def test_missing_return_hint_hard_rejects():
    report = checks.run_preflight("def f(x: int):\n    return x\n")
    assert not report.passed
    messages = [e.message for e in report.errors()]
    assert any("return type hint" in m for m in messages)


def test_missing_first_assignment_hint_hard_rejects():
    report = checks.run_preflight("def f() -> int:\n    x = 5\n    return x\n")
    assert not report.passed
    messages = [e.message for e in report.errors()]
    assert any("'x'" in m and "type hint" in m for m in messages)


def test_reassignment_without_hint_is_exempt():
    src = "def f() -> int:\n    x: int = 1\n    x = 2\n    return x\n"
    report = checks.run_preflight(src)
    assert report.passed


def test_augmented_assignment_without_hint_is_exempt():
    src = "def f() -> int:\n    x: int = 1\n    x += 2\n    return x\n"
    report = checks.run_preflight(src)
    assert report.passed


def test_augmented_assignment_with_no_prior_hint_hard_rejects():
    src = "def f() -> int:\n    x += 2\n    return x\n"
    report = checks.run_preflight(src)
    assert not report.passed


def test_self_attr_derived_from_hinted_param_is_exempt():
    src = (
        "class C:\n"
        "    def __init__(self, x: int):\n"
        "        self.x = x\n"
    )
    report = checks.run_preflight(src)
    assert report.passed


def test_self_attr_with_no_derivable_source_hard_rejects():
    src = (
        "class C:\n"
        "    def __init__(self):\n"
        "        self.x = 5\n"
    )
    report = checks.run_preflight(src)
    assert not report.passed
    messages = [e.message for e in report.errors()]
    assert any("self.x" in m for m in messages)


def test_init_is_exempt_from_return_hint():
    src = (
        "class C:\n"
        "    def __init__(self, x: int):\n"
        "        self.x = x\n"
    )
    report = checks.run_preflight(src)
    assert report.passed


def test_for_over_range_is_exempt():
    src = "def f(n: int) -> None:\n    for i in range(n):\n        print(i)\n"
    report = checks.run_preflight(src)
    assert report.passed


def test_for_over_hinted_list_is_exempt():
    src = "def f(items: list[int]) -> None:\n    for x in items:\n        print(x)\n"
    report = checks.run_preflight(src)
    assert report.passed


def test_for_over_unknown_iterable_hard_rejects():
    src = "def f() -> None:\n    for x in something_unhinted():\n        print(x)\n"
    report = checks.run_preflight(src)
    assert not report.passed


def test_tuple_unpacking_hard_rejects():
    report = checks.run_preflight("def f() -> int:\n    a, b = 1, 2\n    return a\n")
    assert not report.passed
    messages = [e.message for e in report.errors()]
    assert any("tuple-unpacking" in m for m in messages)


def test_multiple_assignment_targets_hard_rejects():
    report = checks.run_preflight("def f() -> int:\n    x = y = 5\n    return x\n")
    assert not report.passed


def test_flags_genuinely_undefined_name():
    src = "def broken(x: int) -> int:\n    return y + x\n"
    report = checks.run_preflight(src)
    assert report.passed  # a warning, not a hard failure
    messages = [w.message for w in report.warnings()]
    assert any("'y'" in m for m in messages)


def test_flags_out_of_scope_constructs_without_failing():
    src = (
        "async def fetch() -> None:\n"
        "    pass\n"
        "\n"
        "class Dog(Animal):\n"
        "    def speak(self) -> None:\n"
        "        yield 'woof'\n"
    )
    report = checks.run_preflight(src)
    assert report.passed
    infos = [i.message for i in report.issues if i.severity == "info"]
    assert any("async" in m for m in infos)
    assert any("yield" in m or "generator" in m for m in infos)
    assert any("base" in m for m in infos)
