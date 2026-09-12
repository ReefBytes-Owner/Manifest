"""Innermost enclosing symbol for a finding's line.

Feeds the identity-based debt ratchet (``src/manifest_agent/checks/debt.py``,
Phase 3 chunk C3): the ratchet's identity never includes a line number
(line numbers churn on unrelated edits), so it needs something else to scope
a finding to the right function/class rather than "the whole file". This
module answers that from data the checker already has -- the parsed AST for
Python, the raw lines for Bash -- so no check body needs to compute it itself.
"""

from __future__ import annotations

import ast
import re

from .source import SourceFile

# Matches a top-level Bash function definition, brace-on-same-line or bare.
_BASH_FUNC_START = re.compile(
    r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{?\s*$"
)


def anchor_for(src: SourceFile, line: int) -> str:
    """The name of the function/class enclosing ``line``, or "" at file level."""
    if src.tree is not None:
        return _python_anchor(src.tree, line)
    if src.language is not None and src.language.key == "shell":
        return _bash_anchor(src.lines, line)
    return ""


def _python_anchor(tree: ast.Module, line: int) -> str:
    """The narrowest def/class node whose span contains ``line``."""
    best_name, best_span = "", None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = node.lineno
        end = getattr(node, "end_lineno", start)
        if start <= line <= end:
            span = end - start
            if best_span is None or span < best_span:
                best_name, best_span = node.name, span
    return best_name


def _bash_anchor(lines: list[str], line: int) -> str:
    """Heuristic: nearest preceding top-level ``name() {`` still open at ``line``.

    Not a full parser -- Bash function nesting is rare in this repository's
    scripts. A function is considered closed at the first line that is
    exactly ``}`` (after stripping indentation). Good enough for a debt
    identity anchor; see ``tests/python/constitution/test_anchor.py``.
    """
    current = ""
    for index, content in enumerate(lines, start=1):
        match = _BASH_FUNC_START.match(content)
        if match:
            current = match.group(1)
        elif content.strip() == "}":
            current = ""
        if index == line:
            return current
    return ""
