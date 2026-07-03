from TestData.three_month_seed.seed_rng import rng_for


def test_same_seed_same_persona_same_kind_identical():
    a = rng_for(seed=42, persona_id="rodrigo", source_kind="manual")
    b = rng_for(seed=42, persona_id="rodrigo", source_kind="manual")
    assert a.integers(0, 1_000_000, size=100).tolist() == \
           b.integers(0, 1_000_000, size=100).tolist()


def test_different_persona_independent():
    a = rng_for(seed=42, persona_id="rodrigo", source_kind="manual")
    b = rng_for(seed=42, persona_id="vannozza", source_kind="manual")
    assert a.integers(0, 1_000_000, size=100).tolist() != \
           b.integers(0, 1_000_000, size=100).tolist()


def test_different_source_kind_independent():
    a = rng_for(seed=42, persona_id="rodrigo", source_kind="manual")
    b = rng_for(seed=42, persona_id="rodrigo", source_kind="narrative")
    assert a.integers(0, 1_000_000, size=100).tolist() != \
           b.integers(0, 1_000_000, size=100).tolist()
