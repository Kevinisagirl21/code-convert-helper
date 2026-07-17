import json

from pyrite.ir import builder
from pyrite.plugins import crate_substitution
from pyrite.plugins.protocol import PluginRequest, PluginSuggestion, run_external_plugin


def test_curated_suggestion_lookup():
    suggestion = crate_substitution.suggest_crate("requests", "get")
    assert suggestion is not None
    assert suggestion.confidence == "curated"
    assert "reqwest" in suggestion.summary


def test_unknown_call_has_no_suggestion():
    assert crate_substitution.suggest_crate("os", "some_unmapped_call") is None


def test_annotate_crate_suggestions_marks_but_does_not_rewrite():
    src = "import requests\n\ndef f(url):\n    response = requests.get(url)\n    return response\n"
    module = builder.build_module_ir(src, "t.py")
    crate_substitution.annotate_crate_suggestions(module)
    fn = [n for n in module.body if n.kind == "function_def"][0]
    assign = fn.body[0]
    texts = [c.text for c in assign.comments.leading]
    assert any("SUGGESTED CRATE" in t for t in texts)
    # never rewritten -- the call itself is untouched
    assert assign.value.func.attr == "get"


def test_external_plugin_protocol_round_trip(tmp_path):
    plugin_path = tmp_path / "echo_plugin.py"
    plugin_path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "json.load(sys.stdin)  # request is read but this stub ignores its content\n"
        "print(json.dumps({'suggestion': {'summary': 'ok', 'detail': '', 'confidence': 'heuristic'}}))\n"
    )
    plugin_path.chmod(0o755)

    request = PluginRequest(hook="library_substitution", context={"call": "foo.bar"})
    result = run_external_plugin(str(plugin_path), request)

    assert result is not None
    assert result.summary == "ok"
    assert result.confidence == "heuristic"


def test_external_plugin_failure_is_swallowed_not_raised():
    request = PluginRequest(hook="library_substitution", context={})
    result = run_external_plugin("/no/such/executable", request)
    assert result is None
