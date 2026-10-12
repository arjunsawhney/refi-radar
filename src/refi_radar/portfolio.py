"""Portfolio-level analysis: run every loan through the engine and roll results up."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from .assessment import assess_loan, months_until
from .models import Loan, LoanAssessment, MarketAssumptions, PropertyType, RiskBucket, Scenario
from .scenarios import get_preset

AT_RISK_BUCKETS = frozenset({RiskBucket.RECAP_REQUIRED, RiskBucket.DISTRESSED})


@dataclass(frozen=True, slots=True)
class BucketStat:
    bucket: RiskBucket
    loan_count: int
    balance: float
    refi_gap: float


@dataclass(frozen=True, slots=True)
class PropertyTypeStat:
    property_type: PropertyType
    loan_count: int
    balance: float
    refi_gap: float
    at_risk_balance: float
    balance_by_bucket: dict[str, float]


@dataclass(frozen=True, slots=True)
class MaturityPeriod:
    period: str
    balance_by_bucket: dict[str, float]


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    loans_in_window: int
    loans_outside_window: int
    total_balance: float
    total_refi_gap: float
    at_risk_balance: float
    at_risk_pct: float
    indicative_fee_pool: float
    by_bucket: list[BucketStat]
    by_property_type: list[PropertyTypeStat]
    maturity_wall: list[MaturityPeriod]


@dataclass(frozen=True, slots=True)
class Migration:
    loan_id: str
    property_name: str
    property_type: PropertyType
    balance: float
    from_bucket: RiskBucket
    to_bucket: RiskBucket


@dataclass(frozen=True, slots=True)
class ScenarioComparison:
    baseline: str
    loans_worsened: int
    balance_worsened: float
    refi_gap_delta: float
    transitions: dict[str, int]
    """'from->to' counts, only for loans whose bucket changed."""
    top_migrations: list[Migration]


@dataclass(frozen=True, slots=True)
class PortfolioAnalysis:
    scenario: Scenario
    as_of: date
    summary: PortfolioSummary
    assessments: list[LoanAssessment]
    comparison: ScenarioComparison | None = None
    data_quality: dict[str, int] = field(default_factory=dict)
    assessments_total: int | None = None
    """Loans matching the call-list filter before truncation to top_n (set by the API)."""


def in_window(loan: Loan, market: MarketAssumptions) -> bool:
    return months_until(market.as_of, loan.maturity_date) <= market.horizon_months


def period_label(a: LoanAssessment) -> str:
    if a.past_maturity:
        return "Past due"
    return f"{a.maturity_date.year} Q{(a.maturity_date.month - 1) // 3 + 1}"


def _period_sort_key(label: str) -> tuple[int, str]:
    return (0, "") if label == "Past due" else (1, label)


def filter_assessments(
    assessments: list[LoanAssessment],
    *,
    property_type: PropertyType | None = None,
    bucket: RiskBucket | None = None,
    period: str | None = None,
) -> list[LoanAssessment]:
    """Narrow the ranked call list (e.g. to a clicked chart segment) without changing the portfolio summary."""
    return [
        a
        for a in assessments
        if (property_type is None or a.property_type is property_type)
        and (bucket is None or a.bucket is bucket)
        and (period is None or period_label(a) == period)
    ]


def summarize(assessments: list[LoanAssessment], loans_outside_window: int) -> PortfolioSummary:
    total_balance = sum(a.balloon_balance for a in assessments)
    at_risk = sum(a.balloon_balance for a in assessments if a.bucket in AT_RISK_BUCKETS)

    by_bucket = [
        BucketStat(
            bucket=b,
            loan_count=sum(1 for a in assessments if a.bucket is b),
            balance=round(sum(a.balloon_balance for a in assessments if a.bucket is b), 2),
            refi_gap=round(sum(a.refi_gap for a in assessments if a.bucket is b), 2),
        )
        for b in RiskBucket
    ]

    by_type = []
    for pt in PropertyType:
        rows = [a for a in assessments if a.property_type is pt]
        if not rows:
            continue
        by_type.append(
            PropertyTypeStat(
                property_type=pt,
                loan_count=len(rows),
                balance=round(sum(a.balloon_balance for a in rows), 2),
                refi_gap=round(sum(a.refi_gap for a in rows), 2),
                at_risk_balance=round(sum(a.balloon_balance for a in rows if a.bucket in AT_RISK_BUCKETS), 2),
                balance_by_bucket={
                    b.value: round(sum(a.balloon_balance for a in rows if a.bucket is b), 2) for b in RiskBucket
                },
            )
        )

    wall: dict[str, dict[str, float]] = defaultdict(lambda: {b.value: 0.0 for b in RiskBucket})
    for a in assessments:
        wall[period_label(a)][a.bucket.value] += a.balloon_balance
    maturity_wall = [
        MaturityPeriod(period=label, balance_by_bucket={k: round(v, 2) for k, v in wall[label].items()})
        for label in sorted(wall, key=_period_sort_key)
    ]

    return PortfolioSummary(
        loans_in_window=len(assessments),
        loans_outside_window=loans_outside_window,
        total_balance=round(total_balance, 2),
        total_refi_gap=round(sum(a.refi_gap for a in assessments), 2),
        at_risk_balance=round(at_risk, 2),
        at_risk_pct=round(at_risk / total_balance, 4) if total_balance else 0.0,
        indicative_fee_pool=round(sum(a.indicative_fee for a in assessments), 2),
        by_bucket=by_bucket,
        by_property_type=by_type,
        maturity_wall=maturity_wall,
    )


def compare(
    baseline: list[LoanAssessment], stressed: list[LoanAssessment], baseline_name: str, top_n: int = 10
) -> ScenarioComparison:
    base_by_id = {a.loan_id: a for a in baseline}
    migrations: list[Migration] = []
    transitions: Counter[str] = Counter()
    for s in stressed:
        b = base_by_id.get(s.loan_id)
        if b is None or b.bucket is s.bucket:
            continue
        transitions[f"{b.bucket.value}->{s.bucket.value}"] += 1
        if s.bucket.severity > b.bucket.severity:
            migrations.append(
                Migration(
                    loan_id=s.loan_id,
                    property_name=s.property_name,
                    property_type=s.property_type,
                    balance=s.balloon_balance,
                    from_bucket=b.bucket,
                    to_bucket=s.bucket,
                )
            )
    migrations.sort(key=lambda m: m.balance, reverse=True)
    return ScenarioComparison(
        baseline=baseline_name,
        loans_worsened=len(migrations),
        balance_worsened=round(sum(m.balance for m in migrations), 2),
        refi_gap_delta=round(sum(a.refi_gap for a in stressed) - sum(a.refi_gap for a in baseline), 2),
        transitions=dict(transitions),
        top_migrations=migrations[:top_n],
    )


def analyze_portfolio(
    loans: list[Loan],
    market: MarketAssumptions,
    scenario: Scenario,
    *,
    compare_to: Scenario | None = None,
    property_types: set[PropertyType] | None = None,
) -> PortfolioAnalysis:
    """Assess all loans maturing inside the horizon, ranked by broker priority.

    When `compare_to` is given (defaults to the base preset for any non-base scenario), the result
    also shows which loans move to a worse bucket, which is the "what does this shock cost us" view.
    """
    universe = [loan for loan in loans if property_types is None or loan.property_type in property_types]
    window = [loan for loan in universe if in_window(loan, market)]

    assessments = sorted(
        (assess_loan(loan, market, scenario) for loan in window),
        key=lambda a: (-a.priority.score, a.maturity_date, a.loan_id),
    )

    if compare_to is None and scenario.name != "base":
        compare_to = get_preset("base")
    comparison = None
    if compare_to is not None:
        baseline = [assess_loan(loan, market, compare_to) for loan in window]
        comparison = compare(baseline, assessments, compare_to.name)

    return PortfolioAnalysis(
        scenario=scenario,
        as_of=market.as_of,
        summary=summarize(assessments, loans_outside_window=len(universe) - len(window)),
        assessments=assessments,
        comparison=comparison,
    )
