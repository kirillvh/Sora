"""Web search tool.

Live mode uses DuckDuckGo (no API key needed). Cached mode serves frozen
results from fixtures/search/ so eval runs are deterministic, reproducible
across candidates, and free.

Use cached=True (or --cached) for ALL eval runs. The fixtures are synthetic
snapshots — they are not guaranteed to match the live web, and that is fine.

CLI:
    python -m tools.search "themed cafes taipei" --cached
"""
import argparse
import json
import pathlib
import re
import sys

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "search"


def _tokens(text: str):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _load_fixtures():
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(FIXTURE_DIR.glob("*.json"))
    ]


def search(query: str, cached: bool = False, max_results: int = 5):
    """Returns a list of {title, url, content} dicts."""
    if cached:
        fixtures = _load_fixtures()
        if not fixtures:
            raise RuntimeError(f"No search fixtures found in {FIXTURE_DIR}")
        qt = _tokens(query)
        # Nearest fixture by token overlap (Jaccard). Every query gets an
        # answer, which mirrors real search: you always get *something*.
        best = max(
            fixtures,
            key=lambda f: len(qt & _tokens(f["query"])) / (len(qt | _tokens(f["query"])) or 1),
        )
        return best["results"][:max_results]

    try:
        from ddgs import DDGS  # current package name
    except ImportError:
        from duckduckgo_search import DDGS  # older package name

    with DDGS() as ddgs:
        hits = list(ddgs.text(query, max_results=max_results))
    return [
        {
            "title": h.get("title", ""),
            "url": h.get("href", h.get("url", "")),
            "content": h.get("body", ""),
        }
        for h in hits
    ]


def main():
    for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp932/cp950
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Web search tool.")
    parser.add_argument("query")
    parser.add_argument("--cached", action="store_true")
    parser.add_argument("--max-results", type=int, default=5)
    args = parser.parse_args()
    results = search(args.query, cached=args.cached, max_results=args.max_results)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
