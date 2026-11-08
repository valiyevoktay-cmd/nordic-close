"""Close calendar: order tasks by dependency, find the critical path, check people and reviews."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


class PlanError(Exception):
    """Raised when the close plan cannot be used."""


@dataclass
class Task:
    id: str
    name: str
    owner: str
    reviewer: str
    days: float
    after: list[str]
    key_control: bool
    start: float = 0.0
    finish: float = 0.0
    slack: float = 0.0
    critical: bool = False


@dataclass
class Plan:
    period: str
    target_workday: float
    first_workday: date | None
    tasks: list[Task]
    order: list[str]
    finish: float
    critical_path: list[str]
    owner_load: dict[str, float]
    problems: list[str]

    @property
    def passes(self) -> bool:
        return not self.problems


def load_plan(path: Path) -> Plan:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PlanError(f"close plan not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PlanError(f"close plan is not valid JSON: {exc}") from exc
    return build_plan(raw)


def _topological(tasks: dict[str, Task]) -> list[str]:
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(tid: str, trail: list[str]) -> None:
        if state.get(tid) == 2:
            return
        if state.get(tid) == 1:
            cycle = " -> ".join([*trail[trail.index(tid):], tid])
            raise PlanError(f"tasks form a cycle: {cycle}")
        state[tid] = 1
        for dep in tasks[tid].after:
            if dep not in tasks:
                raise PlanError(f"task {tid} depends on unknown task {dep!r}")
            visit(dep, [*trail, tid])
        state[tid] = 2
        order.append(tid)

    for tid in tasks:
        visit(tid, [])
    return order


def build_plan(raw: dict) -> Plan:
    tasks = {
        t["id"]: Task(
            id=t["id"],
            name=t["name"],
            owner=t.get("owner", ""),
            reviewer=t.get("reviewer", ""),
            days=float(t["days"]),
            after=list(t.get("after", [])),
            key_control=bool(t.get("key_control", False)),
        )
        for t in raw["tasks"]
    }
    order = _topological(tasks)

    for tid in order:
        t = tasks[tid]
        t.start = max((tasks[d].finish for d in t.after), default=0.0)
        t.finish = t.start + t.days
    finish = max(t.finish for t in tasks.values())

    # Backward pass for slack.
    latest_finish = {tid: finish for tid in tasks}
    for tid in reversed(order):
        t = tasks[tid]
        for dep in t.after:
            latest_finish[dep] = min(latest_finish[dep], latest_finish[tid] - t.days)
    for tid, t in tasks.items():
        t.slack = round(latest_finish[tid] - t.finish, 6)
        t.critical = abs(t.slack) < 1e-9
    # One chain of critical tasks, in order.
    critical_path = [tid for tid in order if tasks[tid].critical]

    target = float(raw.get("target_workday", 5))
    first = date.fromisoformat(raw["first_workday"]) if raw.get("first_workday") else None

    problems: list[str] = []
    for t in tasks.values():
        if not t.owner:
            problems.append(f"task '{t.id}' has no owner")
        if t.key_control and not t.reviewer:
            problems.append(f"key control '{t.id}' has no reviewer")
        if t.reviewer and t.reviewer == t.owner:
            problems.append(f"'{t.id}' is reviewed by its owner ({t.owner})")
    if finish > target + 1e-9:
        problems.append(f"critical path ends on workday {finish:g}, target is {target:g}")

    # Owner load: days of work booked per owner on each workday. Over 1.0 is a plan that slips.
    load: dict[str, float] = {}
    for t in tasks.values():
        day = int(t.start)
        remaining = t.days
        while remaining > 1e-9:
            slot = min(remaining, 1 - (t.start - day) if day == int(t.start) else 1)
            key = f"{t.owner}|{day + 1}"
            load[key] = load.get(key, 0.0) + slot
            remaining -= slot
            day += 1
    overloaded = {k: v for k, v in load.items() if v > 1.0 + 1e-9}
    for key, value in sorted(overloaded.items()):
        owner, wd = key.split("|")
        problems.append(f"{owner} is booked for {value:g} days on workday {wd}")

    return Plan(
        period=raw["period"],
        target_workday=target,
        first_workday=first,
        tasks=[tasks[tid] for tid in order],
        order=order,
        finish=finish,
        critical_path=critical_path,
        owner_load={k: round(v, 3) for k, v in load.items()},
        problems=problems,
    )


def workday_date(first: date, workday: float) -> date:
    """Calendar date of the end of a workday, skipping weekends. Workday 1 is ``first``."""
    days = max(int(workday + 1e-9) - 1, 0)
    current = first
    while days > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days -= 1
    return current
