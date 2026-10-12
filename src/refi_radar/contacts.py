"""Sponsor contacts, standing in for a CRM (e.g. Salesforce) lookup.

Servicer loan tapes rarely carry who to call. In production this module would query the CRM by sponsor;
here it reads a CSV so the join is explicit and swappable.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SponsorContact:
    sponsor: str
    contact_name: str
    title: str
    phone: str
    email: str
    coverage_broker: str
    """Newmark relationship owner for this sponsor."""


CONTACT_COLUMNS = tuple(f.name for f in fields(SponsorContact))


def load_contacts(path: str | Path) -> dict[str, SponsorContact]:
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in CONTACT_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"contacts file missing columns: {missing}")
        return {
            row["sponsor"].strip(): SponsorContact(**{c: row[c].strip() for c in CONTACT_COLUMNS})
            for row in reader
            if row.get("sponsor", "").strip()
        }


def write_contacts(contacts: list[SponsorContact], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CONTACT_COLUMNS)
        writer.writeheader()
        writer.writerows(asdict(c) for c in contacts)


def default_contacts_path(tape_path: str | Path) -> Path:
    """Convention: a CRM extract named crm_contacts.csv sitting next to the loan tape."""
    return Path(tape_path).with_name("crm_contacts.csv")
