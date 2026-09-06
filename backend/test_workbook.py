"""
Tests for the workbook - the goal/project/commitment/entry spine.

The behaviours that matter here are the ones the old day planner got wrong:
unfinished work must roll *visibly* and be counted, completed work must survive
the day it was done in, and a commitment that has been avoided for a week must
look different from one written down this morning.
"""

from datetime import date, timedelta

import pytest

from workbook import STALE_ROLL_COUNT, Workbook


@pytest.fixture()
def wb(tmp_path):
    return Workbook(path=str(tmp_path / "workbook.db"))


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


# ───────────────────────── the hierarchy ─────────────────────────


def test_goal_project_commitment_chain(wb):
    goal = wb.add_goal("ship the channel relaunch", why="growth")
    project = wb.add_project("record 8 videos", goal_id=goal["id"])
    commitment = wb.add_commitment("script episode 3", project_id=project["id"])

    # The goal is inherited through the project - callers should not have to
    # thread the hierarchy through by hand.
    assert commitment["goal_id"] == goal["id"]
    assert commitment["project_id"] == project["id"]


def test_entry_inherits_the_hierarchy_from_its_commitment(wb):
    goal = wb.add_goal("relaunch")
    project = wb.add_project("videos", goal_id=goal["id"])
    commitment = wb.add_commitment("script ep 3", project_id=project["id"])

    entry = wb.log_entry("worked 90m, blocked on b-roll", commitment_id=commitment["id"], minutes=90)
    assert entry["goal_id"] == goal["id"]
    assert entry["project_id"] == project["id"]
    assert entry["minutes"] == 90


def test_goals_and_commitments_are_found_by_loose_reference(wb):
    wb.add_goal("ship the channel relaunch")
    wb.add_commitment("script episode three of the relaunch")

    assert wb.find_goal("the relaunch")["title"] == "ship the channel relaunch"
    assert "episode three" in wb.find_commitment("script episode")["text"]


def test_empty_titles_are_rejected(wb):
    for bad in ("", "   "):
        with pytest.raises(ValueError):
            wb.add_goal(bad)
        with pytest.raises(ValueError):
            wb.add_commitment(bad)


# ───────────────────────── today ─────────────────────────


def test_today_separates_due_overdue_and_done(wb):
    due = wb.add_commitment("due today")
    overdue = wb.add_commitment("from last week", due_date=days_ago(5))
    finished = wb.add_commitment("already handled")
    wb.complete_commitment(finished["id"])

    snapshot = wb.today()
    assert [c["id"] for c in snapshot["due"]] == [due["id"]]
    assert [c["id"] for c in snapshot["overdue"]] == [overdue["id"]]
    assert [c["id"] for c in snapshot["done"]] == [finished["id"]]


def test_completing_a_commitment_records_an_entry(wb):
    commitment = wb.add_commitment("script ep 3")
    wb.complete_commitment(commitment["id"], note="finally done")

    entries = wb.entries_on(date.today().isoformat())
    assert any(e["kind"] == "done" and "finally done" in e["text"] for e in entries)


# ───────────────────────── the planning window ─────────────────────────


def test_upcoming_covers_the_next_seven_days_not_the_calendar_week(wb):
    """
    The Sunday problem: asked "what's due this week" on a Sunday evening, an ISO
    Monday-Sunday window has three hours left in it, so a commitment made for
    tomorrow came back as "nothing outstanding this week" - true and useless.
    """
    tomorrow_item = wb.add_commitment("edit the intro", due_date=tomorrow())

    window = wb.upcoming(7)
    assert [c["id"] for c in window["commitments"]] == [tomorrow_item["id"]]


def test_upcoming_excludes_work_beyond_the_window(wb):
    from datetime import date as _date, timedelta as _td

    wb.add_commitment("far future", due_date=(_date.today() + _td(days=30)).isoformat())
    assert wb.upcoming(7)["commitments"] == []


def test_upcoming_still_carries_overdue_work(wb):
    """Overdue work is part of what is ahead of you - it is still owed."""
    late = wb.add_commitment("send the invoice", due_date=days_ago(4))

    window = wb.upcoming(7)
    assert [c["id"] for c in window["commitments"]] == [late["id"]]
    assert [c["id"] for c in window["overdue"]] == [late["id"]]


def test_upcoming_ignores_completed_work(wb):
    done = wb.add_commitment("already handled")
    wb.complete_commitment(done["id"])
    assert wb.upcoming(7)["commitments"] == []


def test_upcoming_is_ordered_by_when_it_is_due(wb):
    from datetime import date as _date, timedelta as _td

    later = wb.add_commitment("later", due_date=(_date.today() + _td(days=5)).isoformat())
    sooner = wb.add_commitment("sooner", due_date=tomorrow())

    order = [c["id"] for c in wb.upcoming(7)["commitments"]]
    assert order == [sooner["id"], later["id"]]


