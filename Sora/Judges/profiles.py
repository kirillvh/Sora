"""The two persona profiles the benchmark compares.

ASSIGNMENT.md 7.2: measure the baseline first, change the system, measure
again. A "profile" is the thing we change - the persona card plus whatever the
system prompt wraps around it - held constant across sessions and repeats so
the A/B has exactly one moving part.

Today `baseline/persona.md` and `Sora/Prompts/persona.md` are byte-identical,
so the only live difference is the baseline's "answer thoroughly and
completely" suffix. That is deliberate: it gives the harness a real (if small)
signal to detect before the persona work starts, and it means any later change
to `Sora/Prompts/persona.md` is measured against a benchmark that already
works.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The corrupted card used to manufacture low-scoring personality anchors
# (Sora/Judges/__init__.py: "a corrupted persona.md can be used"). Static and
# deterministic - corrupting the card with an LLM would add a call, a cost and
# a source of run-to-run drift for no gain.
CORRUPTED_PERSONA = """# Assistant Configuration

You are a helpful AI assistant. Describe the assistant's responses in the
third person where possible. Maintain a neutral, professional register at all
times. Do not use slang, exclamation marks, emoji, or terms of address. Do not
express opinions or preferences; present balanced information only. Prefer
complete, formal sentences and summarise rather than react.
"""


class Profile:
    """A persona card plus the system-prompt scaffolding around it."""

    __slots__ = ("name", "persona_path", "suffix", "description")

    def __init__(self, name, persona_path, suffix="", description=""):
        self.name = name
        self.persona_path = pathlib.Path(persona_path)
        self.suffix = suffix
        self.description = description

    @property
    def persona(self) -> str:
        path = self.persona_path
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path.read_text(encoding="utf-8")

    @property
    def system_prompt(self) -> str:
        return self.persona + self.suffix

    def relpath(self) -> str:
        return str(self.persona_path).replace("\\", "/")


def _thorough_suffix() -> str:
    from baseline.agent import THOROUGH_SUFFIX

    return THOROUGH_SUFFIX


PROFILES = {
    "baseline": Profile(
        "baseline", "baseline/persona.md", _thorough_suffix(),
        "The shipped baseline: the character card plus the 'answer thoroughly "
        "and completely' instruction that flattens her.",
    ),
    "sora": Profile(
        "sora", "Sora/Prompts/persona.md", "",
        "Our profile. The card we are free to change; no thoroughness clamp.",
    ),
    # Not a candidate - a control. Used to manufacture the low end of the
    # personality score range for the judge's few-shot anchors.
    "corrupted": Profile(
        "corrupted", "baseline/persona.md", "",
        "Deliberately voiceless control, for synthetic low-score anchors.",
    ),
}
PROFILES["corrupted"].persona_path = pathlib.Path("<synthetic>")


def get(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError:
        raise SystemExit("unknown profile %r (have: %s)"
                         % (name, ", ".join(PROFILES))) from None


def system_prompt_for(name: str) -> str:
    if name == "corrupted":
        return CORRUPTED_PERSONA
    return get(name).system_prompt


def build_agent(name: str, cached_search: bool = True):
    """A SoraAgent wired to one profile. Same loop for every profile - the
    persona is the only variable."""
    from baseline.agent import SoraAgent

    return SoraAgent(cached_search=cached_search, system_prompt=system_prompt_for(name))
