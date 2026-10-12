"""Re-underwrites a single loan under a scenario and turns the numbers into a decision."""

from __future__ import annotations

import math
from datetime import date

from .models import (
    LenderConstraints,
    Loan,
    LoanAssessment,
    MarketAssumptions,
    PriorityBreakdown,
    PropertyType,
    RiskBucket,
    Scenario,
)
from .scenarios import stress_operations
from .underwriting import (
    annual_debt_service,
    months_between,
    remaining_balance,
    size_refinance,
    value_from_noi,
)

PRIORITY_WEIGHTS = {"urgency": 0.50, "size": 0.30, "advisory_need": 0.20}

# How much advisory work (and fee) a bucket typically generates; refi-ready deals are
# still worth a call, but recaps and distress need a broker most.
ADVISORY_NEED = {
    RiskBucket.REFI_READY: 0.25,
    RiskBucket.EQUITY_GAP: 0.60,
    RiskBucket.RECAP_REQUIRED: 1.00,
    RiskBucket.DISTRESSED: 1.00,
}

BINDING_LABELS = {"LTV": "loan-to-value", "DSCR": "income coverage (DSCR)", "Debt Yield": "debt yield"}

SIZE_FLOOR = 1_000_000
SIZE_CEILING = 500_000_000


RATIO_CAP = 99.0


def _ratio(numerator: float, denominator: float) -> float:
    """Division that stays JSON-safe: caps instead of returning inf when the denominator is ~0."""
    if denominator <= 0:
        return RATIO_CAP if numerator > 0 else 0.0
    return max(-RATIO_CAP, min(RATIO_CAP, numerator / denominator))


def months_until(as_of: date, target: date) -> int:
    return months_between(as_of.year, as_of.month, target.year, target.month)


