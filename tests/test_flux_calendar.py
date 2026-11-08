from decimal import Decimal

import pytest

from nordic_close.controls import calendar, flux

D = Decimal
POLICY = flux.FluxPolicy(pct=D(10), absolute=D(1000))


def lines(cur, prior, key="is.revenue"):
    return [{"key": key, "label": "Revenue", "current": D(cur), "prior": D(prior)}]


def test_material_line_without_explanation_is_flagged():  # AC-13
    items = flux.review(lines(1150000, 1000000), POLICY, {})
    assert items[0].material and not items[0].ok and items[0].problems == ["missing"]


def test_small_changes_are_not_material():
    assert not flux.review(lines(1050000, 1000000), POLICY, {})[0].material   # 5%
    assert not flux.review(lines(10500, 10000), POLICY, {})[0].material       # below absolute


@pytest.mark.parametrize("text", ["timing", "Timing.", "in line with expectations", "as expected"])
def test_boilerplate_is_rejected(text):  # AC-14
    problems = flux.lint_explanation(text)
    assert any("boilerplate" in p for p in problems)


def test_good_explanation_passes_and_needs_a_number():
    good = "Revenue rose by SEK 150,000 because order 1042 was invoiced on 15 October."
    assert flux.lint_explanation(good) == []
    vague = "Customers ordered a lot more this month overall"
    assert "no amount or reference number" in flux.lint_explanation(vague)
    assert any("too short" in p for p in flux.lint_explanation("Up 15 percent"))


def test_self_review_is_rejected():
    problems = flux.lint_explanation("Revenue rose by SEK 150,000 on order 1042 in October.", "Ann", "Ann")
    assert "reviewed by its own author" in problems


def test_appears_disappears_and_sign_change():
    appear = flux.review(lines(5000, 0), POLICY, {})[0]
    assert "appears" in appear.reasons and appear.change_pct is None  # EC-4
    assert "disappears" in flux.review(lines(0, 5000), POLICY, {})[0].reasons
    assert "changes sign" in flux.review(lines(-5000, 5000), POLICY, {})[0].reasons
    assert not flux.review(lines(-300, 300), POLICY, {})[0].material  # sign flip below threshold


def test_valid_explanation_clears_the_line():
    exp = {"is.revenue": {"text": "Revenue rose by SEK 150,000 on order 1042 in October.",
                          "author": "Ann", "reviewer": "Bo"}}
    item = flux.review(lines(1150000, 1000000), POLICY, exp)[0]
    assert item.material and item.ok


# ---- calendar

def plan(tasks, target=5):
    return {"period": "2026-10", "target_workday": target, "tasks": tasks}


def task(tid, after=(), days=1, owner="Ann", reviewer="Bo", key=False):
    return {"id": tid, "name": tid, "owner": owner, "reviewer": reviewer, "days": days,
            "after": list(after), "key_control": key}


def test_cycle_is_rejected():  # AC-15
    with pytest.raises(calendar.PlanError, match="a -> b -> a"):
        calendar.build_plan(plan([task("a", after=["b"]), task("b", after=["a"])]))


def test_unknown_dependency_is_rejected():
    with pytest.raises(calendar.PlanError, match="unknown task"):
        calendar.build_plan(plan([task("a", after=["zzz"])]))


def test_critical_path_and_slack():
    p = calendar.build_plan(plan([
        task("a", days=1), task("b", after=["a"], days=2, owner="Cy"),
        task("c", after=["a"], days=1, owner="Di"), task("d", after=["b", "c"], days=1, owner="Ed"),
    ]))
    assert p.finish == 4 and p.critical_path == ["a", "b", "d"]
    assert next(t for t in p.tasks if t.id == "c").slack == 1
    assert p.passes


def test_reviewer_is_owner_blocks():  # AC-16
    p = calendar.build_plan(plan([task("a", owner="Ann", reviewer="Ann")]))
    assert not p.passes and "reviewed by its owner" in p.problems[0]


def test_missing_owner_and_missing_reviewer():
    p = calendar.build_plan(plan([task("a", owner=""), task("b", reviewer="", key=True, owner="Cy")]))
    assert "task 'a' has no owner" in p.problems
    assert "key control 'b' has no reviewer" in p.problems


def test_target_overrun_and_owner_overload():
    p = calendar.build_plan(plan([task("a", days=4), task("b", after=["a"], days=3)], target=5))
    assert any("target is 5" in x for x in p.problems)
    q = calendar.build_plan(plan([task("a", days=1), task("b", days=1)]))  # same owner, same day
    assert any("booked for 2 days" in x for x in q.problems)


def test_load_plan_errors(tmp_path):
    with pytest.raises(calendar.PlanError, match="not found"):
        calendar.load_plan(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(calendar.PlanError, match="not valid JSON"):
        calendar.load_plan(bad)


def test_workday_date_skips_weekends():
    from datetime import date
    assert calendar.workday_date(date(2026, 11, 2), 5) == date(2026, 11, 6)
    assert calendar.workday_date(date(2026, 11, 6), 2) == date(2026, 11, 9)
