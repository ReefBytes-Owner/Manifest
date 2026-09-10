"""Innermost-enclosing-symbol anchors used by the debt-identity ratchet (C3)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "configs/claude/scripts"))

from constitution.anchor import anchor_for
from constitution.registry import load
from constitution.source import SourceFile

REGISTRY = load()


def _src(path: str, text: str) -> SourceFile:
    return SourceFile.from_text(Path(path), text, REGISTRY)


def test_python_anchor_is_the_innermost_function():
    text = "def outer():\n    def inner():\n        x = 1\n        return x\n    return inner()\n"
    src = _src("a.py", text)
    assert anchor_for(src, 3) == "inner"


def test_python_anchor_is_the_class_when_outside_any_method():
    text = "class Foo:\n    x = 1\n    def bar(self):\n        return 1\n"
    src = _src("a.py", text)
    assert anchor_for(src, 2) == "Foo"


def test_python_anchor_is_empty_at_file_level():
    text = "x = 1\ny = 2\n"
    src = _src("a.py", text)
    assert anchor_for(src, 1) == ""


def test_bash_anchor_is_the_enclosing_function():
    text = "#!/bin/bash\nfoo() {\n  echo hi\n}\n"
    src = _src("a.sh", text)
    assert anchor_for(src, 3) == "foo"


def test_bash_anchor_is_empty_outside_any_function():
    text = "#!/bin/bash\necho hi\n"
    src = _src("a.sh", text)
    assert anchor_for(src, 2) == ""


def test_bash_anchor_resets_after_the_closing_brace():
    text = "#!/bin/bash\nfoo() {\n  echo hi\n}\necho after\n"
    src = _src("a.sh", text)
    assert anchor_for(src, 5) == ""
