"""Output adapters: JSON-safe dicts, Markdown briefing, and CSV call list."""

from __future__ import annotations

import csv
from dataclasses import fields, is_dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

from .contacts import SponsorContact
from .portfolio import PortfolioAnalysis

BUCKET_LABELS = {
    "refi_ready": "Ready to Refinance",
    "equity_gap": "Small Shortfall",
    "recap_required": "Needs New Capital",
    "distressed": "Distressed",
}


def to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {to_jsonable(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def _m(value: float) -> str:
    if abs(value) >= 1e9:
        return f"${value / 1e9:,.2f}B"
    return f"${value / 1e6:,.1f}M"


def to_markdown(
    analysis: PortfolioAnalysis, top_n: int = 15, contacts: dict[str, SponsorContact] | None = None
) -> str:
    contacts = contacts or {}
    s = analysis.summary
    sc = analysis.scenario
    lines = [
        f"# Refi Radar briefing: {sc.label or sc.name}",
        "",
        f"_As of {analysis.as_of.isoformat()}. {sc.description}_",
        "",
        "## Headline",
        "",
        f"- **{s.loans_in_window} loans / {_m(s.total_balance)}** mature inside the horizon "
        f"({s.loans_outside_window} loans mature later and are excluded).",
        f"- **{_m(s.at_risk_balance)} ({s.at_risk_pct:.0%})** needs a recap or is distressed.",
        f"- **Total refinance gap: {_m(s.total_refi_gap)}**, the equity the market must supply to clear these maturities.",
        f"- **Indicative advisory fee pool: {_m(s.indicative_fee_pool)}** (illustrative fee assumptions).",
    ]
    if analysis.comparison and analysis.comparison.loans_worsened:
        c = analysis.comparison
        lines.append(
            f"- **Versus {c.baseline}:** {c.loans_worsened} loans ({_m(c.balance_worsened)}) move to a worse bucket; "
            f"refi gap changes by {_m(c.refi_gap_delta)}."
        )

    lines += ["", "## By risk bucket", "", "| Bucket | Loans | Balance | Refi gap |", "|---|---:|---:|---:|"]
    for b in s.by_bucket:
        lines.append(f"| {BUCKET_LABELS[b.bucket.value]} | {b.loan_count} | {_m(b.balance)} | {_m(b.refi_gap)} |")

    lines += ["", "## By property type", "", "| Type | Loans | Balance | At-risk | Refi gap |", "|---|---:|---:|---:|---:|"]
    for t in s.by_property_type:
        pct = t.at_risk_balance / t.balance if t.balance else 0
        lines.append(f"| {t.property_type.value} | {t.loan_count} | {_m(t.balance)} | {pct:.0%} | {_m(t.refi_gap)} |")

    lines += [
        "",
        f"## Top {min(top_n, len(analysis.assessments))} broker call list",
        "",
        "| # | Priority | Property | Type | Due | Existing loan | Shortfall | Status | Next step | Call |",
        "|---:|---:|---|---|---|---:|---:|---|---|---|",
    ]
    for i, a in enumerate(analysis.assessments[:top_n], start=1):
        c = contacts.get(a.sponsor)
        call = f"{c.contact_name}, {a.sponsor} {c.phone}" if c else a.sponsor
        lines.append(
            f"| {i} | {a.priority.score:.0f} | {a.property_name} ({a.market}) | {a.property_type.value} | "
            f"{a.maturity_date.isoformat()} | {_m(a.balloon_balance)} | {_m(a.refi_gap)} | "
            f"{BUCKET_LABELS[a.bucket.value]} | {a.next_step} | {call} |"
        )
    lines.append("")
    return "\n".join(lines)


CALL_LIST_COLUMNS = (
    "rank", "priority_score", "loan_id", "property_name", "property_type", "market", "sponsor",
    "lender_type", "maturity_date", "months_to_maturity", "balloon_balance", "max_refi_loan",
    "refi_gap", "refi_gap_pct", "ltv_at_maturity", "in_place_dscr", "refi_dscr", "debt_yield",
    "binding_constraint", "bucket", "next_step", "recommended_action", "indicative_fee",
    "contact_name", "contact_title", "contact_phone", "contact_email", "coverage_broker",
)


def write_call_list(
    analysis: PortfolioAnalysis, path: str | Path, contacts: dict[str, SponsorContact] | None = None
) -> None:
    contacts = contacts or {}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CALL_LIST_COLUMNS)
        writer.writeheader()
        for rank, a in enumerate(analysis.assessments, start=1):
            row = {k: v for k, v in to_jsonable(a).items() if k in CALL_LIST_COLUMNS}
            c = contacts.get(a.sponsor)
            if c:
                row |= {
                    "contact_name": c.contact_name,
                    "contact_title": c.title,
                    "contact_phone": c.phone,
                    "contact_email": c.email,
                    "coverage_broker": c.coverage_broker,
                }
            writer.writerow({**row, "rank": rank, "priority_score": a.priority.score})
