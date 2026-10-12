"""HTTP adapter over the engine. Holds no business logic: validate, delegate, serialise."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .assessment import add_months, assess_loan
from .contacts import SponsorContact, default_contacts_path, load_contacts
from .ingest import IngestResult, RowError, TapeFormatError, load_tape, parse_tape_text
from .market import load_market
from .models import LoanAssessment, MarketAssumptions, PropertyType, RiskBucket, Scenario
from .portfolio import PortfolioAnalysis, analyze_portfolio, filter_assessments
from .scenarios import PRESETS, UnknownScenarioError, get_preset
from .synthetic import generate_contacts, generate_loans

STATIC_DIR = Path(str(resources.files("refi_radar") / "web" / "static"))


class ScenarioOverrides(BaseModel):
    rate_shock_bps: float | None = Field(None, ge=-300, le=500, description="Added to refi coupons.")
    cap_rate_shock_bps: float | None = Field(None, ge=-200, le=400, description="Added to exit cap rates.")
    noi_haircut_pct: float | None = Field(None, ge=0, le=0.6, description="NOI decline on all assets (0.05 = 5%).")
    office_vacancy_shock_pp: float | None = Field(
        None, ge=0, le=0.6, description="Occupancy points lost on office assets (0.15 = 15 points)."
    )


class CallListFilter(BaseModel):
    """Narrows only the returned call list; summary and charts still describe the whole portfolio."""

    property_type: PropertyType | None = None
    bucket: RiskBucket | None = None
    period: str | None = Field(None, description="Maturity quarter label, e.g. '2027 Q3' or 'Past due'.")


class AnalyzeRequest(BaseModel):
    scenario: str = Field("base", description=f"Preset to start from: {sorted(PRESETS)}")
    overrides: ScenarioOverrides | None = Field(None, description="Optional shocks layered on the preset.")
    property_types: list[PropertyType] | None = Field(None, description="Restrict to these property types.")
    top_n: int = Field(50, ge=1, le=5000, description="Number of ranked loans to return.")
    call_list_filter: CallListFilter | None = Field(None, description="Optional drill-down for the call list.")


class HealthResponse(BaseModel):
    status: str
    version: str
    as_of: str
    horizon_months: int
    window_end: str
    tape_source: str
    loans_loaded: int
    data_quality: dict[str, int]


class TapeValidationResponse(BaseModel):
    data_quality: dict[str, int]
    errors: list[RowError]


@dataclass(slots=True)
class AppState:
    loans: list
    market: MarketAssumptions
    tape_source: str
    ingest: IngestResult
    contacts: dict[str, SponsorContact]


def _load_state(tape_path: str | None, market_path: str | None, contacts_path: str | None) -> AppState:
    market = load_market(market_path)
    if tape_path:
        ingest = load_tape(tape_path)
        source = str(tape_path)
        sibling = default_contacts_path(tape_path)
        contacts_path = contacts_path or (str(sibling) if sibling.exists() else None)
        contacts = load_contacts(contacts_path) if contacts_path else {}
    else:
        ingest = IngestResult(loans=generate_loans())
        source = "synthetic (seed=42, n=250)"
        contacts = load_contacts(contacts_path) if contacts_path else {c.sponsor: c for c in generate_contacts()}
    return AppState(loans=ingest.loans, market=market, tape_source=source, ingest=ingest, contacts=contacts)


def _resolve_scenario(name: str, overrides: ScenarioOverrides | None) -> Scenario:
    try:
        scenario = get_preset(name)
    except UnknownScenarioError as exc:
        raise HTTPException(status_code=422, detail=str(exc.args[0])) from exc
    if overrides:
        changes = overrides.model_dump(exclude_none=True)
        if changes:
            scenario = replace(
                scenario,
                name="custom",
                label="Custom Scenario",
                description=f"Your adjustments, starting from {scenario.label or name}.",
                **changes,
            )
    return scenario


def create_app(
    tape_path: str | None = None, market_path: str | None = None, contacts_path: str | None = None
) -> FastAPI:
    tape_path = tape_path or os.environ.get("REFI_RADAR_TAPE")
    market_path = market_path or os.environ.get("REFI_RADAR_MARKET")
    contacts_path = contacts_path or os.environ.get("REFI_RADAR_CONTACTS")
    state = _load_state(tape_path, market_path, contacts_path)

    app = FastAPI(
        title="Refi Radar",
        version=__version__,
        description=(
            "Stress-tests commercial real estate loans approaching maturity, sizes the refinance gap, "
            "and ranks the resulting advisory opportunities for Debt & Structured Finance brokers."
        ),
    )
    app.state.radar = state

    @app.get("/api/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            as_of=state.market.as_of.isoformat(),
            horizon_months=state.market.horizon_months,
            window_end=add_months(state.market.as_of, state.market.horizon_months).isoformat(),
            tape_source=state.tape_source,
            loans_loaded=len(state.loans),
            data_quality=state.ingest.data_quality(),
        )

    @app.get("/api/scenarios", response_model=list[Scenario], tags=["reference"])
    def scenarios() -> list[Scenario]:
        return list(PRESETS.values())

    @app.get("/api/contacts", response_model=list[SponsorContact], tags=["reference"])
    def contacts() -> list[SponsorContact]:
        """Sponsor decision-makers and Newmark coverage, as a CRM lookup would return them."""
        return list(state.contacts.values())

    @app.get("/api/market", response_model=MarketAssumptions, tags=["reference"])
    def market() -> MarketAssumptions:
        return state.market

    @app.post("/api/analyze", response_model=PortfolioAnalysis, tags=["analysis"])
    def analyze(request: AnalyzeRequest) -> PortfolioAnalysis:
        """Run the portfolio through a scenario; returns summary, ranked call list and migration vs base."""
        scenario = _resolve_scenario(request.scenario, request.overrides)
        types = set(request.property_types) if request.property_types else None
        analysis = analyze_portfolio(state.loans, state.market, scenario, property_types=types)
        ranked = analysis.assessments
        if request.call_list_filter:
            ranked = filter_assessments(ranked, **request.call_list_filter.model_dump())
        return replace(
            analysis,
            assessments=ranked[: request.top_n],
            assessments_total=len(ranked),
            data_quality=state.ingest.data_quality(),
        )

    @app.get("/api/loans/{loan_id}", response_model=LoanAssessment, tags=["analysis"])
    def loan_detail(
        loan_id: str,
        scenario: Annotated[str, Query(description="Preset scenario name")] = "base",
    ) -> LoanAssessment:
        loan = next((x for x in state.loans if x.loan_id == loan_id), None)
        if loan is None:
            raise HTTPException(status_code=404, detail=f"loan '{loan_id}' not found")
        return assess_loan(loan, state.market, _resolve_scenario(scenario, None))

    @app.post("/api/tape/validate", response_model=TapeValidationResponse, tags=["ingestion"])
    def validate_tape(
        csv_text: Annotated[str, Body(media_type="text/csv", description="Loan tape as CSV text")],
    ) -> TapeValidationResponse:
        """Dry-run a servicer tape: reports row-level problems without loading it."""
        try:
            result = parse_tape_text(csv_text)
        except TapeFormatError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return TapeValidationResponse(data_quality=result.data_quality(), errors=result.errors[:200])

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def revalidate_dashboard_assets(request, call_next):
        # No build step means no hashed filenames, so ask browsers to revalidate via ETag.
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
