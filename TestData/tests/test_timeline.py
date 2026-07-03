from datetime import date
from TestData.three_month_seed.timeline import iter_days, dispatch_persona_day
from TestData.three_month_seed.personas.registry import get_by_id


def test_iter_days_inclusive_endpoints():
    days = list(iter_days(end=date(2026, 5, 8), window_days=90))
    assert len(days) == 90
    assert days[0] == date(2026, 2, 8)
    assert days[-1] == date(2026, 5, 8)


def test_dispatch_returns_event_descriptors():
    """dispatch_persona_day returns a list of (event_kind, body_dict) pairs
    based on the persona's activity profile + narrative beats for that day."""
    adriana = get_by_id("b015b015-0001-0001-0006-b00000000006")
    events = dispatch_persona_day(adriana, day=date(2026, 3, 12), seed=42)
    assert events, "expected a non-empty descriptor list"
    for kind, body in events:
        assert isinstance(kind, str) and isinstance(body, dict)
    # Adriana self-tracks BP (bp_per_day=2.0).
    assert any(k == "bp" for k, _ in events)


def test_dispatch_emits_narrative_beats():
    lucrezia = get_by_id("b015b015-0001-0001-0003-b00000000003")
    events = dispatch_persona_day(
        lucrezia, day=date(2026, 3, 9), seed=42
    )
    migraines = [e for e in events if e[0] == "migraine_episode"]
    assert len(migraines) == 1
