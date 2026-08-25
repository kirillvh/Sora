"""Small statistics, written out rather than imported.

numpy/scipy would be one line each, but the whole repo currently installs in
seconds and every number below is auditable by eye. If this grows past
bootstrap CIs, take the dependency.

Two ideas do real work here:

**Spread, reported always.** The agent is nondeterministic, so a single
benchmark number is a sample, not a measurement. Every metric carries mean,
sample standard deviation and n, and every profile comparison carries a
confidence interval on the difference. With the default 3 repeats that
interval is wide, and it is meant to be: it is the honest statement of what 3
runs can tell you.

**Quadratic weighted kappa for judge agreement.** Raw agreement flatters a
judge on a 1-5 scale where most replies are 3s and 4s - two graders who both
guess "4" every time agree 70% of the time and have learnt nothing. Kappa
corrects for chance, and the quadratic weighting means a 5-vs-1 disagreement
counts far worse than 4-vs-5, which matches how much we care.
"""
from __future__ import annotations

import math

# Two-sided 95% t critical values, df 1..30. Beyond that, 1.96 is close enough.
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
        14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
        20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
        26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}


def t95(df: int) -> float:
    if df <= 0:
        return float("nan")
    return _T95.get(df, 1.96)


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def stdev(values):
    """Sample standard deviation. None for n < 2 - with one run there is no
    spread to report, and reporting 0.0 would be a lie."""
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    mu = sum(values) / len(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / (len(values) - 1))


def summarise(values) -> dict:
    values = [v for v in values if v is not None]
    sd = stdev(values)
    n = len(values)
    return {
        "mean": mean(values),
        "sd": sd,
        "n": n,
        "sem": (sd / math.sqrt(n)) if sd is not None and n else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def difference(a_values, b_values) -> dict:
    """b - a, with a Welch 95% interval. `significant` is False whenever the
    interval spans zero, which at n=3 is most of the time."""
    a = [v for v in a_values if v is not None]
    b = [v for v in b_values if v is not None]
    out = {"delta": None, "ci95": None, "significant": None, "n_a": len(a), "n_b": len(b)}
    if not a or not b:
        return out
    out["delta"] = mean(b) - mean(a)
    sa, sb = stdev(a), stdev(b)
    if sa is None or sb is None or len(a) < 2 or len(b) < 2:
        out["note"] = "need >= 2 runs per side for an interval"
        return out
    va, vb = sa ** 2 / len(a), sb ** 2 / len(b)
    se = math.sqrt(va + vb)
    if se == 0:
        out.update(ci95=(out["delta"], out["delta"]), significant=out["delta"] != 0)
        return out
    df = (va + vb) ** 2 / ((va ** 2 / (len(a) - 1)) + (vb ** 2 / (len(b) - 1)))
    half = t95(int(df)) * se
    out["ci95"] = (out["delta"] - half, out["delta"] + half)
    out["significant"] = (out["ci95"][0] > 0) or (out["ci95"][1] < 0)
    out["se"] = se
    out["df"] = df
    return out


def pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 2:
        return None
    mx = mean([p[0] for p in pairs])
    my = mean([p[1] for p in pairs])
    num = sum((x - mx) * (y - my) for x, y in pairs)
    dx = math.sqrt(sum((x - mx) ** 2 for x, _ in pairs))
    dy = math.sqrt(sum((y - my) ** 2 for _, y in pairs))
    return num / (dx * dy) if dx and dy else None


def quadratic_weighted_kappa(human, judge, min_score=1, max_score=5):
    """Cohen's kappa with quadratic weights, over the fixed 1-5 rating scale."""
    pairs = [(int(h), int(j)) for h, j in zip(human, judge)
             if h is not None and j is not None]
    if not pairs:
        return None
    labels = list(range(min_score, max_score + 1))
    index = {label: i for i, label in enumerate(labels)}
    k = len(labels)

    observed = [[0] * k for _ in range(k)]
    for h, j in pairs:
        if h in index and j in index:
            observed[index[h]][index[j]] += 1

    hist_h = [sum(row) for row in observed]
    hist_j = [sum(observed[r][c] for r in range(k)) for c in range(k)]
    n = len(pairs)
    if n == 0:
        return None

    denom_w = (k - 1) ** 2
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            weight = ((i - j) ** 2) / denom_w
            expected = hist_h[i] * hist_j[j] / n
            num += weight * observed[i][j]
            den += weight * expected
    if den == 0:
        return 1.0 if num == 0 else 0.0
    return 1.0 - num / den


def agreement(human, judge) -> dict:
    """Everything we report about one axis of human-vs-judge agreement."""
    pairs = [(h, j) for h, j in zip(human, judge) if h is not None and j is not None]
    n = len(pairs)
    if not n:
        return {"n": 0}
    exact = sum(1 for h, j in pairs if h == j)
    within1 = sum(1 for h, j in pairs if abs(h - j) <= 1)
    deltas = [j - h for h, j in pairs]
    return {
        "n": n,
        "exact_pct": 100.0 * exact / n,
        "within1_pct": 100.0 * within1 / n,
        "mae": sum(abs(d) for d in deltas) / n,
        "bias": sum(deltas) / n,          # >0: the judge is more generous than the human
        "kappa_qw": quadratic_weighted_kappa([h for h, _ in pairs], [j for _, j in pairs]),
        "pearson_r": pearson([h for h, _ in pairs], [j for _, j in pairs]),
        "human_mean": mean([h for h, _ in pairs]),
        "judge_mean": mean([j for _, j in pairs]),
        "worst": max(pairs, key=lambda p: abs(p[1] - p[0])) if pairs else None,
    }


def fmt(value, spec="%.2f", none="n/a"):
    return none if value is None else spec % value
