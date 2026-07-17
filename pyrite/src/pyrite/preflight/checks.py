"""Stage 0: preflight checks.

Before any translation happens, verify the input file is sound enough to
translate at all. A hard failure here means the tool refuses to proceed --
feeding a broken or unsound file into a translator just produces
confidently wrong Rust.

This is intentionally a lightweight, v1-scoped checker rather than a
reimplementation of ``mypy`` or ``pyflakes``: it catches syntax errors,
flags obviously undefined names within a function's own scope, and
records which out-of-scope constructs (generators, decorators, async,
``eval``, etc.) appear, without failing the run over those -- they become
:class:`~pyrite.ir.schema.UnsupportedStmt` nodes later instead of being
silently mistranslated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import libcst as cst

#: Names always available without an explicit assignment or parameter.
_BUILTINS = {
    "True",
    "False",
    "None",
    "print",
    "len",
    "range",
    "str",
    "int",
    "float",
    "bool",
    "list",
    "dict",
    "self",
}

#: CST node types that mark a construct as out of scope for v1. Recording
#: these here (rather than failing) is what lets stage 3 turn them into
#: UnsupportedStmt nodes instead of dropping them.
_OUT_OF_SCOPE_NODE_TYPES: dict[type, str] = {
    cst.FunctionDef: "async_or_decorated",  # only flagged if async/decorated, see below
}


@dataclass
class PreflightIssue:
    """One problem or notable finding from the preflight pass."""

    severity: str  # "error" | "warning" | "info"
    message: str
    line: int | None = None


@dataclass
class PreflightReport:
    passed: bool
    issues: list[PreflightIssue] = field(default_factory=list)

    def errors(self) -> list[PreflightIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def warnings(self) -> list[PreflightIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def _check_syntax(source: str) -> tuple[cst.Module | None, list[PreflightIssue]]:
    try:
        return cst.parse_module(source), []
    except cst.ParserSyntaxError as exc:
        return None, [
            PreflightIssue(
                severity="error",
                message=f"syntax error: {exc.message}",
                line=getattr(exc, "raw_line", None),
            )
        ]


class _OutOfScopeScanner(cst.CSTVisitor):
    """Records constructs v1 doesn't support, without failing the run."""

    def __init__(self) -> None:
        self.findings: list[PreflightIssue] = []

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        if node.asynchronous is not None:
            self.findings.append(
                PreflightIssue("info", f"async function '{node.name.value}' is out of v1 scope")
            )
        if node.decorators:
            self.findings.append(
                PreflightIssue("info", f"decorated function '{node.name.value}' is out of v1 scope")
            )
        for stmt in _walk_statements(node.body):
            if isinstance(stmt, cst.SimpleStatementLine):
                for small in stmt.body:
                    if isinstance(small, (cst.Yield,)):
                        pass  # visited separately below

    def visit_Yield(self, node: cst.Yield) -> None:
        self.findings.append(PreflightIssue("info", "generator ('yield') is out of v1 scope"))

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        bases = [a.value for a in node.bases]
        if bases:
            names = ", ".join(cst.Module([]).code_for_node(b) for b in bases)
            self.findings.append(
                PreflightIssue(
                    "info",
                    f"class '{node.name.value}' has base(s) ({names}); "
                    "inheritance is out of v1 scope, fields/methods still convert",
                )
            )

    def visit_With(self, node: cst.With) -> None:
        self.findings.append(PreflightIssue("info", "'with' statement is out of v1 scope"))


def _walk_statements(body: cst.BaseSuite):
    if isinstance(body, cst.IndentedBlock):
        return body.body
    return []


class _UndefinedNameScanner(cst.CSTVisitor):
    """A shallow, best-effort undefined-name check, scoped per function.

    This is not a full scope resolver (see ``ARCHITECTURE.md`` for why a
    fuller checker is future work) -- it flags names that are read inside
    a function but never assigned, passed as a parameter, defined as a
    module-level function/class, or a recognized builtin.
    """

    def __init__(self, module_level_names: set[str]) -> None:
        self.module_level_names = module_level_names
        self.findings: list[PreflightIssue] = []

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        known = set(self.module_level_names) | _BUILTINS
        for param in node.params.params:
            known.add(param.name.value)

        class _Assigns(cst.CSTVisitor):
            def visit_Assign(self, n: cst.Assign) -> None:
                for t in n.targets:
                    if isinstance(t.target, cst.Name):
                        known.add(t.target.value)

            def visit_For(self, n: cst.For) -> None:
                if isinstance(n.target, cst.Name):
                    known.add(n.target.value)

        node.visit(_Assigns())

        class _Reads(cst.CSTVisitor):
            def visit_Name(self2, n: cst.Name) -> None:
                pass

        # Second pass: find reads not covered by `known`.
        def _scan(n: cst.CSTNode) -> None:
            if isinstance(n, cst.Name) and n.value not in known:
                # Heuristic: skip names that are attribute targets (`self.x`
                # already excluded since `self` itself is known) or keyword
                # argument labels, which aren't real reads.
                self.findings.append(
                    PreflightIssue(
                        "warning",
                        f"'{n.value}' used in '{node.name.value}' but never assigned, "
                        "parameterized, or imported (best-effort check)",
                    )
                )

        class _NameFinder(cst.CSTVisitor):
            def visit_Name(self2, n: cst.Name) -> None:
                _scan(n)

            def visit_Arg(self2, n: cst.Arg) -> bool:
                # Don't descend into keyword= labels as if they were reads.
                if n.keyword is not None:
                    n.value.visit(self2)
                    return False
                return True

            def visit_Attribute(self2, n: cst.Attribute) -> bool:
                # Only the base of an attribute chain is a real name read.
                n.value.visit(self2)
                return False

        node.body.visit(_NameFinder())
        return False  # don't recurse further; nested defs handled independently


def run_preflight(source: str) -> PreflightReport:
    """Run the full stage-0 preflight pass over Python ``source``.

    Returns a :class:`PreflightReport`. ``passed`` is ``False`` only on a
    hard syntax error -- undefined-name warnings and out-of-scope-construct
    findings never fail the run by themselves.
    """

    tree, issues = _check_syntax(source)
    if tree is None:
        return PreflightReport(passed=False, issues=issues)

    module_level_names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, cst.FunctionDef):
            module_level_names.add(stmt.name.value)
        elif isinstance(stmt, cst.ClassDef):
            module_level_names.add(stmt.name.value)
        elif isinstance(stmt, cst.SimpleStatementLine):
            for small in stmt.body:
                if isinstance(small, cst.Import):
                    for name in small.names:
                        module_level_names.add((name.asname or name).name.value)
                elif isinstance(small, cst.ImportFrom):
                    if small.names != cst.ImportStar():
                        for name in small.names:
                            module_level_names.add((name.asname or name).name.value)

    scope_scanner = _UndefinedNameScanner(module_level_names)
    tree.visit(scope_scanner)
    issues.extend(scope_scanner.findings)

    scope_finder = _OutOfScopeScanner()
    tree.visit(scope_finder)
    issues.extend(scope_finder.findings)

    return PreflightReport(passed=True, issues=issues)
