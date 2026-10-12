"""Pure lending math. No I/O, no domain objects beyond primitives, so it is trivially testable."""

from __future__ import annotations

from dataclasses import dataclass


def annual_debt_constant(rate: float, amortization_years: int) -> float:
    """Annual debt service per $1 of loan. Interest-only when amortization_years == 0."""
    if amortization_years <= 0:
        return rate
    if rate == 0:
        return 1 / amortization_years
    r = rate / 12
    n = amortization_years * 12
    return 12 * r / (1 - (1 + r) ** -n)


def annual_debt_service(balance: float, rate: float, amortization_years: int) -> float:
    return balance * annual_debt_constant(rate, amortization_years)


def remaining_balance(
    original_balance: float, rate: float, amortization_years: int, months_elapsed: int
) -> float:
    """Outstanding principal after `months_elapsed` payments on a level-payment mortgage."""
    if amortization_years <= 0 or months_elapsed <= 0:
        return original_balance
    n = amortization_years * 12
    k = min(months_elapsed, n)
    if rate == 0:
        return original_balance * (1 - k / n)
    r = rate / 12
    growth = (1 + r) ** k
    payment = original_balance * r / (1 - (1 + r) ** -n)
    return max(0.0, original_balance * growth - payment * (growth - 1) / r)


def value_from_noi(noi: float, cap_rate: float) -> float:
    if cap_rate <= 0:
        raise ValueError("cap_rate must be positive")
    return max(0.0, noi) / cap_rate


@dataclass(frozen=True, slots=True)
class SizingResult:
    max_loan: float
    binding_constraint: str
    ltv_limit: float
    dscr_limit: float
    debt_yield_limit: float


def size_refinance(
    *,
    noi: float,
    value: float,
    rate: float,
    amortization_years: int,
    max_ltv: float,
    min_dscr: float,
    min_debt_yield: float,
) -> SizingResult:
    """Largest new loan a lender would make: the minimum of the three standard sizing tests."""
    noi = max(0.0, noi)
    ltv_limit = value * max_ltv
    dscr_limit = noi / min_dscr / annual_debt_constant(rate, amortization_years)
    debt_yield_limit = noi / min_debt_yield
    limits = {"LTV": ltv_limit, "DSCR": dscr_limit, "Debt Yield": debt_yield_limit}
    binding = min(limits, key=limits.__getitem__)
    return SizingResult(
        max_loan=limits[binding],
        binding_constraint=binding,
        ltv_limit=ltv_limit,
        dscr_limit=dscr_limit,
        debt_yield_limit=debt_yield_limit,
    )


def months_between(start_year: int, start_month: int, end_year: int, end_month: int) -> int:
    return (end_year - start_year) * 12 + (end_month - start_month)
