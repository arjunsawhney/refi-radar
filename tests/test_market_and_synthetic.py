from datetime import date

import pytest

from refi_radar.market import MarketConfigError, load_market, parse_market
from refi_radar.models import PropertyType
from refi_radar.synthetic import generate_loans


def test_bundled_market_assumptions_load_for_every_property_type():
    market = load_market()
    assert market.as_of == date(2026, 10, 1)
    assert set(market.property_types) == set(PropertyType)


def test_market_rejects_missing_property_type():
    raw = {
        "as_of": date(2026, 10, 1),
        "base_rate": 0.04,
        "refi_amortization_years": 30,
        "horizon_months": 36,
        "property_types": {},
        "fees": {},
        "thresholds": {},
    }
    with pytest.raises(MarketConfigError, match="office"):
        parse_market(raw)


def test_synthetic_tape_is_deterministic_per_seed():
    assert generate_loans(50, seed=7) == generate_loans(50, seed=7)
    assert generate_loans(50, seed=7) != generate_loans(50, seed=8)


def test_synthetic_loans_are_internally_consistent():
    for loan in generate_loans(500, seed=1):
        assert loan.maturity_date > loan.origination_date
        assert 0 < loan.original_balance < loan.original_value
        assert 0 < loan.occupancy <= 1
        assert 0 < loan.note_rate < 0.25
        assert loan.current_noi > 0
