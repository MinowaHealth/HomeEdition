"""Canonical dosage-unit vocabulary and normalization.

Single source of truth for medication/supplement units. Dependency-free so
scripts/ can import it without dragging in Flask (see
scripts/normalize_dosage_units.py).

The frontend mirrors this list in the DOSAGE_UNIT_GROUPS constant in
index.html — keep the two in sync.
"""

CANONICAL_UNITS: tuple[str, ...] = (
    # mass / volume
    'ug', 'mg', 'g', 'ml', 'oz',
    # potency
    'iu',
    # dose forms / counts
    'spray', 'patch', 'application', 'drop', 'puff',
    'tablet', 'capsule', 'unit',
)

# Keys must be casefolded. 'dose' (HealthKit magic value) and 'tbd' are
# deliberately absent — they must never normalize into a real unit.
UNIT_ALIASES: dict[str, str] = {
    'mcg': 'ug',
    'µg': 'ug',  # MICRO SIGN — casefold() does not unify with GREEK MU
    'μg': 'ug',  # GREEK SMALL LETTER MU
    'microgram': 'ug',
    'micrograms': 'ug',
    'milligram': 'mg',
    'milligrams': 'mg',
    'gram': 'g',
    'grams': 'g',
    'milliliter': 'ml',
    'milliliters': 'ml',
    'millilitre': 'ml',
    'millilitres': 'ml',
    'ounce': 'oz',
    'ounces': 'oz',
    'sprays': 'spray',
    'patches': 'patch',
    'applications': 'application',
    'drops': 'drop',
    'puffs': 'puff',
    'tablets': 'tablet',
    'capsules': 'capsule',
    'units': 'unit',
}


def normalize_unit(raw: str | None) -> str | None:
    """Normalize a dosage unit to its canonical form.

    Args:
        raw: Unit string as received from a client, or None.

    Returns:
        The canonical unit, or None if raw is None/empty/whitespace
        (the column stays NULL).

    Raises:
        ValueError: If the unit does not map to the canonical vocabulary.
    """
    if raw is None or not raw.strip():
        return None
    key = raw.strip().casefold()
    if key in CANONICAL_UNITS:
        return key
    if key in UNIT_ALIASES:
        return UNIT_ALIASES[key]
    raise ValueError(
        f"default_unit must be one of: {', '.join(CANONICAL_UNITS)}"
    )
