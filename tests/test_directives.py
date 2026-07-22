from directives import parser


def test_bare_ownership_shorthand():
    d = parser.parse_directive_text("#! owner")
    assert d is not None
    assert d.directive_key == "ownership"
    assert d.value == "owner"
    assert d.raw_text == "#! owner"


def test_explicit_key_value_form():
    d = parser.parse_directive_text("#! ownership: refer")
    assert d is not None
    assert d.directive_key == "ownership"
    assert d.value == "refer"


def test_ordinary_comment_is_not_a_directive():
    assert parser.parse_directive_text("# just a note") is None


def test_bare_shebang_like_comment_is_not_a_directive():
    assert parser.parse_directive_text("#!") is None
    assert parser.parse_directive_text("#!   ") is None


def test_whitespace_is_tolerated():
    d = parser.parse_directive_text("  #!   refer_mut  ")
    assert d is not None
    assert d.value == "refer_mut"


def test_is_valid_ownership_value():
    assert parser.is_valid_ownership_value("owner")
    assert parser.is_valid_ownership_value("refer")
    assert parser.is_valid_ownership_value("refer_mut")
    assert parser.is_valid_ownership_value("move")
    assert not parser.is_valid_ownership_value("own")
    assert not parser.is_valid_ownership_value("ref")


def test_is_ownership_directive():
    d = parser.parse_directive_text("#! owner")
    assert parser.is_ownership_directive(d)
