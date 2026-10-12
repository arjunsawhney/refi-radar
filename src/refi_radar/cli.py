"""Command-line interface: generate a synthetic tape, analyze a tape, or serve the API/dashboard."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from .contacts import default_contacts_path, load_contacts, write_contacts
from .ingest import TapeFormatError, load_tape, write_tape
from .market import load_market
from .models import PropertyType
from .portfolio import analyze_portfolio
from .report import to_markdown, write_call_list
from .scenarios import PRESETS, UnknownScenarioError, get_preset
from .synthetic import generate_contacts, generate_loans

DEFAULT_TAPE = Path("data/sample_loan_tape.csv")


def _cmd_generate(args: argparse.Namespace) -> int:
    loans = generate_loans(count=args.count, seed=args.seed)
    write_tape(loans, args.out)
    contacts_out = args.contacts_out or default_contacts_path(args.out)
    write_contacts(generate_contacts(seed=args.seed), contacts_out)
    print(f"Wrote {len(loans)} synthetic loans to {args.out} and sponsor contacts to {contacts_out}")
    return 0


def _cmd_scenarios(_: argparse.Namespace) -> int:
    for s in PRESETS.values():
        print(f"{s.name:22} {s.description}")
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    try:
        ingest = load_tape(args.tape)
        scenario = get_preset(args.scenario)
    except (FileNotFoundError, TapeFormatError, UnknownScenarioError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    overrides = {
        k: v
        for k, v in {
            "rate_shock_bps": args.rate_shock_bps,
            "cap_rate_shock_bps": args.cap_rate_shock_bps,
            "noi_haircut_pct": args.noi_haircut_pct,
            "office_vacancy_shock_pp": args.office_vacancy_shock_pp,
        }.items()
        if v is not None
    }
    if overrides:
        scenario = replace(
            scenario,
            name="custom",
            label="Custom Scenario",
            description=f"{scenario.label or scenario.name} with overrides {overrides}",
            **overrides,
        )

    if ingest.errors:
        print(f"warning: rejected {ingest.data_quality()['rows_rejected']} row(s):", file=sys.stderr)
        for e in ingest.errors[:20]:
            print(f"  row {e.row}, {e.field}: {e.message}", file=sys.stderr)

    types = {PropertyType(t) for t in args.property_type} if args.property_type else None
    analysis = analyze_portfolio(ingest.loans, load_market(args.market), scenario, property_types=types)

    contacts_path = args.contacts or default_contacts_path(args.tape)
    contacts = load_contacts(contacts_path) if Path(contacts_path).exists() else {}

    markdown = to_markdown(analysis, top_n=args.top, contacts=contacts)
    print(markdown)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"briefing_{scenario.name}.md").write_text(markdown, encoding="utf-8")
        write_call_list(analysis, out / f"call_list_{scenario.name}.csv", contacts=contacts)
        print(f"Saved briefing and call list to {out}/", file=sys.stderr)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    if args.tape:
        os.environ["REFI_RADAR_TAPE"] = str(args.tape)
    if args.market:
        os.environ["REFI_RADAR_MARKET"] = str(args.market)
    if args.contacts:
        os.environ["REFI_RADAR_CONTACTS"] = str(args.contacts)
    uvicorn.run("refi_radar.api:create_app", factory=True, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="refi-radar", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="write a deterministic synthetic loan tape")
    gen.add_argument("--count", type=int, default=250)
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--out", type=Path, default=DEFAULT_TAPE)
    gen.add_argument("--contacts-out", type=Path, help="defaults to crm_contacts.csv next to the tape")
    gen.set_defaults(func=_cmd_generate)

    sc = sub.add_parser("scenarios", help="list preset stress scenarios")
    sc.set_defaults(func=_cmd_scenarios)

    an = sub.add_parser("analyze", help="stress-test a loan tape and print a broker briefing")
    an.add_argument("--tape", type=Path, default=DEFAULT_TAPE)
    an.add_argument("--market", type=Path, default=None, help="market assumptions TOML (defaults to bundled)")
    an.add_argument("--contacts", type=Path, help="CRM contacts CSV (defaults to crm_contacts.csv next to the tape)")
    an.add_argument("--scenario", default="base", choices=sorted(PRESETS))
    an.add_argument("--rate-shock-bps", type=float)
    an.add_argument("--cap-rate-shock-bps", type=float)
    an.add_argument("--noi-haircut-pct", type=float, help="decimal, e.g. 0.05")
    an.add_argument("--office-vacancy-shock-pp", type=float, help="decimal occupancy points, e.g. 0.15")
    an.add_argument("--property-type", action="append", choices=[p.value for p in PropertyType])
    an.add_argument("--top", type=int, default=15)
    an.add_argument("--out", type=Path, help="directory for briefing .md and call list .csv")
    an.set_defaults(func=_cmd_analyze)

    sv = sub.add_parser("serve", help="run the API and dashboard")
    sv.add_argument("--tape", type=Path)
    sv.add_argument("--market", type=Path)
    sv.add_argument("--contacts", type=Path)
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
