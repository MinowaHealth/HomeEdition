from datetime import date
from TestData.three_month_seed.personas.registry import REGISTRY, get_by_id


def test_registry_is_the_six_household_members():
    emails = {p.email for p in REGISTRY}
    assert emails == {
        "rodrigo@borgia.family", "vannozza@borgia.family",
        "lucrezia@borgia.family", "juan@borgia.family",
        "cesare@borgia.family", "adriana@borgia.family",
    }


def test_all_personas_are_single_household_domain():
    """No enterprise (@accademia / @ferrara.gov) domains survive."""
    for p in REGISTRY:
        assert p.email.endswith("@borgia.family"), p.email


def test_daily_event_total_meets_baseline_volume():
    """Per-day means × 90 days exceed a baseline at SCALE=1. Volume testing
    dials this far higher via SCALE; this only guards the persona profiles."""
    daily_total = 0.0
    for p in REGISTRY:
        ap = p.activity_profile
        daily_total += (
            ap.bp_per_day
            + ap.weight_per_week / 7
            + ap.stack_logs_per_day
            + ap.meal_logs_per_day
            + ap.observations_per_week / 7
        )
    assert daily_total * 90 > 1_500, f"got {daily_total * 90:.0f}, need > 1,500"


def test_adriana_is_top_density():
    adriana = get_by_id("b015b015-0001-0001-0006-b00000000006")
    def density(p):
        ap = p.activity_profile
        return ap.bp_per_day + ap.stack_logs_per_day + ap.meal_logs_per_day
    assert all(density(adriana) >= density(p) or p is adriana
               for p in REGISTRY if p.activity_profile.bp_per_day < 2.0)


def test_lucrezia_has_8_migraine_episodes():
    lucrezia = get_by_id("b015b015-0001-0001-0003-b00000000003")
    migraines = [b for b in lucrezia.narrative_beats if b.kind == "migraine_episode"]
    assert len(migraines) == 8


def test_adriana_has_6_missed_doses():
    adriana = get_by_id("b015b015-0001-0001-0006-b00000000006")
    missed = [b for b in adriana.narrative_beats if b.kind == "missed_dose"]
    assert len(missed) == 6


def test_minors_have_no_bp_self_tracking():
    """Lucrezia and Juan (minors) don't self-track blood pressure."""
    for uid in ("b015b015-0001-0001-0003-b00000000003",
                "b015b015-0001-0001-0004-b00000000004"):
        assert get_by_id(uid).activity_profile.bp_per_day == 0.0
