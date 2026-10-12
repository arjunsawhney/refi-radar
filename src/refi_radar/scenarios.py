"""Stress scenarios and how they transform a property's operating profile."""

from __future__ import annotations

from dataclasses import dataclass

from .models import PropertyType, Scenario

PRESETS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario(
            name="base",
            label="Base Case",
            description=(
                "Today's market as it is: current interest rates and property values, no extra stress. "
                "Use this to see which loans are already in trouble."
            ),
        ),
        Scenario(
            name="higher_for_longer",
            label="Higher for Longer",
            description=(
                "Interest rates stay high instead of falling. New loans cost 0.75% more and investors "
                "pay slightly less for buildings (cap rates +0.25%)."
            ),
            rate_shock_bps=75,
            cap_rate_shock_bps=25,
        ),
        Scenario(
            name="office_vacancy_shock",
            label="Office Vacancy Shock",
            description=(
                "More companies shrink their offices as leases end: office buildings lose 15% of their "
                "occupancy while their running costs stay the same. Other property types are unaffected."
            ),
            office_vacancy_shock_pp=0.15,
        ),
        Scenario(
            name="severe",
            label="Severe Downturn",
            description=(
                "Several things go wrong at once: rates +1%, property values fall (cap rates +0.5%), "
                "rental income drops 5% everywhere, and offices lose 20% of their occupancy."
            ),
            rate_shock_bps=100,
            cap_rate_shock_bps=50,
            noi_haircut_pct=0.05,
            office_vacancy_shock_pp=0.20,
        ),
    )
}


class UnknownScenarioError(KeyError):
    pass


def get_preset(name: str) -> Scenario:
    try:
        return PRESETS[name]
    except KeyError as exc:
        raise UnknownScenarioError(f"unknown scenario '{name}'; choose from {sorted(PRESETS)}") from exc


@dataclass(frozen=True, slots=True)
class StressedOperations:
    noi: float
    occupancy: float


def apply_vacancy_shock(
    noi: float, occupancy: float, opex_ratio: float, occupancy_loss: float
) -> StressedOperations:
    """Re-derive NOI after an occupancy drop, holding operating expenses fixed.

    NOI = EGI - OpEx, where OpEx = opex_ratio * EGI today. Revenue scales with occupancy but
    most expenses (taxes, insurance, staffing) do not, so a 15-point occupancy loss on an
    office building can remove ~30% of NOI. That operating leverage is why office refis break.
    """
    if occupancy_loss <= 0 or occupancy <= 0:
        return StressedOperations(noi=noi, occupancy=occupancy)
    new_occupancy = max(0.0, occupancy - occupancy_loss)
    egi = noi / (1 - opex_ratio)
    opex = egi - noi
    potential_gross = egi / occupancy
    new_egi = potential_gross * new_occupancy
    return StressedOperations(noi=new_egi - opex, occupancy=new_occupancy)


def stress_operations(
    *,
    property_type: PropertyType,
    noi: float,
    occupancy: float,
    opex_ratio: float,
    scenario: Scenario,
) -> StressedOperations:
    ops = StressedOperations(noi=noi, occupancy=occupancy)
    if property_type is PropertyType.OFFICE and scenario.office_vacancy_shock_pp > 0:
        ops = apply_vacancy_shock(noi, occupancy, opex_ratio, scenario.office_vacancy_shock_pp)
    if scenario.noi_haircut_pct > 0:
        ops = StressedOperations(noi=ops.noi * (1 - scenario.noi_haircut_pct), occupancy=ops.occupancy)
    return ops
