"""Deterministic synthetic loan tape that looks like a lender/servicer maturity schedule.

All names are fictional. Distributions are shaped to resemble the 2026-2029 maturity wall:
loans written in the low-rate 2016-2022 era, office NOI eroding since origination,
industrial and multifamily NOI growing, and heavy interest-only usage in CMBS/debt-fund paper.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date

from .contacts import SponsorContact
from .models import Loan, PropertyType

# Approximate 10Y UST by origination year; used only to make coupons look era-appropriate.
HISTORICAL_BASE_RATE = {
    2016: 0.018, 2017: 0.023, 2018: 0.029, 2019: 0.021, 2020: 0.009,
    2021: 0.015, 2022: 0.030, 2023: 0.040, 2024: 0.042, 2025: 0.043,
}


@dataclass(frozen=True, slots=True)
class TypeProfile:
    weight: float
    origination_spread: float
    origination_cap_rate: float
    annual_noi_growth: float
    noi_growth_noise: float
    occupancy_range: tuple[float, float]
    value_range: tuple[float, float]
    names: tuple[str, ...]


PROFILES: dict[PropertyType, TypeProfile] = {
    PropertyType.MULTIFAMILY: TypeProfile(
        0.32, 0.0170, 0.0475, 0.030, 0.020, (0.88, 0.97), (15e6, 250e6),
        ("Apartments", "Residences", "Lofts", "Flats"),
    ),
    PropertyType.OFFICE: TypeProfile(
        0.24, 0.0200, 0.0575, -0.010, 0.035, (0.62, 0.92), (20e6, 300e6),
        ("Tower", "Plaza", "Corporate Center", "Office Park"),
    ),
    PropertyType.INDUSTRIAL: TypeProfile(
        0.18, 0.0180, 0.0500, 0.050, 0.020, (0.86, 1.00), (12e6, 200e6),
        ("Logistics Center", "Distribution Park", "Commerce Center"),
    ),
    PropertyType.RETAIL: TypeProfile(
        0.14, 0.0220, 0.0650, 0.015, 0.020, (0.80, 0.97), (10e6, 150e6),
        ("Marketplace", "Town Center", "Shopping Center"),
    ),
    PropertyType.HOTEL: TypeProfile(
        0.12, 0.0280, 0.0725, 0.025, 0.030, (0.58, 0.82), (20e6, 220e6),
        ("Hotel", "Suites", "Resort"),
    ),
}

LENDERS: dict[str, dict] = {
    "Agency": {"terms": (7, 10), "ltv": (0.60, 0.75), "io_share": 0.20, "amort": 30, "spread_adj": -0.002},
    "CMBS": {"terms": (5, 10), "ltv": (0.58, 0.70), "io_share": 0.65, "amort": 30, "spread_adj": 0.0},
    "Bank": {"terms": (5, 7), "ltv": (0.55, 0.68), "io_share": 0.15, "amort": 25, "spread_adj": 0.001},
    "Life Co": {"terms": (7, 10), "ltv": (0.45, 0.60), "io_share": 0.30, "amort": 30, "spread_adj": -0.003},
    "Debt Fund": {"terms": (3, 5), "ltv": (0.65, 0.78), "io_share": 1.00, "amort": 30, "spread_adj": 0.015},
}

PREFIXES = (
    "Harbor Point", "Lakeside", "Meridian", "Riverside", "Summit", "Cedar Ridge", "Union", "Crescent",
    "Gateway", "Northgate", "Willow Creek", "Ironwood", "Bayview", "Highland", "Liberty", "Foundry",
    "Granite", "Larkspur", "Copper Hill", "Westbrook", "Ashgrove", "Stonebridge", "Elm Street", "Pioneer",
)
MARKETS = (
    "New York, NY", "Chicago, IL", "San Francisco, CA", "Los Angeles, CA", "Dallas, TX", "Houston, TX",
    "Atlanta, GA", "Washington, DC", "Boston, MA", "Seattle, WA", "Denver, CO", "Phoenix, AZ",
    "Miami, FL", "Charlotte, NC", "Nashville, TN", "Austin, TX",
)
SPONSORS = (
    "Northbeam Capital", "Cobalt Ridge Partners", "Lantern Real Estate", "Silverline Holdings",
    "Kestrel Property Group", "Oakhaven Investors", "Tidewater Equities", "Pinecrest Realty",
    "Halcyon Street Partners", "Brightwater Asset Mgmt", "Quarry Lane Capital", "Redfern Holdings",
)

MATURITY_START = date(2026, 4, 1)
MATURITY_END = date(2030, 6, 30)


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + years, day=28)


def _lender_for(rng: random.Random, property_type: PropertyType) -> str:
    if property_type is PropertyType.MULTIFAMILY:
        return rng.choices(["Agency", "Bank", "Life Co", "Debt Fund"], weights=[60, 15, 10, 15])[0]
    if property_type is PropertyType.HOTEL:
        return rng.choices(["CMBS", "Bank", "Debt Fund"], weights=[45, 25, 30])[0]
    return rng.choices(["CMBS", "Bank", "Life Co", "Debt Fund"], weights=[35, 30, 20, 15])[0]


def generate_loans(count: int = 250, seed: int = 42) -> list[Loan]:
    rng = random.Random(seed)
    types = list(PROFILES)
    weights = [PROFILES[t].weight for t in types]
    span_days = (MATURITY_END - MATURITY_START).days

    loans: list[Loan] = []
    for i in range(1, count + 1):
        pt = rng.choices(types, weights=weights)[0]
        profile = PROFILES[pt]
        lender = _lender_for(rng, pt)
        terms = LENDERS[lender]

        maturity = date.fromordinal(MATURITY_START.toordinal() + rng.randrange(span_days)).replace(day=1)
        term = rng.choice(terms["terms"])
        origination = _add_years(maturity, -term)
        if origination.year < min(HISTORICAL_BASE_RATE):
            origination = origination.replace(year=min(HISTORICAL_BASE_RATE))
        vintage_rate = HISTORICAL_BASE_RATE.get(origination.year, 0.043)

        note_rate = max(
            0.025,
            vintage_rate + profile.origination_spread + terms["spread_adj"] + rng.uniform(-0.003, 0.003),
        )
        low_rate_vintage = origination.year in (2020, 2021, 2022)
        orig_cap = profile.origination_cap_rate - (0.005 if low_rate_vintage else 0.0) + rng.uniform(-0.004, 0.004)

        lo, hi = profile.value_range
        original_value = round(lo * (hi / lo) ** rng.random(), -4)
        original_noi = original_value * orig_cap
        original_balance = round(original_value * rng.uniform(*terms["ltv"]), -4)

        years_held = max(0.0, (date(2026, 10, 1) - origination).days / 365.25)
        annual_growth = rng.gauss(profile.annual_noi_growth, profile.noi_growth_noise)
        current_noi = original_noi * max(0.50, (1 + annual_growth) ** years_held)

        amortization = 0 if rng.random() < terms["io_share"] else terms["amort"]

        loans.append(
            Loan(
                loan_id=f"NMK-{i:05d}",
                property_name=f"{rng.choice(PREFIXES)} {rng.choice(profile.names)}",
                property_type=pt,
                market=rng.choice(MARKETS),
                sponsor=rng.choice(SPONSORS),
                lender_type=lender,
                origination_date=origination,
                maturity_date=maturity,
                original_balance=original_balance,
                note_rate=round(note_rate, 5),
                amortization_years=amortization,
                original_value=original_value,
                current_noi=round(current_noi, 2),
                occupancy=round(rng.uniform(*profile.occupancy_range), 4),
            )
        )
    return loans


FIRST_NAMES = (
    "Alex", "Jordan", "Priya", "Marcus", "Elena", "David", "Sofia", "Kenji", "Rachel", "Omar", "Laura", "Ben",
)
LAST_NAMES = (
    "Chen", "Ramirez", "Patel", "Okafor", "Novak", "Brennan", "Lindqvist", "Tanaka", "Goldberg", "Haddad",
    "Moreau", "Sullivan",
)
TITLES = ("Managing Director, Capital Markets", "CFO", "Head of Asset Management", "Principal", "VP, Finance")
COVERAGE_BROKERS = ("J. Rivera (DSF)", "M. Shah (DSF)", "K. O'Neill (DSF)", "T. Brooks (Loan Sales)")


def generate_contacts(seed: int = 42) -> list[SponsorContact]:
    """One fictional decision-maker per synthetic sponsor. Phone numbers use the reserved 555-01xx range."""
    rng = random.Random(seed)
    contacts = []
    for i, sponsor in enumerate(SPONSORS):
        first, last = FIRST_NAMES[i], rng.choice(LAST_NAMES)
        domain = "".join(ch for ch in sponsor.lower() if ch.isalnum())
        contacts.append(
            SponsorContact(
                sponsor=sponsor,
                contact_name=f"{first} {last}",
                title=rng.choice(TITLES),
                phone=f"(212) 555-01{i:02d}",
                email=f"{first.lower()}.{last.lower()}@{domain}.example.com",
                coverage_broker=rng.choice(COVERAGE_BROKERS),
            )
        )
    return contacts