def add_months(d: date, months: int) -> date:
    total = d.year * 12 + (d.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def _size_score(balance: float) -> float:
    if balance <= SIZE_FLOOR:
        return 0.0
    return min(1.0, math.log10(balance / SIZE_FLOOR) / math.log10(SIZE_CEILING / SIZE_FLOOR))


def _urgency_score(months_to_maturity: int, horizon_months: int) -> float:
    if months_to_maturity <= 0:
        return 1.0
    return max(0.0, 1 - months_to_maturity / horizon_months)


LENDER_LABELS = {
    "CMBS": "the CMBS special servicer",
    "Bank": "the bank",
    "Life Co": "the life insurance lender",
    "Debt Fund": "the debt fund",
    "Agency": "the Agency lender",
}


def lender_contact_label(lender_type: str) -> str:
    """CMBS loans in default are run by a special servicer, not the original lender."""
    return LENDER_LABELS.get(lender_type, f"the {lender_type.lower()} lender")


def _recommended_action(bucket: RiskBucket, property_type: PropertyType, underwater: bool) -> str:
    match bucket:
        case RiskBucket.REFI_READY:
            source = (
                "Agency (Fannie Mae / Freddie Mac)"
                if property_type is PropertyType.MULTIFAMILY
                else "bank, life company or CMBS"
            )
            return f"Debt placement: refinance with {source} execution"
        case RiskBucket.EQUITY_GAP:
            return "Cash-in refinance: place senior debt; sponsor or preferred equity funds the gap"
        case RiskBucket.RECAP_REQUIRED:
            return "Recapitalization: senior debt plus JV/preferred equity, or negotiate extension"
        case RiskBucket.DISTRESSED if underwater:
            return "Loan sale advisory: lender sells the note or accepts a discounted payoff; owner has no equity left"
        case RiskBucket.DISTRESSED:
            return "Investment sale (or recap): owner still has equity but income can't cover payments"


def _next_step(bucket: RiskBucket, property_type: PropertyType, underwater: bool) -> str:
    match bucket:
        case RiskBucket.REFI_READY:
            return "Pitch Agency refinance" if property_type is PropertyType.MULTIFAMILY else "Pitch refinance"
        case RiskBucket.EQUITY_GAP:
            return "Pitch refi + gap equity"
        case RiskBucket.RECAP_REQUIRED:
            return "Pitch recapitalization"
        case RiskBucket.DISTRESSED if underwater:
            return "Pitch note sale to lender"
        case RiskBucket.DISTRESSED:
            return "Pitch asset sale"


def _money(value: float) -> str:
    if abs(value) >= 1e9:
        return f"${value / 1e9:,.2f}B"
    return f"${value / 1e6:,.1f}M"


def _new_loan_limit(binding: str, property_type: PropertyType, constraints: LenderConstraints, rate: float) -> str:
    """Plain-English reason the new loan is capped where it is."""
    kind = property_type.value
    match binding:
        case "LTV":
            return f"lenders cap {kind} loans at {constraints.max_ltv:.0%} of the property's value"
        case "DSCR":
            return (
                f"at a ~{rate:.2%} rate, the property's income can only cover payments on that much "
                f"(lenders want income of at least {constraints.min_dscr:.2f}x the yearly payments)"
            )
        case _:
            return f"lenders want yearly income of at least {constraints.min_debt_yield:.0%} of the loan amount"


def _talking_points(
    *,
    loan: Loan,
    bucket: RiskBucket,
    balloon: float,
    max_loan: float,
    gap: float,
    gap_pct: float,
    refi_rate: float,
    value: float,
    months_to_maturity: int,
    past_maturity: bool,
    underwater: bool,
    ltv: float,
    binding: str,
    constraints: LenderConstraints,
) -> list[str]:
    """Three call notes. Always says 'existing loan' or 'new loan' with a dollar figure so they can't be confused."""
    existing = f"existing {_money(balloon)} loan"
    due = loan.maturity_date.strftime("%b %Y")
    if past_maturity:
        timing = (
            f"The {existing} on {loan.property_name} matured in {due} and has not been repaid. "
            "The owner is likely negotiating an extension and needs a plan now."
        )
    else:
        timing = (
            f"The {existing} on {loan.property_name} comes due in {due} ({months_to_maturity} months away). "
            "Refinancing usually takes 3–6 months, so the owner should start now."
        )
    market = (
        f"The property is worth about {_money(value)} today. A new loan would be at most {_money(max_loan)}, "
        f"because {_new_loan_limit(binding, loan.property_type, constraints, refi_rate)}."
    )
    match bucket:
        case RiskBucket.REFI_READY:
            pitch = (
                f"The new loan fully covers the {existing}. Pitch: lock in a new loan early; Newmark runs a "
                "competitive lender process to get the best terms."
            )
        case RiskBucket.EQUITY_GAP:
            pitch = (
                f"That leaves a {_money(gap)} gap between the {existing} and the new one ({gap_pct:.0%}). "
                "Pitch: refinance now, with the owner adding cash or Newmark placing a small preferred equity "
                "piece to close the gap."
            )
        case RiskBucket.RECAP_REQUIRED:
            pitch = (
                f"That leaves a {_money(gap)} gap between the {existing} and the new one ({gap_pct:.0%}), too "
                "large for most owners to fund alone. Pitch: Newmark brings in a JV or preferred equity partner, "
                "or negotiates an extension with the current lender."
            )
        case RiskBucket.DISTRESSED if underwater:
            pitch = (
                f"The {existing} is now more than the {_money(value)} the property is worth ({ltv:.0%} "
                f"loan-to-value), so the owner has no equity left and {lender_contact_label(loan.lender_type)} "
                "will likely take a loss. Pitch to the lender: Newmark's loan sales team sells the note or "
                "negotiates a discounted payoff."
            )
        case RiskBucket.DISTRESSED:
            pitch = (
                f"The property ({_money(value)}) is still worth more than the {existing}, but its income no "
                "longer covers the current payments. Pitch to the owner: sell now while there is equity to "
                "protect (Newmark investment sales), or bring in a recapitalization partner."
            )
    return [timing, market, pitch]


def _classify(
    *, in_place_dscr: float, ltv: float, gap_pct: float, balloon: float, value: float, market: MarketAssumptions
) -> tuple[RiskBucket, str]:
    t = market.thresholds
    if in_place_dscr < t.distressed_dscr:
        return (
            RiskBucket.DISTRESSED,
            f"Income no longer covers the existing loan's payments (DSCR {in_place_dscr:.2f}x, below {t.distressed_dscr:.2f}x)",
        )
    if ltv >= t.distressed_ltv:
        return (
            RiskBucket.DISTRESSED,
            f"Existing loan ({_money(balloon)}) is more than the property's value ({_money(value)}), LTV {ltv:.0%}",
        )
    if gap_pct <= 0:
        return RiskBucket.REFI_READY, "A new loan at today's terms fully repays the existing loan"
    if gap_pct <= t.equity_gap_max_pct:
        return RiskBucket.EQUITY_GAP, f"Shortfall is {gap_pct:.0%} of the existing loan, small enough for most owners to cover"
    return RiskBucket.RECAP_REQUIRED, f"Shortfall is {gap_pct:.0%} of the existing loan, too large for most owners to cover alone"


def assess_loan(loan: Loan, market: MarketAssumptions, scenario: Scenario) -> LoanAssessment:
    pta = market.for_type(loan.property_type)
    as_of = market.as_of

    months_to_maturity = months_until(as_of, loan.maturity_date)
    past_maturity = loan.maturity_date < as_of
    term_months = months_until(loan.origination_date, loan.maturity_date)
    elapsed_months = min(term_months, months_until(loan.origination_date, as_of))

    balloon = remaining_balance(loan.original_balance, loan.note_rate, loan.amortization_years, term_months)
    current_balance = remaining_balance(
        loan.original_balance, loan.note_rate, loan.amortization_years, elapsed_months
    )

    ops = stress_operations(
        property_type=loan.property_type,
        noi=loan.current_noi,
        occupancy=loan.occupancy,
        opex_ratio=pta.opex_ratio,
        scenario=scenario,
    )
    cap_rate = pta.cap_rate + scenario.cap_rate_shock_bps / 10_000
    refi_rate = market.base_rate + pta.spread + scenario.rate_shock_bps / 10_000
    value = value_from_noi(ops.noi, cap_rate)

    sizing = size_refinance(
        noi=ops.noi,
        value=value,
        rate=refi_rate,
        amortization_years=market.refi_amortization_years,
        max_ltv=pta.constraints.max_ltv,
        min_dscr=pta.constraints.min_dscr,
        min_debt_yield=pta.constraints.min_debt_yield,
    )

    refi_gap = max(0.0, balloon - sizing.max_loan)
    gap_pct = _ratio(refi_gap, balloon)
    ltv = _ratio(balloon, value)
    debt_yield = _ratio(ops.noi, balloon)
    existing_ds = annual_debt_service(current_balance, loan.note_rate, loan.amortization_years)
    in_place_dscr = _ratio(ops.noi, existing_ds)
    refi_ds = annual_debt_service(balloon, refi_rate, market.refi_amortization_years)
    refi_dscr = _ratio(ops.noi, refi_ds)

    bucket, bucket_reason = _classify(
        in_place_dscr=in_place_dscr, ltv=ltv, gap_pct=gap_pct, balloon=balloon, value=value, market=market
    )

    # Underwater distressed loans are lender-driven (note sale); otherwise the owner sells the asset.
    underwater = bucket is RiskBucket.DISTRESSED and ltv >= market.thresholds.distressed_ltv
    fees = market.fees
    if bucket is RiskBucket.DISTRESSED:
        # A defaulted note trades at roughly the collateral value, so both fees key off property value.
        bps = fees.loan_sales_bps if underwater else fees.investment_sales_bps
        fee = value * bps / 10_000
    elif bucket is RiskBucket.REFI_READY:
        fee = balloon * fees.debt_placement_bps / 10_000
    else:
        fee = (sizing.max_loan * fees.debt_placement_bps + refi_gap * fees.equity_placement_bps) / 10_000

    urgency = _urgency_score(months_to_maturity, market.horizon_months)
    size = _size_score(balloon)
    need = ADVISORY_NEED[bucket]
    score = 100 * (
        PRIORITY_WEIGHTS["urgency"] * urgency
        + PRIORITY_WEIGHTS["size"] * size
        + PRIORITY_WEIGHTS["advisory_need"] * need
    )

    noi_change = ops.noi / loan.current_noi - 1 if loan.current_noi else 0.0
    value_change = value / loan.original_value - 1 if loan.original_value else 0.0
    reasons = [
        bucket_reason,
        (
            f"Yearly income (NOI) {_money(ops.noi)}, {noi_change:+.0%} vs today in this scenario"
            if abs(noi_change) >= 0.005
            else f"Yearly income (NOI) {_money(ops.noi)}, unchanged in this scenario"
        ),
        f"Worth about {_money(value)} at a {cap_rate:.2%} cap rate ({value_change:+.0%} vs when the loan was made)",
        f"A new loan costs {refi_rate:.2%} vs {loan.note_rate:.2%} on the existing loan",
        f"A new loan would be at most {_money(sizing.max_loan)}, limited by the {BINDING_LABELS[sizing.binding_constraint]} test",
    ]
    if past_maturity:
        reasons.insert(0, f"Matured {loan.maturity_date.isoformat()} and still outstanding")

    return LoanAssessment(
        loan_id=loan.loan_id,
        property_name=loan.property_name,
        property_type=loan.property_type,
        market=loan.market,
        sponsor=loan.sponsor,
        lender_type=loan.lender_type,
        maturity_date=loan.maturity_date,
        months_to_maturity=months_to_maturity,
        past_maturity=past_maturity,
        balloon_balance=round(balloon, 2),
        stressed_noi=round(ops.noi, 2),
        stressed_occupancy=round(ops.occupancy, 4),
        stressed_value=round(value, 2),
        refi_rate=round(refi_rate, 6),
        max_refi_loan=round(sizing.max_loan, 2),
        binding_constraint=sizing.binding_constraint,
        refi_gap=round(refi_gap, 2),
        refi_gap_pct=round(gap_pct, 4),
        ltv_at_maturity=round(ltv, 4),
        debt_yield=round(debt_yield, 4),
        in_place_dscr=round(in_place_dscr, 3),
        refi_dscr=round(refi_dscr, 3),
        bucket=bucket,
        next_step=_next_step(bucket, loan.property_type, underwater),
        call_target="lender" if underwater else "owner",
        recommended_action=_recommended_action(bucket, loan.property_type, underwater),
        indicative_fee=round(fee, 2),
        priority=PriorityBreakdown(
            urgency=round(urgency, 3),
            size=round(size, 3),
            advisory_need=need,
            score=round(score, 1),
        ),
        reasons=reasons,
        talking_points=_talking_points(
            loan=loan,
            bucket=bucket,
            balloon=balloon,
            max_loan=sizing.max_loan,
            gap=refi_gap,
            gap_pct=gap_pct,
            refi_rate=refi_rate,
            value=value,
            months_to_maturity=months_to_maturity,
            past_maturity=past_maturity,
            underwater=underwater,
            ltv=ltv,
            binding=sizing.binding_constraint,
            constraints=pta.constraints,
        ),
    )
