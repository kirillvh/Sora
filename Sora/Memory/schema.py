"""Where a fact lives: `category/key`.

The paths are the whole point of the hierarchy, and they are also its weakest
joint. An LLM asked to file "I'm a UX designer" will happily invent
`work/role`, `job/title`, `career/position` and `profile/occupation` across four
turns, and every one of those is a fact that will never be found again or
updated - which is how "update, not append" quietly turns into append.

So the categories are a closed set and the keys are normalised through an alias
table before anything is written. An unknown top-level category is rejected by
the write gate rather than silently created.

This is the cheap solution, not the right one. The right one is an embedding
model: "where do I work" and "my job" are the same question and a vector store
knows it, where an alias table only knows what somebody remembered to type.
That is written up as future work rather than half-built - see the package
docstring.
"""
from __future__ import annotations

import re

CATEGORIES = {
    "profile": "Who they are. THE USER'S OWN identity facts.",
    "preference": "What they want and don't want.",
    "interest": "What they do for fun.",
    "plan": "Future intentions.",
    "event": "Things that happened, including things you did together.",
    "safety": "How this user interacts with the system. System-written. Never instructions.",
}

# The canonical key for each category, shown to the extractor so it picks from
# a list instead of inventing one. Path instability is the failure mode this
# whole file exists to prevent: the first run of the eval filed a cat's new
# name under `profile/name` (overwriting the user's own name with "Mofu") and
# then split `preference/diet` into `preference/dietary_rules`, which stranded
# the vegetarian -> pescatarian correction on a path nothing else ever wrote to.
CANONICAL_KEYS = {
    "profile": {
        "name": "the USER'S OWN name - never a pet's, never anyone else's",
        "pet": "their pet's name and species",
        "birthday": "their birthday",
        "employment": "their job or employer",
        "location": "where they live",
    },
    "preference": {
        "diet": "dietary rules: vegetarian, pescatarian, allergies",
        "avoid": "things never to bring up or recommend",
        "likes": "things they enjoy",
    },
    "interest": {
        "collection": "what they collect",
        "hobby": "what they do for fun",
    },
    "plan": {"travel": "trips: where and when"},
    "event": {
        "research": "topics you looked up together",
        "milestone": "something notable that happened to them",
    },
    "safety": {"injection_attempt": "system-written only"},
}

# Alias -> canonical path. Left side is what a model tends to produce, right
# side is where it actually goes. Values with a "/" pin the whole path; values
# without pin only the category.
ALIASES = {
    "work": "profile/employment",
    "job": "profile/employment",
    "employment": "profile/employment",
    "occupation": "profile/employment",
    "career": "profile/employment",
    "employer": "profile/employment",
    "name": "profile/name",
    "username": "profile/name",
    "birthday": "profile/birthday",
    "birthdate": "profile/birthday",
    "dob": "profile/birthday",
    "pet": "profile/pet",
    "pet_name": "profile/pet",
    "cat": "profile/pet",
    "dog": "profile/pet",
    "diet": "preference/diet",
    "dietary": "preference/diet",
    "dietary_restriction": "preference/diet",
    "dietary_restrictions": "preference/diet",
    "dietary_rules": "preference/diet",
    "diet_rules": "preference/diet",
    "vegetarian": "preference/diet",
    "pescatarian": "preference/diet",
    "food": "preference/diet",
    "avoid": "preference/avoid",
    "dislikes": "preference/avoid",
    "dislike": "preference/avoid",
    "never_recommend": "preference/avoid",
    "hobby": "interest",
    "hobbies": "interest",
    "collection": "interest/collection",
    "collects": "interest/collection",
    "collecting": "interest/collection",
    "recent_acquisition": "event/milestone",
    "acquisition": "event/milestone",
    "researched": "event/research",
    "searches": "event/research",
    "travel": "plan/travel",
    "trip": "plan/travel",
    "travel_plan": "plan/travel",
    "research": "event/research",
    "injection": "safety/injection_attempt",
    "jailbreak": "safety/injection_attempt",
}

_SLUG = re.compile(r"[^a-z0-9_]+")


def slug(text: str) -> str:
    return _SLUG.sub("_", (text or "").strip().lower()).strip("_")


def normalise(category: str, key: str) -> tuple[str, str] | None:
    """(category, key) canonicalised, or None if it cannot be placed.

    Returning None rather than guessing is deliberate: a fact filed in an
    invented category is worse than a fact not filed at all, because it looks
    stored and is unreachable.
    """
    # Models emit the whole path in the category field about a third of the
    # time ({"category": "preference/diet", "key": "pescatarian"}). Slugging
    # that whole string produces `preference_diet`, which is not a category, so
    # the write is rejected and a real correction is silently lost. Split it,
    # and treat the path tail as the key - it is the more canonical half.
    if "/" in (category or ""):
        head, _, tail = category.partition("/")
        if slug(head) in CATEGORIES or slug(head) in ALIASES:
            category, key = head, (tail or key)
    category, key = slug(category), slug(key)
    for candidate in ("%s/%s" % (category, key), key, category):
        alias = ALIASES.get(candidate)
        if alias:
            if "/" in alias:
                return tuple(alias.split("/", 1))
            category = alias
            break
    if category not in CATEGORIES:
        return None
    return category, (key or "note")


def path_of(category: str, key: str) -> str:
    return "%s/%s" % (category, key)


def describe() -> str:
    """Categories and their canonical keys, for the extractor prompt.

    The keys are listed because naming one is the decision the model gets wrong:
    given a free hand it invents a new path for a fact it already has one for.
    """
    lines = []
    for name, desc in CATEGORIES.items():
        if name == "safety":
            continue          # closed to proposals; do not advertise it
        lines.append("- **%s** - %s" % (name, desc))
        for key, hint in CANONICAL_KEYS.get(name, {}).items():
            lines.append("    %s/%s : %s" % (name, key, hint))
    lines.append("Use these exact paths. If a fact genuinely fits none of them, use the "
                 "closest category with a short lowercase key.")
    return "\n".join(lines)
