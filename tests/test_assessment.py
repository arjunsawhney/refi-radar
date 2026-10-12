from datetime import date

import pytest

from refi_radar.assessment import assess_loan
from refi_radar.models import PropertyType, RiskBucket, Scenario

BASE = Scenario(name="base", description="")

# With the conftest market: NOI $6M -> value $75M; max loan is LTV-bound at $48.75M.


@pytest.mark.parametrize(
    ("loan_kwargs", "bucket"),
    [
        ({"original_balance": 40_000_000}, RiskBucket.REFI_READY),
        ({"original_balance": 50_000_000}, RiskBucket.EQUITY_GAP),  # gap 1.25M = 2.5%
        ({"original_balance": 60_000_000}, RiskBucket.RECAP_REQUIRED),  # gap 11.25M = 18.75%
        ({"original_balance": 80_000_000}, RiskBucket.DISTRESSED),  # LTV 107%
        ({"original_balance": 20_000_000, "current_noi": 2_000_000, "note_rate": 0.12}, RiskBucket.DISTRESSED),
    ],
)
def test_classification(make_loan, market, loan_kwargs, bucket):
    assert assess_loan(make_loan(**loan_kwargs), market, BASE).bucket is bucket


def test_equity_gap_numbers(make_loan, market):
    a = assess_loan(make_loan(original_balance=50_000_000), market, BASE)
    assert a.stressed_value == pytest.approx(75_000_000)
    assert a.max_refi_loan == pytest.approx(48_750_000)
    assert a.binding_constraint == "LTV"
    assert a.refi_gap == pytest.approx(1_250_000)
    assert a.refi_gap_pct == pytest.approx(0.025)
    assert a.refi_rate == pytest.approx(0.06)
    assert a.in_place_dscr == pytest.approx(6_000_000 / (50_000_000 * 0.04), rel=1e-3)


def test_distress_reason_names_the_failing_test(make_loan, market):
    a = assess_loan(make_loan(original_balance=20_000_000, current_noi=2_000_000, note_rate=0.12), market, BASE)
    assert "DSCR 0.83x" in a.reasons[0]


@pytest.mark.parametrize(
    ("balance", "step", "pitch_word"),
    [
        (40_000_000, "Pitch refinance", "fully covers"),
        (50_000_000, "Pitch refi + gap equity", "preferred equity"),
        (60_000_000, "Pitch recapitalization", "JV or preferred equity partner"),
        (80_000_000, "Pitch note sale to lender", "discounted payoff"),
    ],
)
def test_next_step_and_talking_points_follow_the_bucket(make_loan, market, balance, step, pitch_word):
    a = assess_loan(make_loan(original_balance=balance), market, BASE)
    assert a.next_step == step
    assert len(a.talking_points) == 3
    assert "Apr 2027" in a.talking_points[0]
    assert pitch_word in a.talking_points[2]


def test_talking_points_quote_the_computed_numbers(make_loan, market):
    a = assess_loan(make_loan(original_balance=60_000_000), market, BASE)
    assert "$48.8M" in a.talking_points[1]  # max new loan
    assert "$11.2M" in a.talking_points[2] or "$11.3M" in a.talking_points[2]  # shortfall


def test_past_due_talking_point_says_so(make_loan, market):
    a = assess_loan(make_loan(maturity_date=date(2026, 6, 1)), market, BASE)
    assert "has not been repaid" in a.talking_points[0]


def test_amortizing_loan_refinances_its_balloon_not_original_balance(make_loan, market):
    a = assess_loan(make_loan(amortization_years=30, note_rate=0.05), market, BASE)
    assert a.balloon_balance < 50_000_000


def test_rate_shock_moves_refi_coupon_by_exact_bps(make_loan, market):
    shocked = assess_loan(make_loan(), market, Scenario(name="s", description="", rate_shock_bps=150))
    assert shocked.refi_rate == pytest.approx(0.075)


def test_sooner_maturity_ranks_higher(make_loan, market):
    soon = assess_loan(make_loan(maturity_date=date(2027, 1, 1)), market, BASE)
    later = assess_loan(make_loan(maturity_date=date(2029, 1, 1)), market, BASE)
    assert soon.priority.urgency > later.priority.urgency
    assert soon.priority.score > later.priority.score


