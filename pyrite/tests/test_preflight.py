from pyrite.preflight import checks


def test_valid_source_passes():
    report = checks.run_preflight("def f(x):\n    return x + 1\n")
    assert report.passed
    assert not report.errors()


def test_syntax_error_fails():
    report = checks.run_preflight("def f(:\n    pass\n")
    assert not report.passed
    assert report.errors()


def test_no_false_positive_on_params_and_locals():
    src = (
        "def add(a: int, b: int) -> int:\n"
        "    total = a + b\n"
        "    return total\n"
    )
    report = checks.run_preflight(src)
    assert report.passed
    assert not report.warnings()


def test_flags_genuinely_undefined_name():
    src = "def broken(x):\n    return y + x\n"
    report = checks.run_preflight(src)
    assert report.passed  # a warning, not a hard failure
    messages = [w.message for w in report.warnings()]
    assert any("'y'" in m for m in messages)


def test_flags_out_of_scope_constructs_without_failing():
    src = (
        "async def fetch():\n"
        "    pass\n"
        "\n"
        "class Dog(Animal):\n"
        "    def speak(self):\n"
        "        yield 'woof'\n"
    )
    report = checks.run_preflight(src)
    assert report.passed
    infos = [i.message for i in report.issues if i.severity == "info"]
    assert any("async" in m for m in infos)
    assert any("yield" in m or "generator" in m for m in infos)
    assert any("base" in m for m in infos)


def test_for_loop_target_recognized_as_assignment():
    src = "def f(items):\n    for x in items:\n        print(x)\n"
    report = checks.run_preflight(src)
    assert not report.warnings()
