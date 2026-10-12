"""Domain model. Pure dataclasses so the engine has no framework dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class PropertyType(StrEnum):
    OFFICE = "office"
    MULTIFAMILY = "multifamily"
    INDUSTRIAL = "industrial"
    RETAIL = "retail"
    HOTEL = "hotel"


class RiskBucket(StrEnum):
    """Ordered from healthiest to worst. Order matters for migration analysis."""

    REFI_READY = "refi_ready"
    EQUITY_GAP = "equity_gap"
    RECAP_REQUIRED = "recap_required"
    DISTRESSED = "distressed"

    @property
    def severity(self) -> int:
        return list(RiskBucket).index(self)


@dataclass(frozen=True, slots=True)
class Loan:
    loan_id: str
    property_name: str
    property_type: PropertyType
    market: str
    sponsor: str
    lender_type: str
    origination_date: date
    maturity_date: date
    original_balance: float
    note_rate: float
    """Annual coupon as a decimal (0.045 == 4.50%)."""
    amortization_years: int
    """0 means full-term interest-only."""
    original_value: float
    current_noi: float
    """Trailing-12 net operating income."""
    occupancy: float
    """Physical occupancy as a decimal."""


@dataclass(frozen=True, slots=True)
class LenderConstraints:
    max_ltv: float
    min_dscr: float
    min_debt_yield: float


@dataclass(frozen=True, slots=True)
class PropertyTypeAssumptions:
    spread: float
    """Lender spread over the base rate, decimal."""
    cap_rate: float
    """Current market cap rate used to re-value the asset."""
    opex_ratio: float
    """Operating expenses as a share of effective gross income; drives operating leverage."""
    constraints: LenderConstraints


@dataclass(frozen=True, slots=True)
class FeeAssumptions:
    debt_placement_bps: float
    equity_placement_bps: float
    investment_sales_bps: float
    loan_sales_bps: float


@dataclass(frozen=True, slots=True)
class BucketThresholds:
    equity_gap_max_pct: float
    """Refi gaps up to this share of the balloon are a 'cash-in refi'; above is a recap."""
    distressed_ltv: float
    """Loan-to-value at maturity at or above which the asset is considered underwater."""
    distressed_dscr: float
    """In-place DSCR (at the existing note rate) below which the loan is already in trouble."""


@dataclass(frozen=True, slots=True)
class MarketAssumptions:
    as_of: date
    base_rate: float
    """Benchmark (e.g. 10Y UST) as a decimal."""
    refi_amortization_years: int
    horizon_months: int
    property_types: dict[PropertyType, PropertyTypeAssumptions]
    fees: FeeAssumptions
    thresholds: BucketThresholds
    source_note: str = ""

    def for_type(self, property_type: PropertyType) -> PropertyTypeAssumptions:
        return self.property_types[property_type]


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    description: str
    label: str = ""
    """Human-friendly title shown in the UI."""
    rate_shock_bps: float = 0.0
    cap_rate_shock_bps: float = 0.0
    noi_haircut_pct: float = 0.0
    """Applied to every property type, decimal (0.05 == 5% NOI decline)."""
    office_vacancy_shock_pp: float = 0.0
    """Occupancy points lost on office assets at rollover, decimal (0.15 == 15 points)."""


@dataclass(frozen=True, slots=True)
class PriorityBreakdown:
    """Explainable 0-100 score so a broker can see *why* a loan is at the top of the list."""

    urgency: float
    size: float
    advisory_need: float
    score: float


@dataclass(frozen=True, slots=True)
class LoanAssessment:
    loan_id: str
    property_name: str
    property_type: PropertyType
    market: str
    sponsor: str
    lender_type: str
    maturity_date: date
    months_to_maturity: int
    past_maturity: bool
    balloon_balance: float
    stressed_noi: float
    stressed_occupancy: float
    stressed_value: float
    refi_rate: float
    max_refi_loan: float
    binding_constraint: str
    refi_gap: float
    refi_gap_pct: float
    ltv_at_maturity: float
    debt_yield: float
    in_place_dscr: float
    refi_dscr: float
    bucket: RiskBucket
    next_step: str
    """Short, table-friendly verb phrase, e.g. 'Pitch recapitalization'."""
    call_target: str
    """Who the pitch is for: 'owner' (sponsor/borrower) or 'lender' (note sales)."""
    recommended_action: str
    indicative_fee: float
    priority: PriorityBreakdown
    reasons: list[str] = field(default_factory=list)
    talking_points: list[str] = field(default_factory=list)
    """Plain-English call notes generated from the numbers."""