def test_past_maturity_is_flagged_and_maximally_urgent(make_loan, market):
    a = assess_loan(make_loan(maturity_date=date(2026, 6, 1)), market, BASE)
    assert a.past_maturity
    assert a.months_to_maturity < 0
    assert a.priority.urgency == 1.0
    assert a.reasons[0].startswith("Matured")


def test_priority_score_is_bounded(make_loan, market):
    for balance in (500_000, 50_000_000, 5_000_000_000):
        score = assess_loan(make_loan(original_balance=balance), market, BASE).priority.score
        assert 0 <= score <= 100


def test_fee_follows_the_recommended_execution(make_loan, market):
    refi = assess_loan(make_loan(original_balance=40_000_000), market, BASE)
    assert refi.indicative_fee == pytest.approx(40_000_000 * 0.005)

    note_sale = assess_loan(make_loan(original_balance=80_000_000), market, BASE)
    assert note_sale.indicative_fee == pytest.approx(75_000_000 * 0.0075)

    asset_sale = assess_loan(make_loan(original_balance=20_000_000, current_noi=2_000_000, note_rate=0.12), market, BASE)
    assert asset_sale.indicative_fee == pytest.approx(25_000_000 * 0.01)

    recap = assess_loan(make_loan(original_balance=60_000_000), market, BASE)
    assert recap.indicative_fee == pytest.approx(48_750_000 * 0.005 + 11_250_000 * 0.01)


def test_multifamily_refi_points_to_agency_execution(make_loan, market):
    a = assess_loan(make_loan(original_balance=40_000_000, property_type=PropertyType.MULTIFAMILY), market, BASE)
    assert "Agency" in a.recommended_action


def test_zero_noi_does_not_produce_infinite_ratios(make_loan, market):
    a = assess_loan(make_loan(current_noi=0), market, BASE)
    assert a.bucket is RiskBucket.DISTRESSED
    assert a.ltv_at_maturity < float("inf")


def test_underwater_distress_is_pitched_to_the_lender(make_loan, market):
    a = assess_loan(make_loan(original_balance=80_000_000, lender_type="CMBS"), market, BASE)
    assert a.bucket is RiskBucket.DISTRESSED
    assert a.call_target == "lender"
    assert a.next_step == "Pitch note sale to lender"
    assert "special servicer" in a.talking_points[2]


def test_distress_with_equity_is_pitched_to_the_owner(make_loan, market):
    # Income can't cover the 12% coupon (DSCR 0.83x) but the $25M property is worth more than the $20M loan.
    a = assess_loan(make_loan(original_balance=20_000_000, current_noi=2_000_000, note_rate=0.12), market, BASE)
    assert a.bucket is RiskBucket.DISTRESSED
    assert a.ltv_at_maturity < 1
    assert a.call_target == "owner"
    assert a.next_step == "Pitch asset sale"
    assert "equity to protect" in a.talking_points[2]


def test_non_distressed_loans_are_pitched_to_the_owner(make_loan, market):
    for balance in (40_000_000, 50_000_000, 60_000_000):
        assert assess_loan(make_loan(original_balance=balance), market, BASE).call_target == "owner"


def test_talking_points_never_leave_loan_ambiguous(make_loan, market):
    """Regression: 'loan' must always be qualified as existing or new, with figures, so 104% LTV reads correctly."""
    a = assess_loan(make_loan(original_balance=80_000_000), market, BASE)  # $80M existing vs $75M value
    timing, new_loan, pitch = a.talking_points
    assert "existing $80.0M loan" in timing
    assert "worth about $75.0M" in new_loan and "A new loan would be at most $48.8M" in new_loan
    assert "65% of the property's value" in new_loan  # explains why the new loan is smaller
    assert "existing $80.0M loan is now more than the $75.0M" in pitch


def test_dscr_bound_new_loan_explains_income_limit(make_loan, market):
    a = assess_loan(make_loan(original_balance=40_000_000, current_noi=3_000_000), market, BASE)
    if a.binding_constraint == "DSCR":
        assert "income can only cover payments" in a.talking_points[1]
