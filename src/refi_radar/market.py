"""Loads market assumptions from TOML so analysts can change them without a code change."""

from __future__ import annotations

import tomllib
from datetime import date
from importlib import resources
from pathlib import Path

from .models import (
    BucketThresholds,
    FeeAssumptions,
    LenderConstraints,
    MarketAssumptions,
    PropertyType,
    PropertyTypeAssumptions,
)


class MarketConfigError(ValueError):
    pass


def default_market_path() -> Path:
    return Path(str(resources.files("refi_radar") / "config" / "market_assumptions.toml"))


def load_market(path: str | Path | None = None) -> MarketAssumptions:
    path = Path(path) if path else default_market_path()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return parse_market(raw)


def parse_market(raw: dict) -> MarketAssumptions:
    try:
        as_of = raw["as_of"]
        if not isinstance(as_of, date):
            as_of = date.fromisoformat(str(as_of))

        types_raw = raw["property_types"]
        property_types: dict[PropertyType, PropertyTypeAssumptions] = {}
        for pt in PropertyType:
            if pt.value not in types_raw:
                raise MarketConfigError(f"missing assumptions for property type '{pt.value}'")
            t = types_raw[pt.value]
            property_types[pt] = PropertyTypeAssumptions(
                spread=float(t["spread"]),
                cap_rate=float(t["cap_rate"]),
                opex_ratio=float(t["opex_ratio"]),
                constraints=LenderConstraints(
                    max_ltv=float(t["max_ltv"]),
                    min_dscr=float(t["min_dscr"]),
                    min_debt_yield=float(t["min_debt_yield"]),
                ),
            )
            if not 0 < property_types[pt].cap_rate < 0.25:
                raise MarketConfigError(f"implausible cap rate for {pt.value}")
            if not 0 <= property_types[pt].opex_ratio < 1:
                raise MarketConfigError(f"opex_ratio for {pt.value} must be in [0, 1)")

        fees = raw["fees"]
        thresholds = raw["thresholds"]
        return MarketAssumptions(
            as_of=as_of,
            base_rate=float(raw["base_rate"]),
            refi_amortization_years=int(raw["refi_amortization_years"]),
            horizon_months=int(raw["horizon_months"]),
            property_types=property_types,
            fees=FeeAssumptions(
                debt_placement_bps=float(fees["debt_placement_bps"]),
                equity_placement_bps=float(fees["equity_placement_bps"]),
                investment_sales_bps=float(fees["investment_sales_bps"]),
                loan_sales_bps=float(fees["loan_sales_bps"]),
            ),
            thresholds=BucketThresholds(
                equity_gap_max_pct=float(thresholds["equity_gap_max_pct"]),
                distressed_ltv=float(thresholds["distressed_ltv"]),
                distressed_dscr=float(thresholds["distressed_dscr"]),
            ),
            source_note=str(raw.get("source_note", "")),
        )
    except KeyError as exc:
        raise MarketConfigError(f"missing market assumption: {exc}") from exc
