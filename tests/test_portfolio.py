import pytest

from refi_radar.assessment import assess_loan
from refi_radar.market import load_market
from refi_radar.models import PropertyType, RiskBucket
from refi_radar.portfolio import analyze_portfolio, in_window
from refi_radar.scenarios import get_preset


@pytest.fixture(scope="module")
def real_market():
    return load_market()


def test_summary_reconciles_to_loan_level_detail(synthetic_loans, real_market):
    analysis = analyze_portfolio(synthetic_loans, real_market, get_preset("base"))
    s = analysis.summary
    assert s.loans_in_window + s.loans_outside_window == len(synthetic_loans)
    assert sum(b.loan_count for b in s.by_bucket) == s.loans_in_window
    assert sum(b.balance for b in s.by_bucket) == pytest.approx(s.total_balance)
    assert sum(t.balance for t in s.by_property_type) == pytest.approx(s.total_balance)
    for t in s.by_property_type:
        assert sum(t.balance_by_bucket.values()) == pytest.approx(t.balance)
        at_risk = t.balance_by_bucket["recap_required"] + t.balance_by_bucket["distressed"]
        assert at_risk == pytest.approx(t.at_risk_balance)
    assert sum(sum(p.balance_by_bucket.values()) for p in s.maturity_wall) == pytest.approx(s.total_balance)
    assert s.total_refi_gap == pytest.approx(sum(a.refi_gap for a in analysis.assessments))


def test_call_list_is_ranked_by_priority(synthetic_loans, real_market):
    scores = [a.priority.score for a in analyze_portfolio(synthetic_loans, real_market, get_preset("base")).assessments]
    assert scores == sorted(scores, reverse=True)


def test_only_loans_inside_the_horizon_are_triaged(synthetic_loans, real_market):
    analysis = analyze_portfolio(synthetic_loans, real_market, get_preset("base"))
    assert analysis.summary.loans_outside_window > 0
    ids = {a.loan_id for a in analysis.assessments}
    assert all(in_window(l, real_market) == (l.loan_id in ids) for l in synthetic_loans)


@pytest.mark.parametrize("scenario", ["higher_for_longer", "office_vacancy_shock", "severe"])
def test_stress_never_improves_any_loan(synthetic_loans, real_market, scenario):
    """Invariant: every preset is a pure downside, so no loan's gap or bucket may improve."""
    base = {l.loan_id: assess_loan(l, real_market, get_preset("base")) for l in synthetic_loans}
    for loan in synthetic_loans:
        stressed = assess_loan(loan, real_market, get_preset(scenario))
        assert stressed.refi_gap >= base[loan.loan_id].refi_gap - 1e-6
        assert stressed.bucket.severity >= base[loan.loan_id].bucket.severity


def test_office_shock_migrations_are_office_only(synthetic_loans, real_market):
    comparison = analyze_portfolio(synthetic_loans, real_market, get_preset("office_vacancy_shock")).comparison
    assert comparison is not None and comparison.baseline == "base"
    assert comparison.loans_worsened > 0
    assert comparison.refi_gap_delta > 0
    assert all(m.property_type is PropertyType.OFFICE for m in comparison.top_migrations)
    assert all(m.to_bucket.severity > m.from_bucket.severity for m in comparison.top_migrations)


def test_base_scenario_has_no_comparison(synthetic_loans, real_market):
    assert analyze_portfolio(synthetic_loans, real_market, get_preset("base")).comparison is None


def test_property_type_filter(synthetic_loans, real_market):
    analysis = analyze_portfolio(
        synthetic_loans, real_market, get_preset("base"), property_types={PropertyType.INDUSTRIAL}
    )
    assert analysis.assessments
    assert {a.property_type for a in analysis.assessments} == {PropertyType.INDUSTRIAL}


def test_synthetic_book_tells_the_expected_story(synthetic_loans, real_market):
    """Sanity check on calibration: office is the stressed sector, industrial the healthiest."""
    by_type = {
        t.property_type: t.at_risk_balance / t.balance
        for t in analyze_portfolio(synthetic_loans, real_market, get_preset("base")).summary.by_property_type
    }
    assert by_type[PropertyType.OFFICE] == max(by_type.values())
    assert by_type[PropertyType.INDUSTRIAL] == min(by_type.values())


def test_empty_portfolio(real_market):
    s = analyze_portfolio([], real_market, get_preset("severe")).summary
    assert s.loans_in_window == 0 and s.at_risk_pct == 0
    assert [b.bucket for b in s.by_bucket] == list(RiskBucket)
