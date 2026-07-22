"""Milestone 2: the ownership decision log.

Every ownership decision made during IR building -- whether sourced from
an explicit ``#!`` directive or from usage-based inference -- is walked
back out of the finished IR here (the same "walk the finished tree"
pattern used by :mod:`report.summary`) and written to two companion
artifacts:

* ``ownership_log.json`` -- machine-readable, one entry per decision.
* ``ownership_log.md`` -- the same information, human-scannable.

Any *inferred* decision (no directive present) gets printed to stdout as
a warning, since it means the tool guessed rather than being told. Any
*conflict* (a directive that disagrees with what usage-based inference
would have chosen) is printed too, regardless of source, since a
disagreement is never silently dropped -- see ``PROJECT_OVERVIEW.md``'s
"never guess silently" principle. Under ``--warnings-as-fatal`` the
caller (see :mod:`pipeline`) turns these same messages into a hard
failure instead of just a printed warning.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ir import schema


@dataclass
class OwnershipLogEntry:
    context: str
    value: str
    source: str  # "directive" | "inferred"
    evidence: list[str] = field(default_factory=list)
    conflict: str | None = None
    directive_text: str | None = None


@dataclass
class OwnershipLog:
    entries: list[OwnershipLogEntry] = field(default_factory=list)

    def inferred_entries(self) -> list[OwnershipLogEntry]:
        return [e for e in self.entries if e.source == "inferred"]

    def conflicts(self) -> list[OwnershipLogEntry]:
        return [e for e in self.entries if e.conflict is not None]

    def to_markdown(self) -> str:
        lines = ["# Ownership decision log", ""]
        lines.append(f"- Total decisions: {len(self.entries)}")
        lines.append(
            f"- From explicit `#!` directives: "
            f"{sum(1 for e in self.entries if e.source == 'directive')}"
        )
        lines.append(f"- Inferred (no directive present): {len(self.inferred_entries())}")
        lines.append(f"- Conflicts (directive disagreed with inference): {len(self.conflicts())}")
        lines.append("")
        if self.entries:
            lines.append("## All decisions")
            for e in self.entries:
                lines.append(f"- **{e.context}** -> `{e.value}` ({e.source})")
                if e.directive_text:
                    lines.append(f"  - directive: `{e.directive_text}`")
                if e.evidence:
                    lines.append(f"  - evidence: {'; '.join(e.evidence)}")
                if e.conflict:
                    lines.append(f"  - CONFLICT: {e.conflict}")
            lines.append("")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps([asdict(e) for e in self.entries], indent=2)


def _record(
    entries: list[OwnershipLogEntry], context: str, decision: "schema.OwnershipDecision | None"
) -> None:
    if decision is None:
        return
    entries.append(
        OwnershipLogEntry(
            context=context,
            value=decision.value,
            source=decision.source,
            evidence=list(decision.evidence),
            conflict=decision.conflict,
            directive_text=decision.directive.raw_text if decision.directive else None,
        )
    )


def _walk_stmt(stmt: schema.Stmt, entries: list[OwnershipLogEntry], scope: str) -> None:
    if isinstance(stmt, schema.AssignStmt):
        _record(entries, f"{scope}: assignment to '{stmt.target}'", stmt.ownership)
    elif isinstance(stmt, schema.IfStmt):
        for s in stmt.body:
            _walk_stmt(s, entries, scope)
        for s in stmt.orelse:
            _walk_stmt(s, entries, scope)
    elif isinstance(stmt, (schema.WhileStmt, schema.ForStmt)):
        for s in stmt.body:
            _walk_stmt(s, entries, scope)


def _walk_function(fn: schema.FunctionDefNode, entries: list[OwnershipLogEntry]) -> None:
    for p in fn.params:
        _record(entries, f"'{fn.name}' param '{p.name}'", p.ownership)
    _record(entries, f"'{fn.name}' return value", fn.return_ownership)
    for s in fn.body:
        _walk_stmt(s, entries, f"'{fn.name}'")


def build_ownership_log(module: schema.ModuleNode) -> OwnershipLog:
    """Walk a finished module IR and collect every ownership decision made."""

    entries: list[OwnershipLogEntry] = []
    for top in module.body:
        if isinstance(top, schema.FunctionDefNode):
            _walk_function(top, entries)
        elif isinstance(top, schema.ClassDefNode):
            for m in top.methods:
                _walk_function(m, entries)
    return OwnershipLog(entries=entries)


def print_ownership_warnings(log: OwnershipLog) -> list[str]:
    """Print a stdout warning for every inferred decision and every
    directive/inference conflict.

    Returns the printed messages so a caller (e.g. under
    ``--warnings-as-fatal``) can turn them into a hard failure instead of
    just a printed warning, without having to re-walk the log itself.
    """

    messages: list[str] = []
    for entry in log.entries:
        if entry.source == "inferred":
            msg = (
                f"[code-convert-helper] no ownership directive for {entry.context}; inferred '{entry.value}'"
                + (f" ({'; '.join(entry.evidence)})" if entry.evidence else "")
            )
            print(msg)
            messages.append(msg)
        if entry.conflict is not None:
            msg = f"[code-convert-helper] ownership conflict at {entry.context}: {entry.conflict}"
            print(msg)
            messages.append(msg)
    return messages


def write_ownership_log(log: OwnershipLog, output_dir: Path) -> None:
    """Write ``ownership_log.json`` and ``ownership_log.md`` to ``output_dir``."""

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "ownership_log.json").write_text(log.to_json(), encoding="utf-8")
    (output_dir / "ownership_log.md").write_text(log.to_markdown(), encoding="utf-8")
