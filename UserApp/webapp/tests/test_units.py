"""Tests for the canonical dosage-unit vocabulary (units.py)."""
import pytest

from units import CANONICAL_UNITS, UNIT_ALIASES, normalize_unit


class TestNormalizeUnit:
    @pytest.mark.parametrize('unit', CANONICAL_UNITS)
    def test_canonical_passthrough(self, unit):
        assert normalize_unit(unit) == unit

    @pytest.mark.parametrize('alias,canonical', sorted(UNIT_ALIASES.items()))
    def test_every_alias_maps(self, alias, canonical):
        assert normalize_unit(alias) == canonical
        assert canonical in CANONICAL_UNITS

    @pytest.mark.parametrize('raw,expected', [
        ('IU', 'iu'),
        ('mL', 'ml'),
        ('ML', 'ml'),
        ('MCG', 'ug'),
        ('MG', 'mg'),
        ('Tablets', 'tablet'),
    ])
    def test_casefold(self, raw, expected):
        assert normalize_unit(raw) == expected

    def test_micro_sign_codepoints(self):
        assert normalize_unit('µg') == 'ug'  # MICRO SIGN
        assert normalize_unit('μg') == 'ug'  # GREEK SMALL LETTER MU

    @pytest.mark.parametrize('raw,expected', [
        ('  mg  ', 'mg'),
        ('\tiu\n', 'iu'),
    ])
    def test_whitespace_trim(self, raw, expected):
        assert normalize_unit(raw) == expected

    @pytest.mark.parametrize('raw', [None, '', '   ', '\t\n'])
    def test_none_and_blank_return_none(self, raw):
        assert normalize_unit(raw) is None

    @pytest.mark.parametrize('raw', ['furlong', 'dose', 'tbd', '10 mg'])
    def test_unknown_raises_with_allowed_list(self, raw):
        with pytest.raises(ValueError) as exc:
            normalize_unit(raw)
        for unit in CANONICAL_UNITS:
            assert unit in str(exc.value)
