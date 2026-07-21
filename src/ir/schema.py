"""Intermediate representation (IR) schema.

This module defines the versioned, serializable data structures that make
up py2rust's intermediate representation. The IR sits between the Python
front end (:mod:`ir.builder`) and the Rust back end
(:mod:`codegen.rust_writer`): it is the artifact that gets written
to disk, locked read-only, and inspected for debugging.

v2 schema changes (Milestone 1)
--------------------------------
* Type hints are now mandatory everywhere preflight requires them (see
  :mod:`preflight.checks`), so there is no more base-type inference and
  no more ``TypeHole``. A type slot is always a resolved
  :class:`ConcreteType`. ``TypeSlot`` is kept as a name (rather than
  deleted outright) purely so downstream modules that spell out
  ``schema.TypeSlot`` in an annotation don't all need touching in the
  same commit; it is simply an alias for ``ConcreteType`` now.
* The "don't guess silently, show your work" idea that ``TypeHole``
  embodied for *types* in v1 is being repurposed for *ownership* in v2
  (see the ROADMAP's Milestone 2). For Milestone 1 this is intentionally
  a bare placeholder -- ``ownership: str | None`` -- with no
  evidence-carrying structure yet. The real ``own``/``ref``/``refmut``/
  ``move`` resolver and its evidence trail land in Milestone 2; wiring a
  richer shape now would mean guessing at a design before the code that
  actually populates it exists.

Design notes (unchanged from v1)
---------------------------------
* Every node kind is a plain :func:`dataclasses.dataclass` with no
  inheritance between node kinds. This keeps serialization simple
  (:func:`dataclasses.asdict` handles any nested dataclass regardless of
  static type) and keeps each class easy to read in isolation.
* A ``kind`` field on every node acts as a tag so a dict loaded back from
  JSON can be dispatched to the right dataclass constructor
  (see :mod:`ir.storage`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

#: Schema version for this IR shape. v2 drops type-hole/inference support
#: and adds the (currently placeholder) ownership field, so it gets its
#: own version rather than silently reusing "v1_core" for a different
#: shape -- see ARCHITECTURE.md's "how it unlocks for later revisions".
SCHEMA_VERSION = "v2_ownership"


@dataclass
class SourceSpan:
    """A location in the original Python source file."""

    file: str
    start_line: int
    end_line: int


@dataclass
class ConcreteType:
    """A fully resolved type, always produced from an explicit type hint.

    v2 requires a hint everywhere preflight checks for one (see
    :mod:`preflight.checks`), so this is never a guess -- there is no
    inference fallback and no unresolved-hole state anymore.
    """

    value: str
    kind: str = "concrete"


#: v1 had a union here (``ConcreteType | TypeHole``) because a type could
#: be legitimately unresolved. v2 has no holes, so every type slot is a
#: ConcreteType. Kept as a separate name rather than replacing every
#: ``schema.TypeSlot`` annotation with ``schema.ConcreteType`` in one
#: sweep across the codebase.
TypeSlot = ConcreteType


@dataclass
class Comment:
    """A single comment, with a confidence score for its attachment."""

    text: str
    confidence: float = 1.0


@dataclass
class Comments:
    """Comments attached to a node: those above it, and same-line trailing."""

    leading: list[Comment] = field(default_factory=list)
    trailing: list[Comment] = field(default_factory=list)


@dataclass
class Ambiguity:
    """Records a judgment call the tool made, so it can be marked visibly.

    Attributes
    ----------
    category:
        A short machine-readable label, e.g. ``"collection-type"``.
    chosen:
        What the tool actually emitted.
    alternatives:
        Other reasonable choices that were not picked -- this list is
        allowed to have a single entry today ("no other option
        implemented yet") without changing the shape of the field, so a
        future revision can add real alternatives without touching the
        schema.
    rationale:
        Short human-readable explanation shown in the generated comment.
    """

    category: str
    chosen: str
    alternatives: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class Param:
    """A function or method parameter.

    ``ownership`` is a Milestone 1 placeholder for the ``#!`` ownership
    directive model landing in Milestone 2 (``"own" | "ref" | "refmut" |
    "move"``, or ``None`` if not yet resolved). It carries no evidence
    structure yet -- that's the resolver's job, not the schema's.
    """

    name: str
    type: TypeSlot
    ownership: "str | None" = None


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


@dataclass
class ConstantExpr:
    """A literal: int, float, str, bool, or None."""

    value: object
    py_type: str  # "int" | "float" | "str" | "bool" | "None"
    kind: str = "constant"


@dataclass
class NameExpr:
    """A bare name reference, e.g. ``x``."""

    name: str
    kind: str = "name"


@dataclass
class BinOpExpr:
    """A binary operator expression, e.g. ``a + b``."""

    op: str
    left: "Expr"
    right: "Expr"
    kind: str = "binop"


@dataclass
class CompareExpr:
    """A comparison, e.g. ``a < b``. Only single comparisons in v1."""

    op: str
    left: "Expr"
    right: "Expr"
    kind: str = "compare"


@dataclass
class BoolOpExpr:
    """A boolean combination, e.g. ``a and b``."""

    op: str  # "and" | "or"
    values: list["Expr"]
    kind: str = "boolop"


@dataclass
class UnaryOpExpr:
    """A unary operator, e.g. ``not x`` or ``-x``."""

    op: str
    operand: "Expr"
    kind: str = "unaryop"


@dataclass
class CallExpr:
    """A function or method call."""

    func: "Expr"
    args: list["Expr"] = field(default_factory=list)
    kind: str = "call"


@dataclass
class AttributeExpr:
    """Attribute access, e.g. ``self.x`` or ``requests.get``."""

    value: "Expr"
    attr: str
    kind: str = "attribute"


@dataclass
class SubscriptExpr:
    """Indexing, e.g. ``items[0]``."""

    value: "Expr"
    index: "Expr"
    kind: str = "subscript"


@dataclass
class ListExpr:
    """A list literal."""

    elements: list["Expr"] = field(default_factory=list)
    kind: str = "list"


@dataclass
class DictExpr:
    """A dict literal."""

    keys: list["Expr"] = field(default_factory=list)
    values: list["Expr"] = field(default_factory=list)
    kind: str = "dict"


Expr = Union[
    ConstantExpr,
    NameExpr,
    BinOpExpr,
    CompareExpr,
    BoolOpExpr,
    UnaryOpExpr,
    CallExpr,
    AttributeExpr,
    SubscriptExpr,
    ListExpr,
    DictExpr,
]


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass
class AssignStmt:
    """An assignment.

    ``target_kind`` distinguishes:

    * ``"name"`` -- a fresh local binding -> Rust ``let`` (``let mut`` if
      ``mutable`` is set because the name is reassigned later).
    * ``"self_attr"`` -- a mutation of an existing struct field -> plain
      ``self.x = ...;``, no ``let``/type, since the field's type was
      already declared on the struct.
    * ``"reassign"`` -- a later assignment to a name already bound earlier
      in the same function (e.g. a loop accumulator) -> plain
      ``x = ...;``, no ``let``/type, since re-declaring would shadow
      rather than mutate. See
      :func:`py2rust.ir.builder.apply_mutability` for how this gets set.
    """

    target: str
    value: "Expr"
    type: TypeSlot
    mutable: bool = False
    target_kind: str = "name"  # "name" | "self_attr" | "reassign"
    comments: Comments = field(default_factory=Comments)
    #: Milestone 1 placeholder -- see :class:`Param`. Assignments are
    #: where the ownership directive model (Milestone 2) also applies.
    ownership: "str | None" = None
    kind: str = "assign"


@dataclass
class ReturnStmt:
    value: "Expr | None"
    comments: Comments = field(default_factory=Comments)
    kind: str = "return"


@dataclass
class ExprStmt:
    """A bare expression used as a statement, e.g. a call for side effects."""

    value: "Expr"
    comments: Comments = field(default_factory=Comments)
    kind: str = "expr_stmt"


@dataclass
class PassStmt:
    comments: Comments = field(default_factory=Comments)
    kind: str = "pass"


@dataclass
class IfStmt:
    test: "Expr"
    body: list["Stmt"]
    orelse: list["Stmt"] = field(default_factory=list)
    comments: Comments = field(default_factory=Comments)
    kind: str = "if"


@dataclass
class WhileStmt:
    test: "Expr"
    body: list["Stmt"]
    comments: Comments = field(default_factory=Comments)
    kind: str = "while"


@dataclass
class ForStmt:
    """A ``for target in iter:`` loop.

    ``iter_kind`` records whether the iterable was recognized as a
    ``range(...)`` call (translated to a Rust range expression) or a
    generic sequence (translated to ``.iter()``) -- this itself is an
    ambiguity-adjacent decision worth keeping explicit rather than
    silently picking one.
    """

    target: str
    iter: "Expr"
    iter_kind: str  # "range" | "sequence"
    body: list["Stmt"]
    comments: Comments = field(default_factory=Comments)
    kind: str = "for"


@dataclass
class RaiseStmt:
    """A ``raise`` statement.

    Rust has no exceptions, so this is always ambiguity-marked in codegen
    (see :mod:`py2rust.codegen.rust_writer`) -- the default translation is
    a ``panic!``, clearly marked as a placeholder for a ``Result``-based
    rewrite.
    """

    message: "Expr | None"
    comments: Comments = field(default_factory=Comments)
    kind: str = "raise"


@dataclass
class UnsupportedStmt:
    """An opaque placeholder for a construct v1 doesn't understand yet.

    Carries the exact original source text and location so a future
    revision can find and re-parse just this fragment without touching
    the rest of the already-converted IR.
    """

    source_text: str
    reason: str
    comments: Comments = field(default_factory=Comments)
    kind: str = "unsupported"


Stmt = Union[
    AssignStmt,
    ReturnStmt,
    ExprStmt,
    PassStmt,
    IfStmt,
    WhileStmt,
    ForStmt,
    RaiseStmt,
    UnsupportedStmt,
]


# ---------------------------------------------------------------------------
# Top-level nodes
# ---------------------------------------------------------------------------


@dataclass
class FunctionDefNode:
    node_id: str
    name: str
    params: list[Param]
    return_type: TypeSlot
    body: list[Stmt]
    source_span: SourceSpan
    comments: Comments = field(default_factory=Comments)
    ambiguity: "Ambiguity | None" = None
    #: Milestone 1 placeholder -- see :class:`Param`. Applies to the
    #: return type's ownership (e.g. does the function return an owned
    #: value or a reference).
    return_ownership: "str | None" = None
    kind: str = "function_def"


@dataclass
class ClassFieldNode:
    """A struct field inferred from a ``self.x = ...`` assignment in ``__init__``."""

    name: str
    type: TypeSlot


@dataclass
class ClassDefNode:
    node_id: str
    name: str
    fields: list[ClassFieldNode]
    methods: list[FunctionDefNode]
    source_span: SourceSpan
    comments: Comments = field(default_factory=Comments)
    ambiguity: "Ambiguity | None" = None
    unsupported_bases: list[str] = field(default_factory=list)
    kind: str = "class_def"


@dataclass
class ImportNode:
    """A recorded ``import`` statement.

    Not translated to Rust directly in v1 -- kept so plugins (e.g. the
    crate-substitution plugin) can recognize which module a later
    ``module.call(...)`` belongs to.
    """

    module: str
    alias: "str | None"
    source_span: SourceSpan
    kind: str = "import"


TopLevel = Union[FunctionDefNode, ClassDefNode, ImportNode, UnsupportedStmt]


@dataclass
class ModuleNode:
    """The root IR node for one source file."""

    schema_version: str
    source_file: str
    body: list[TopLevel] = field(default_factory=list)
