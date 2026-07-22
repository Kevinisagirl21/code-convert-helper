"""The ``#!`` same-line directive grammar (Milestone 2, ``ROADMAP.md`` #2).

A directive is a same-line trailing comment starting with ``#!`` (as
opposed to an ordinary ``#`` comment, which is left completely alone and
flows through the normal :class:`~ir.schema.Comments` pipeline). The
general shape is ``key: value`` so the grammar is forward-compatible with
future directive keys, but the MVP only ever populates one key --
``"ownership"`` -- and additionally accepts a bare-keyword shorthand for
it, since ownership is the only thing implemented so far and spelling
``#! ownership: owner`` everywhere would be needless ceremony:

* Shorthand (most common): ``#! owner``, ``#! refer``, ``#! refer_mut``,
  ``#! move`` -- the bare word is taken as the ``"ownership"`` key's value.
* Explicit form: ``#! ownership: owner`` -- same result, spelled out.
  This is what a future non-ownership directive key would use, e.g. a
  hypothetical ``#! lifetime: 'a``.

Recognized attachment points (see ``ir/builder.py`` for where each is
read from the CST):

* A parameter's trailing comma comment, e.g. ``x: int,  #! owner``.
* A function's ``-> ReturnType:`` line trailing comment (the
  ``IndentedBlock.header`` in libcst terms).
* An assignment statement's trailing comment, e.g. ``x = f()  #! move``.
"""

from __future__ import annotations

from ir import schema

_DIRECTIVE_PREFIX = "#!"

#: The only directive key implemented in the MVP. A bare-keyword
#: shorthand (no explicit ``key:``) is always interpreted against this
#: key, since it's the only one that exists today.
_DEFAULT_KEY = "ownership"


def parse_directive_text(comment_text: str) -> schema.Directive | None:
    """Parse one directive out of a raw comment token's text.

    Returns ``None`` if ``comment_text`` isn't a directive at all (i.e.
    doesn't start with ``#!``) -- callers should fall back to treating it
    as an ordinary comment in that case. An empty directive body (bare
    ``#!`` with nothing after it) is also treated as "not a directive"
    rather than raising, since a malformed/empty directive is more useful
    left as a no-op than a hard parse error over what might just be a
    stray ``#!`` shebang-style comment a user typed out of habit.
    """

    text = comment_text.strip()
    if not text.startswith(_DIRECTIVE_PREFIX):
        return None
    body = text[len(_DIRECTIVE_PREFIX):].strip()
    if not body:
        return None

    if ":" in body:
        key, _, value = body.partition(":")
        key = key.strip()
        value = value.strip()
    else:
        key = _DEFAULT_KEY
        value = body

    if not key or not value:
        return None

    return schema.Directive(directive_key=key, value=value, raw_text=text)


def is_ownership_directive(directive: schema.Directive) -> bool:
    """Whether a parsed directive is (or claims to be) an ownership one."""

    return directive.directive_key == _DEFAULT_KEY


def is_valid_ownership_value(value: str) -> bool:
    """Whether ``value`` is one of the four MVP ownership keywords."""

    return value in schema.OWNERSHIP_VALUES
