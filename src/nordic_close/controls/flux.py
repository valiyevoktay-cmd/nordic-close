"""Flux review: find material month-on-month movements and check each has a real explanation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

STOCK_PHRASES = (
    "timing",
    "in line with expectations",
    "as expected",
    "business as usual",
    "normal course",
    "normal fluctuation",
    "see above",
    "n/a",
    "tbc",
    "tbd",
    "one-off",
    "one off",
    "no comment",
    "various",
)
MIN_WORDS = 6


@dataclass
class FluxPolicy:
    pct: Decimal
    absolute: Decimal


@dataclass
class FluxItem:
    key: str
    label: str
    current: Decimal
    prior: Decimal
    change: Decimal
    change_pct: Decimal | None
    reasons: list[str]
    explanation: str
    author: str
    reviewer: str
    problems: list[str]

    @property
    def material(self) -> bool:
        return bool(self.reasons)

    @property
    def ok(self) -> bool:
        return not self.problems


def lint_explanation(text: str, author: str = "", reviewer: str = "") -> list[str]:
    """Return what is wrong with an explanation. An empty list means it is acceptable."""
    problems: list[str] = []
    stripped = text.strip()
    if not stripped:
        return ["missing"]
    lowered = stripped.lower()
    words = re.findall(r"[\w'-]+", lowered)
    only_stock = any(
        lowered.strip(" .,;:!") == phrase or (len(words) <= 4 and phrase in lowered)
        for phrase in STOCK_PHRASES
    )
    if only_stock:
        problems.append("boilerplate, name the driver and the amount")
    if len(words) < MIN_WORDS and not only_stock:
        problems.append(f"too short, use at least {MIN_WORDS} words")
    if not re.search(r"\d", stripped):
        problems.append("no amount or reference number")
    if author and reviewer and author == reviewer:
        problems.append("reviewed by its own author")
    return problems


def review(
    lines: list[dict],
    policy: FluxPolicy,
    explanations: dict[str, dict[str, str]],
) -> list[FluxItem]:
    """Evaluate every line. Material lines are those that pass the thresholds."""
    items: list[FluxItem] = []
    for line in lines:
        cur, pri = line["current"], line["prior"]
        change = cur - pri
        pct = None if pri == 0 else change / abs(pri) * 100
        reasons: list[str] = []
        big = abs(change) >= policy.absolute
        if big and pct is not None and abs(pct) >= policy.pct:
            reasons.append("above both thresholds")
        if big and pri == 0 and cur != 0:
            reasons.append("appears")
        if big and cur == 0 and pri != 0:
            reasons.append("disappears")
        if big and pri != 0 and cur != 0 and (pri > 0) != (cur > 0):
            reasons.append("changes sign")
        exp = explanations.get(line["key"], {})
        text = exp.get("text", "")
        problems = lint_explanation(text, exp.get("author", ""), exp.get("reviewer", "")) if reasons else []
        items.append(
            FluxItem(
                key=line["key"],
                label=line["label"],
                current=cur,
                prior=pri,
                change=change,
                change_pct=pct,
                reasons=reasons,
                explanation=text,
                author=exp.get("author", ""),
                reviewer=exp.get("reviewer", ""),
                problems=problems,
            )
        )
    return items
