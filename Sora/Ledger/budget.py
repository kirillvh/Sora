"""A spend ceiling for anything that runs in a loop.

It lives in the Ledger because the Ledger is the only thing that knows what a
call cost. Evals are where the $20 goes - an eval that repeats N sessions x M
profiles x K repeats and then calls a judge per turn per axis is a
multiplicative structure, and multiplicative structures are how people wake up
to an empty API key.

    guard = CostGuard(max_usd=1.00, label="benchmark")
    for unit in work:
        guard.check("session_3 repeat 2")   # raises BudgetExceeded
        ...
    print(guard.summary())

The check is before-the-fact and coarse: it stops before starting new work
once spend has crossed the line, so the real total can overshoot by at most
one unit of work. Making it exact would mean predicting a call's cost before
making it, which is a worse trade than overshooting by one turn.

Only calls made in THIS process count (it reads the in-process ledger totals),
which is what you want for "how much did this run cost".
"""
from __future__ import annotations


class BudgetExceeded(RuntimeError):
    """Raised by CostGuard.check() once the ceiling is crossed."""

    def __init__(self, spent, limit, note=""):
        self.spent, self.limit, self.note = spent, limit, note
        super().__init__("cost ceiling reached: $%.4f of $%.2f%s"
                         % (spent, limit, (" at %s" % note) if note else ""))


class CostGuard:
    def __init__(self, max_usd: float = 1.00, label: str = ""):
        from .meter import totals

        self.max_usd = float(max_usd)
        self.label = label
        self._start = totals()["usd"]
        self._start_calls = totals()["calls"]
        self.stopped_at = None

    def spent(self) -> float:
        from .meter import totals

        return max(0.0, totals()["usd"] - self._start)

    def calls(self) -> int:
        from .meter import totals

        return totals()["calls"] - self._start_calls

    def remaining(self) -> float:
        return max(0.0, self.max_usd - self.spent())

    def exceeded(self) -> bool:
        return self.spent() >= self.max_usd

    def check(self, note: str = "") -> None:
        if self.exceeded():
            self.stopped_at = note
            raise BudgetExceeded(self.spent(), self.max_usd, note)

    def fraction(self) -> float:
        return self.spent() / self.max_usd if self.max_usd else 0.0

    def summary(self) -> str:
        return "[budget%s] $%.4f of $%.2f used (%.0f%%), %d calls%s" % (
            (" " + self.label) if self.label else "", self.spent(), self.max_usd,
            100 * self.fraction(), self.calls(),
            (" - STOPPED at %s" % self.stopped_at) if self.stopped_at else "")

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "max_usd": self.max_usd,
            "spent_usd": round(self.spent(), 6),
            "calls": self.calls(),
            "stopped_early": self.stopped_at is not None,
            "stopped_at": self.stopped_at,
        }
