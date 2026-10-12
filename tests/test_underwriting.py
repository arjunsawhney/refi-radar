import pytest

from refi_radar.underwriting import (
    annual_debt_constant,
    annual_debt_service,
    remaining_balance,
    size_refinance,
    value_from_noi,
)


def simulate_balance(principal: float, rate: float, years: int, months: int) -> float:
    """Independent oracle: walk the amortization schedule month by month."""
    r = rate / 12
    n = years * 12
    payment = principal * r / (1 - (1 + r) ** -n)
    for _ in range(months):
        principal -= payment - principal * r
    return principal


def test_amortizing_debt_constant_matches_known_mortgage_payment():
    # $1M, 6%, 30-year: textbook monthly payment of $5,995.51
    assert annual_debt_service(1_000_000, 0.06, 30) / 12 == pytest.approx(5_995.51, abs=0.01)


def test_interest_only_debt_constant_is_the_coupon():
    assert annual_debt_constant(0.055, 0) == 0.055


def test_zero_rate_amortizing_loan_repays_straight_line():
    assert annual_debt_constant(0.0, 25) == pytest.approx(1 / 25)
    assert remaining_balance(1_000_000, 0.0, 25, 60) == pytest.approx(800_000)


@pytest.mark.parametrize("months", [1, 12, 60, 120, 359])
def test_remaining_balance_matches_schedule_simulation(months):
    assert remaining_balance(1_000_000, 0.065, 30, months) == pytest.approx(
        simulate_balance(1_000_000, 0.065, 30, months), rel=1e-9
    )


def test_interest_only_balance_never_amortizes():
    assert remaining_balance(1_000_000, 0.05, 0, 120) == 1_000_000


def test_balance_is_zero_after_full_term():
    assert remaining_balance(1_000_000, 0.05, 10, 120) == pytest.approx(0, abs=1e-6)
    assert remaining_balance(1_000_000, 0.05, 10, 500) == pytest.approx(0, abs=1e-6)


def test_value_from_noi_and_guards():
    assert value_from_noi(6_000_000, 0.08) == pytest.approx(75_000_000)
    assert value_from_noi(-1, 0.08) == 0
    with pytest.raises(ValueError):
        value_from_noi(1, 0)


def _size(noi, value, **kw):
    params = dict(rate=0.06, amortization_years=30, max_ltv=0.65, min_dscr=1.25, min_debt_yield=0.10)
    params.update(kw)
    return size_refinance(noi=noi, value=value, **params)


def test_sizing_takes_the_minimum_of_the_three_tests():
    result = _size(6_000_000, 75_000_000)
    assert result.ltv_limit == pytest.approx(48_750_000)
    assert result.debt_yield_limit == pytest.approx(60_000_000)
    assert result.dscr_limit == pytest.approx(6_000_000 / 1.25 / annual_debt_constant(0.06, 30))
    assert result.binding_constraint == "LTV"
    assert result.max_loan == pytest.approx(48_750_000)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"max_ltv": 0.90}, "Debt Yield"),
        ({"max_ltv": 0.90, "min_debt_yield": 0.05, "rate": 0.09}, "DSCR"),
    ],
)
def test_each_constraint_can_bind(overrides, expected):
    assert _size(6_000_000, 75_000_000, **overrides).binding_constraint == expected


def test_negative_noi_supports_no_debt():
    assert _size(-500_000, 0).max_loan == 0
