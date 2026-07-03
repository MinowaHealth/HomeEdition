# TestData/three_month_seed/personas/borgia.py
#
# The Borgia nuclear household — the six members of the single-household
# Home Edition test fixture. All accounts are @borgia.family, tenant_id=1.
# No delegation/provider relationships exist: privacy is per-user at the
# application layer, so personas carry only their own activity + beats.
from datetime import date
from .base import Persona, ActivityProfile, NarrativeBeat

RODRIGO = Persona(
    user_id="b015b015-0001-0001-0001-b00000000001",
    email="rodrigo@borgia.family",
    display_name="Rodrigo Borgia",
    activity_profile=ActivityProfile(
        bp_per_day=1.0, weight_per_week=1.0,
        stack_logs_per_day=1.0, meal_logs_per_day=1.0,
        observations_per_week=1.0,
    ),
    narrative_beats=(),  # BP-pact handled in Vannozza's beats below
)

VANNOZZA = Persona(
    user_id="b015b015-0001-0001-0002-b00000000002",
    email="vannozza@borgia.family",
    display_name="Vannozza dei Cattanei",
    activity_profile=ActivityProfile(
        bp_per_day=1.0, weight_per_week=1.0,
        stack_logs_per_day=1.0, meal_logs_per_day=1.0,
        observations_per_week=1.0,
    ),
    narrative_beats=(
        # Couple's BP pact — both spouses log at 07:00 daily. Encoded as
        # a single beat marker; timeline.py uses time_of_day_hint to pin to
        # 07:00 instead of distributing randomly.
        NarrativeBeat(when=date(2026, 2, 8), kind="bp_pact_start", payload=(7,)),
    ),
)

# Lucrezia — 8 migraine episodes on deterministic dates.
_LUCREZIA_MIGRAINE_DATES = (
    date(2026, 3, 9), date(2026, 3, 17), date(2026, 3, 26),
    date(2026, 4, 5), date(2026, 4, 14), date(2026, 4, 21),
    date(2026, 4, 30), date(2026, 5, 6),
)
LUCREZIA = Persona(
    user_id="b015b015-0001-0001-0003-b00000000003",
    email="lucrezia@borgia.family",
    display_name="Lucrezia Borgia",
    activity_profile=ActivityProfile(
        bp_per_day=0.0, weight_per_week=0.0,
        stack_logs_per_day=1.0, meal_logs_per_day=1.0,
        observations_per_week=2.0,
    ),
    narrative_beats=tuple(
        NarrativeBeat(when=d, kind="migraine_episode",
                      payload=("aura", "sumatriptan_50mg"))
        for d in _LUCREZIA_MIGRAINE_DATES
    ),
)

JUAN = Persona(
    user_id="b015b015-0001-0001-0004-b00000000004",
    email="juan@borgia.family",
    display_name="Juan Borgia",
    activity_profile=ActivityProfile(
        weight_per_week=1.0, stack_logs_per_day=1.0, meal_logs_per_day=1.0,
    ),
)

CESARE = Persona(
    user_id="b015b015-0001-0001-0005-b00000000005",
    email="cesare@borgia.family",
    display_name="Cesare Borgia",
    activity_profile=ActivityProfile(
        bp_per_day=1.0, weight_per_week=1.0,
        stack_logs_per_day=1.5, meal_logs_per_day=1.0,
        observations_per_week=1.0,
    ),
    narrative_beats=(
        NarrativeBeat(when=date(2026, 4, 16), kind="note_to_self",
                      payload=("Start tracking sleep quality this month.",)),
    ),
)

# Adriana — 6 missed-dose events scattered through window.
_ADRIANA_MISSED_DOSE_DATES = (
    date(2026, 3, 12), date(2026, 3, 22), date(2026, 4, 3),
    date(2026, 4, 17), date(2026, 4, 28), date(2026, 5, 5),
)
ADRIANA = Persona(
    user_id="b015b015-0001-0001-0006-b00000000006",
    email="adriana@borgia.family",
    display_name="Adriana de Mila",
    activity_profile=ActivityProfile(
        bp_per_day=2.0, weight_per_week=1.0,
        stack_logs_per_day=1.0, meal_logs_per_day=1.0,
        observations_per_week=2.0,
    ),
    narrative_beats=tuple(
        NarrativeBeat(when=d, kind="missed_dose", payload=("warfarin_5mg",))
        for d in _ADRIANA_MISSED_DOSE_DATES
    ),
)

BORGIA = (RODRIGO, VANNOZZA, LUCREZIA, JUAN, CESARE, ADRIANA)
