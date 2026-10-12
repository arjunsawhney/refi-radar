"""Loan tape ingestion with row-level validation.

Real servicer tapes are messy ("$12,500,000", "4.5%", blank cells). We normalize what we safely
can and reject the rest with a row/field/message error instead of failing the whole file.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .models import Loan, PropertyType

REQUIRED_COLUMNS: tuple[str, ...] = (
    "loan_id",
    "property_name",
    "property_type",
    "market",
    "sponsor",
    "lender_type",
    "origination_date",
    "maturity_date",
    "original_balance",
    "note_rate",
    "amortization_years",
    "original_value",
    "current_noi",
    "occupancy",
)


class TapeFormatError(ValueError):
    """The file as a whole is unusable (e.g. missing required columns)."""


@dataclass(frozen=True, slots=True)
class RowError:
    row: int
    field: str
    message: str


@dataclass(slots=True)
class IngestResult:
    loans: list[Loan] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)

    @property
    def rows_read(self) -> int:
        return len(self.loans) + len({e.row for e in self.errors})

    def data_quality(self) -> dict[str, int]:
        return {
            "rows_read": self.rows_read,
            "rows_accepted": len(self.loans),
            "rows_rejected": len({e.row for e in self.errors}),
        }


def _money(raw: str) -> float:
    return float(raw.replace("$", "").replace(",", "").strip())


def _rate(raw: str) -> float:
    """Accepts 0.045, 4.5 or 4.5% and returns a decimal."""
    text = raw.strip()
    if text.endswith("%"):
        return float(text[:-1]) / 100
    value = float(text)
    return value / 100 if value >= 1 else value


def _date(raw: str) -> date:
    return date.fromisoformat(raw.strip())


def _occupancy(raw: str) -> float:
    """Accepts 0.92, 92 or 92% and returns a decimal; 1 means fully occupied, not 1%."""
    text = raw.strip()
    if text.endswith("%"):
        return float(text[:-1]) / 100
    value = float(text)
    return value / 100 if value > 1 else value


def _int(raw: str) -> int:
    return int(float(raw.strip()))


def _text(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise ValueError("is blank")
    return text


def _property_type(raw: str) -> PropertyType:
    try:
        return PropertyType(raw.strip().lower())
    except ValueError:
        raise ValueError(f"must be one of {[p.value for p in PropertyType]}") from None


PARSERS: dict[str, Callable[[str], Any]] = {
    "loan_id": _text,
    "property_name": _text,
    "property_type": _property_type,
    "market": _text,
    "sponsor": _text,
    "lender_type": _text,
    "origination_date": _date,
    "maturity_date": _date,
    "original_balance": _money,
    "note_rate": _rate,
    "amortization_years": _int,
    "original_value": _money,
    "current_noi": _money,
    "occupancy": _occupancy,
}


def _business_rules(values: dict[str, Any]) -> list[tuple[str, str]]:
    problems = []
    if values["maturity_date"] <= values["origination_date"]:
        problems.append(("maturity_date", "must be after origination_date"))
    if values["original_balance"] <= 0:
        problems.append(("original_balance", "must be positive"))
    if values["original_value"] <= 0:
        problems.append(("original_value", "must be positive"))
    if not 0 < values["note_rate"] < 0.25:
        problems.append(("note_rate", "must be between 0% and 25%"))
    if not 0 <= values["amortization_years"] <= 40:
        problems.append(("amortization_years", "must be between 0 (IO) and 40"))
    if not 0 < values["occupancy"] <= 1:
        problems.append(("occupancy", "must be between 0% and 100%"))
    return problems


def parse_rows(rows: csv.DictReader) -> IngestResult:
    missing = [c for c in REQUIRED_COLUMNS if c not in (rows.fieldnames or [])]
    if missing:
        raise TapeFormatError(f"missing required columns: {missing}")

    result = IngestResult()
    seen_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=2):  # row 1 is the header
        values: dict[str, Any] = {}
        row_errors: list[RowError] = []
        for column, parser in PARSERS.items():
            raw = row.get(column) or ""
            try:
                values[column] = parser(raw)
            except ValueError as exc:
                detail = str(exc) if str(exc).startswith(("is ", "must ")) else f"could not parse '{raw}'"
                row_errors.append(RowError(row_number, column, detail))
        if not row_errors:
            row_errors.extend(RowError(row_number, f, m) for f, m in _business_rules(values))
        if not row_errors and values["loan_id"] in seen_ids:
            row_errors.append(RowError(row_number, "loan_id", "duplicate loan_id"))

        if row_errors:
            result.errors.extend(row_errors)
            continue
        seen_ids.add(values["loan_id"])
        result.loans.append(Loan(**values))
    return result


def load_tape(path: str | Path) -> IngestResult:
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        return parse_rows(csv.DictReader(fh))


def parse_tape_text(text: str) -> IngestResult:
    return parse_rows(csv.DictReader(io.StringIO(text)))


def write_tape(loans: list[Loan], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        for loan in loans:
            writer.writerow(
                {
                    "loan_id": loan.loan_id,
                    "property_name": loan.property_name,
                    "property_type": loan.property_type.value,
                    "market": loan.market,
                    "sponsor": loan.sponsor,
                    "lender_type": loan.lender_type,
                    "origination_date": loan.origination_date.isoformat(),
                    "maturity_date": loan.maturity_date.isoformat(),
                    "original_balance": f"{loan.original_balance:.2f}",
                    "note_rate": f"{loan.note_rate:.5f}",
                    "amortization_years": loan.amortization_years,
                    "original_value": f"{loan.original_value:.2f}",
                    "current_noi": f"{loan.current_noi:.2f}",
                    "occupancy": f"{loan.occupancy:.4f}",
                }
            )
