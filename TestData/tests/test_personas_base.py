"""Test Persona dataclass and registry."""
from TestData.three_month_seed.personas.base import Persona, ActivityProfile
from TestData.three_month_seed.personas.registry import REGISTRY, get_by_id


def test_registry_size_matches_cohort_gate():
    from TestData.three_month_seed.cohort_gate import EXPECTED_USERS
    assert len(REGISTRY) == EXPECTED_USERS


def test_registry_uuids_match_seed_sql():
    expected_uuids = {
        "b015b015-0001-0001-0001-b00000000001",  # Rodrigo
        "b015b015-0001-0001-0002-b00000000002",  # Vannozza
        "b015b015-0001-0001-0003-b00000000003",  # Lucrezia
        "b015b015-0001-0001-0004-b00000000004",  # Juan
        "b015b015-0001-0001-0005-b00000000005",  # Cesare
        "b015b015-0001-0001-0006-b00000000006",  # Adriana
    }
    actual_uuids = {p.user_id for p in REGISTRY}
    assert actual_uuids == expected_uuids


def test_persona_is_frozen():
    p = REGISTRY[0]
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.email = "changed@example.com"


def test_get_by_id_returns_persona():
    p = get_by_id("b015b015-0001-0001-0001-b00000000001")
    assert p.email == "rodrigo@borgia.family"


def test_get_by_id_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        get_by_id("00000000-0000-0000-0000-000000000000")