# ───────────────────────── rolling and slipping ─────────────────────────


def test_rolling_counts_the_roll(wb):
    commitment = wb.add_commitment("edit the intro")
    rolled = wb.roll_commitment(commitment["id"])

    assert rolled["due_date"] == tomorrow()
    assert rolled["roll_count"] == 1
    # first_due is the memory the old planner never kept: where this started.
    assert rolled["first_due"] == date.today().isoformat()


def test_a_repeatedly_rolled_commitment_is_flagged_as_stale(wb):
    commitment = wb.add_commitment("call the accountant", due_date=tomorrow())
    for _ in range(STALE_ROLL_COUNT):
        wb.roll_commitment(commitment["id"], tomorrow())

    flagged = wb.slipping()
    assert len(flagged) == 1
    assert flagged[0]["stale"] is True
    # Not overdue - it is dated in the future. It is flagged purely because it
    # keeps moving, which is exactly the case the flat planner could not see.
    assert flagged[0]["overdue"] is False


def test_overdue_commitments_report_how_late_they_are(wb):
    wb.add_commitment("send the invoice", due_date=days_ago(6))
    flagged = wb.slipping()
    assert flagged[0]["overdue"] is True
    assert flagged[0]["daysLate"] == 6


def test_a_fresh_commitment_is_not_flagged(wb):
    wb.add_commitment("written down this morning")
    assert wb.slipping() == []


# ───────────────────────── close-out ─────────────────────────


def test_close_out_records_the_day_and_rolls_what_is_left(wb):
    done = wb.add_commitment("recorded ep 2")
    wb.complete_commitment(done["id"])
    unfinished = wb.add_commitment("edit ep 2")

    result = wb.close_out()

    assert result["done"] == 1
    assert result["rolled"] == 1
    assert wb.get_commitment(unfinished["id"])["due_date"] == tomorrow()
    assert wb.was_closed_out(date.today().isoformat())


def test_completed_work_survives_the_close_out(wb):
    """The old planner erased completed tasks at midnight; history is the point."""
    done = wb.add_commitment("recorded ep 2")
    wb.complete_commitment(done["id"])

    wb.close_out()

    assert wb.get_commitment(done["id"])["status"] == "done"
    assert wb.scorecard()["done"] == 1


def test_close_out_can_leave_work_where_it_is(wb):
    commitment = wb.add_commitment("think about the thumbnail")
    result = wb.close_out(roll_unfinished=False)

    assert result["rolled"] == 0
    assert wb.get_commitment(commitment["id"])["due_date"] == date.today().isoformat()


def test_close_out_is_idempotent_for_a_date(wb):
    wb.close_out()
    wb.close_out()
    assert wb.was_closed_out(date.today().isoformat())


# ───────────────────────── the weekly scorecard ─────────────────────────


def test_scorecard_reports_kept_versus_committed(wb):
    kept = wb.add_commitment("kept this one")
    wb.complete_commitment(kept["id"])
    wb.add_commitment("did not get to this")

    card = wb.scorecard()
    assert card["committed"] == 2
    assert card["done"] == 1
    assert card["keptRatio"] == 0.5


def test_scorecard_names_goals_that_got_no_attention(wb):
    touched = wb.add_goal("relaunch")
    ignored = wb.add_goal("learn colour grading")
    commitment = wb.add_commitment("script ep 3", goal_id=touched["id"])
    wb.complete_commitment(commitment["id"])

    card = wb.scorecard()
    by_id = {g["id"]: g for g in card["goals"]}
    assert by_id[touched["id"]]["untouched"] is False
    assert by_id[ignored["id"]]["untouched"] is True


def test_scorecard_sums_logged_minutes(wb):
    commitment = wb.add_commitment("edit")
    wb.log_entry("first pass", commitment_id=commitment["id"], minutes=45)
    wb.log_entry("second pass", commitment_id=commitment["id"], minutes=30)

    assert wb.scorecard()["minutesLogged"] == 75


def test_empty_week_does_not_divide_by_zero(wb):
    card = wb.scorecard()
    assert card["committed"] == 0
    assert card["keptRatio"] is None


def test_stats_summarise_the_workbook(wb):
    goal = wb.add_goal("relaunch")
    wb.add_project("videos", goal_id=goal["id"])
    wb.add_commitment("script ep 3")
    wb.add_commitment("overdue thing", due_date=days_ago(3))

    stats = wb.stats()
    assert stats == {
        "goals": 1,
        "projects": 1,
        "openCommitments": 2,
        "entries": 0,
        "slipping": 1,
    }


def test_state_survives_a_reopen(wb, tmp_path):
    """It is a database, not a cache - restarting must not lose the week."""
    wb.add_goal("relaunch")
    wb.add_commitment("script ep 3")

    reopened = Workbook(path=wb._path)
    assert len(reopened.list_goals()) == 1
    assert len(reopened.list_commitments()) == 1
