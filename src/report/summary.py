"""Stage 6 (part 1): collect a run summary and write ``ambiguities.md``.

Walks the finished IR one more time to gather every marker that ended up
in the generated Rust -- type holes, ambiguities, and unsupported
fragments -- into one scannable report, instead of requiring a grep
through the output file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ir import schema


@dataclass
class RunSummary:
    functions_converted: int = 0
    classes_converted: int = 0
    type_holes: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = ["# Conversion summary", ""]
        lines.append(f"- Functions converted: {self.functions_converted}")
        lines.append(f"- Classes converted: {self.classes_converted}")
        lines.append(f"- Type holes remaining: {len(self.type_holes)}")
        lines.append(f"- Ambiguities flagged: {len(self.ambiguities)}")
        lines.append(f"- Unsupported constructs preserved: {len(self.unsupported)}")
        lines.append("")

        if self.type_holes:
            lines.append("## Type holes")
            lines.extend(f"- {h}" for h in self.type_holes)
            lines.append("")
        if self.ambiguities:
            lines.append("## Ambiguities")
            lines.extend(f"- {a}" for a in self.ambiguities)
            lines.append("")
        if self.unsupported:
            lines.append("## Unsupported constructs (captured, not lost)")
            lines.extend(f"- {u}" for u in self.unsupported)
            lines.append("")

        return "\n".join(lines)


def _walk_type_slot(slot: schema.TypeSlot, context: str, summary: RunSummary) -> None:
    if isinstance(slot, schema.TypeHole):
        info = "; ".join(slot.known_info) if slot.known_info else "no evidence gathered"
        summary.type_holes.append(f"{slot.id} ({context}): {info}")


def _walk_stmt(stmt: schema.Stmt, summary: RunSummary) -> None:
    if isinstance(stmt, schema.AssignStmt):
        if stmt.target_kind == "name":
            _walk_type_slot(stmt.type, f"assignment to '{stmt.target}'", summary)
        for c in stmt.comments.leading + stmt.comments.trailing:
            if c.text.startswith("AMBIGUOUS"):
                summary.ambiguities.append(c.text)
    elif isinstance(stmt, schema.ForStmt):
        for c in stmt.comments.leading:
            if c.text.startswith("AMBIGUOUS"):
                summary.ambiguities.append(c.text)
        for s in stmt.body:
            _walk_stmt(s, summary)
    elif isinstance(stmt, schema.RaiseStmt):
        for c in stmt.comments.leading:
            if c.text.startswith("AMBIGUOUS"):
                summary.ambiguities.append(c.text)
    elif isinstance(stmt, schema.IfStmt):
        for s in stmt.body:
            _walk_stmt(s, summary)
        for s in stmt.orelse:
            _walk_stmt(s, summary)
    elif isinstance(stmt, schema.WhileStmt):
        for s in stmt.body:
            _walk_stmt(s, summary)
    elif isinstance(stmt, schema.UnsupportedStmt):
        summary.unsupported.append(f"{stmt.reason}: {stmt.source_text[:60]!r}")


def _walk_function(fn: schema.FunctionDefNode, summary: RunSummary) -> None:
    summary.functions_converted += 1
    for p in fn.params:
        _walk_type_slot(p.type, f"param '{p.name}' of '{fn.name}'", summary)
    _walk_type_slot(fn.return_type, f"return type of '{fn.name}'", summary)
    if fn.ambiguity is not None:
        summary.ambiguities.append(f"{fn.name}: {fn.ambiguity.rationale}")
    for s in fn.body:
        _walk_stmt(s, summary)


def build_summary(module: schema.ModuleNode) -> RunSummary:
    """Walk a finished module IR and produce a :class:`RunSummary`."""

    summary = RunSummary()
    for top in module.body:
        if isinstance(top, schema.FunctionDefNode):
            _walk_function(top, summary)
        elif isinstance(top, schema.ClassDefNode):
            summary.classes_converted += 1
            if top.ambiguity is not None:
                summary.ambiguities.append(f"{top.name}: {top.ambiguity.rationale}")
            for f in top.fields:
                _walk_type_slot(f.type, f"field '{f.name}' of '{top.name}'", summary)
            for m in top.methods:
                _walk_function(m, summary)
        elif isinstance(top, schema.UnsupportedStmt):
            summary.unsupported.append(f"{top.reason}: {top.source_text[:60]!r}")
    return summary


def write_ambiguities_report(summary: RunSummary, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(summary.to_markdown(), encoding="utf-8")
