"""Judge prompts live in .md files, not in Python.

One file per axis under Sora/Prompts/, each with `{{PLACEHOLDER}}` slots:

    {{PERSONA}}   the character card being imitated (personality judge only)
    {{EXAMPLES}}  human-labelled few-shot block, or nothing
    {{USER}}      the user turn being graded
    {{REPLY}}     Sora's reply being graded

Every template must render correctly with `{{EXAMPLES}}` empty - a cold pool
with no human labels yet is the normal starting state, and a judge that only
works once somebody has labelled 20 turns is a judge nobody will ever run.
`render()` enforces that by leaving no placeholder behind and collapsing the
blank line an empty block would leave.
"""
from __future__ import annotations

import pathlib
import re

AXES = ("personality", "novelty", "initiative")

PROMPT_DIR = pathlib.Path(__file__).resolve().parents[1] / "Prompts"
_PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def path_for(axis: str) -> pathlib.Path:
    if axis not in AXES:
        raise SystemExit("unknown axis %r (have: %s)" % (axis, ", ".join(AXES)))
    return PROMPT_DIR / ("%s.md" % axis)


def load(axis: str) -> str:
    path = path_for(axis)
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise SystemExit("judge template %s is empty - the rubric lives there" % path)
    return text


def placeholders(axis: str) -> set:
    return set(_PLACEHOLDER.findall(load(axis)))


def render(axis: str, **values) -> str:
    """Fill a template. Unknown placeholders become empty strings, and any
    placeholder the caller did not supply is reported rather than shipped to
    the model as literal `{{REPLY}}`."""
    text = load(axis)
    missing = []

    def sub(match):
        key = match.group(1)
        if key not in values:
            missing.append(key)
            return ""
        return str(values[key] if values[key] is not None else "")

    out = _PLACEHOLDER.sub(sub, text)
    if missing:
        raise SystemExit("template %s wants %s, which render() was not given"
                         % (path_for(axis).name, ", ".join(sorted(set(missing)))))
    return re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"


def relpaths() -> dict:
    """Template paths for the report header, so a reader can see exactly which
    rubric produced the numbers."""
    root = PROMPT_DIR.resolve().parents[1]
    return {axis: str(path_for(axis).resolve().relative_to(root)).replace("\\", "/")
            for axis in AXES}
