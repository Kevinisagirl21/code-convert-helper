"""Stage 4: ambiguity marking.

Where more than one reasonable Rust translation exists for a Python
pattern, this module records the choice as an :class:`~ir.schema.Ambiguity`
attached to the relevant IR node -- never a silent pick. In this
prototype only one option is actually implemented for each category, but
the ``alternatives`` list documents what a future revision would add, and
nothing about the shape of the marker needs to change when that happens.

No part of this module reads from an external config file: which
*translation* gets chosen is never configurable, by design (see
``PROJECT_OVERVIEW.md``, principle 2).
"""

from __future__ import annotations

from ir import schema


def mark_collection_type(concrete: schema.ConcreteType) -> schema.Ambiguity | None:
    """Attach an ambiguity marker to a collection type, if applicable.

    Returns ``None`` for non-collection concrete types (nothing to mark).
    """

    if concrete.value.startswith("Vec<"):
        return schema.Ambiguity(
            category="collection-type",
            chosen=concrete.value,
            alternatives=["a fixed-size array", "a borrowed slice (&[T])"],
            rationale=(
                "Vec<T> is the safe default for a Python list; switch to an "
                "array or slice if the size is fixed or ownership isn't needed."
            ),
        )
    if concrete.value.startswith("HashMap<"):
        return schema.Ambiguity(
            category="collection-type",
            chosen=concrete.value,
            alternatives=["BTreeMap<K, V> (if insertion/sort order matters)"],
            rationale=(
                "HashMap is the safe default for a Python dict; switch to "
                "BTreeMap if you rely on key ordering."
            ),
        )
    return None


def mark_class_shape(class_name: str) -> schema.Ambiguity:
    """Ambiguity marker for the struct-vs-trait-object decision on a class.

    v1 always emits a plain struct + impl block; the trait-object
    alternative is recorded so a human (or a future revision that can see
    polymorphic usage across the file) can reconsider it.
    """

    return schema.Ambiguity(
        category="class-shape",
        chosen="struct + impl",
        alternatives=["a trait + trait object (dyn Trait), if used polymorphically"],
        rationale=(
            f"'{class_name}' was translated as a plain struct; reconsider a "
            "trait object if it's used polymorphically elsewhere in the project."
        ),
    )


def mark_raise(message_hint: str) -> schema.Ambiguity:
    """Ambiguity marker for a ``raise`` statement.

    Rust has no exceptions, so this is always marked -- ``panic!`` is a
    safe, obvious-to-grep default, never a silent stand-in for proper
    ``Result``-based error handling.
    """

    return schema.Ambiguity(
        category="error-handling",
        chosen="panic!(...)",
        alternatives=["returning Result<T, E> and propagating with '?'"],
        rationale=(
            f"Python 'raise {message_hint}' was translated as a panic; "
            "consider a Result-based rewrite for recoverable errors."
        ),
    )


def mark_for_loop(iter_kind: str) -> schema.Ambiguity | None:
    """Ambiguity marker for how a ``for`` loop's iterable was interpreted."""

    if iter_kind == "sequence":
        return schema.Ambiguity(
            category="iteration-style",
            chosen=".iter()",
            alternatives=[".into_iter() (if ownership of elements should move)"],
            rationale=(
                "Iterating by shared reference is the safe default; switch "
                "to into_iter() if the loop body needs to own each element."
            ),
        )
    return None
