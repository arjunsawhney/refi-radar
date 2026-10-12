from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from refi_radar.models import (
    BucketThresholds,
    FeeAssumptions,
    LenderConstraints,
    Loan,
    MarketAssumptions,
    PropertyType,
    PropertyTypeAssumptions,
)
from refi_radar.synthetic import generate_loans

AS_OF = date(2026, 10, 1)


@pytest.fixture
def market() -> MarketAssumptions:
    """Round-number assumptions so expected values can be computed by hand.

    Refi coupon = 4% + 2% = 6%; cap rate 8%; max LTV 65%; min DSCR 1.25x; min debt yield 10%.
    """
    pta = PropertyTypeAssumptions(
        spread=0.02,
        cap_rate=0.08,
        opex_ratio=0.40,
        constraints=LenderConstraints(max_ltv=0.65, min_dscr=1.25, min_debt_yield=0.10),
    )
    return MarketAssumptions(
        as_of=AS_OF,
        base_rate=0.04,
        refi_amortization_years=30,
        horizon_months=36,
        property_types={pt: pta for pt in PropertyType},
        fees=FeeAssumptions(
            debt_placement_bps=50, equity_placement_bps=100, investment_sales_bps=100, loan_sales_bps=75
        ),
        thresholds=BucketThresholds(equity_gap_max_pct=0.10, distressed_ltv=1.0, distressed_dscr=1.0),
    )


BASE_LOAN = Loan(
    loan_id="T-1",
    property_name="Test Tower",
    property_type=PropertyType.OFFICE,
    market="Chicago, IL",
    sponsor="Test Sponsor",
    lender_type="CMBS",
    origination_date=date(2019, 4, 1),
    maturity_date=date(2027, 4, 1),
    original_balance=50_000_000,
    note_rate=0.04,
    amortization_years=0,
    original_value=80_000_000,
    current_noi=6_000_000,
    occupancy=0.85,
)


@pytest.fixture
def make_loan():
    """Factory: an interest-only loan with $6M NOI (value $75M at an 8% cap); override any field."""

    def _make(**overrides) -> Loan:
        return replace(BASE_LOAN, **overrides)

    return _make


@pytest.fixture(scope="session")
def synthetic_loans() -> list[Loan]:
    return generate_loans(count=250, seed=42)
